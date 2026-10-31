"""The production composition root: the kernel wired to real adapters from Settings (§4).

This is the only module that imports more than one adapter; the CLI and the API both start here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from logging import getLogger
from pathlib import Path
from typing import Any

from sdp.adapters.archives import simbad
from sdp.adapters.archives.benchmarks import VIZIER_MIRRORS, VizierKnownClusters
from sdp.adapters.archives.bulk import BulkCatalogueGateway
from sdp.adapters.archives.capabilities import VoCapabilityDiscovery
from sdp.adapters.archives.dasch import DaschDialect, DaschGateway
from sdp.adapters.archives.dialect import YamlDialectCurator
from sdp.adapters.archives.gaia_datalink import GaiaDataLinkGateway, GaiaDataLinkInventory
from sdp.adapters.archives.irsa_wise import IrsaWiseGateway, IrsaWiseInventory
from sdp.adapters.archives.mast import MastObservationGateway
from sdp.adapters.archives.mast_inventory import MastProductInventory
from sdp.adapters.archives.references import TAP_CATALOGUES, VIZIER, TapConeCatalogue, VsxCatalogue
from sdp.adapters.archives.simbad import SimbadResolver
from sdp.adapters.archives.tap import TapArchiveAdapter, load_sources
from sdp.adapters.archives.transport import (
    CassetteTransport,
    MirrorTransport,
    PoliteTransport,
    Transport,
    UrllibTransport,
)
from sdp.adapters.archives.untrusted import IsolatedVotableParser
from sdp.adapters.archives.xmatch import CdsXMatchAdapter, FallbackCrossMatch, LocalCrossMatch
from sdp.adapters.association import BayesFactorAssociation, LikelihoodRatioAssociation
from sdp.adapters.compute.clustering import FastHdbscan, SklearnDbscan, SklearnHdbscan
from sdp.adapters.compute.cpu import CpuExecutor
from sdp.adapters.imaging.photutils_detector import PhotutilsDetector
from sdp.adapters.imaging.tweakwcs_refiner import TweakwcsRefiner
from sdp.adapters.ledger.git import GitSignedLedger, load_signing_key
from sdp.adapters.local.catalogue import LocalDiscovery
from sdp.adapters.local.gateway import LocalFrameDialect, LocalImageGateway
from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.sources import LocalSourceError, LocalSources, data_dir
from sdp.adapters.logs import configure_logging
from sdp.adapters.postgres import PostgresDatabase
from sdp.adapters.postgres.capabilities import PostgresCapabilityRegistry
from sdp.adapters.products import EnvelopeValidator, GaiaDataLinkDialect, JwstDialect, WiseLightCurveDialect
from sdp.adapters.products.preview import preview_png
from sdp.adapters.publication.zenodo import ZenodoPublisher, creators_from
from sdp.adapters.settings import Settings
from sdp.adapters.storage.nas import NasArtifactStore
from sdp.adapters.system import SystemClock, SystemEnvironmentProbe, UlidGenerator
from sdp.adapters.telemetry.prometheus import PrometheusTextfileTracker
from sdp.kernel.application.investigate import (
    InvestigatePorts,
    ProductEnvelopes,
    load_candidate_rankers,
    load_investigation_tests,
)
from sdp.kernel.application.kernel import Kernel, KernelPorts
from sdp.kernel.application.plugins import EntryPointProtocolRegistry
from sdp.kernel.domain.integration import AcquisitionBudget
from sdp.kernel.ports.archives import ArchiveGateway, CrossMatchService, DialectCurator
from sdp.kernel.ports.evidence import ExperimentTracker
from sdp.kernel.ports.investigate import ObservationArchiveGateway
from sdp.kernel.ports.plugins import ProtocolRegistry
from sdp.kernel.ports.publication import BundlePublisher, DoiRecord
from sdp.kernel.ports.references import ReferenceCatalogue
from sdp.kernel.ports.storage import ArtifactStore
from sdp.kernel.science.investigation import InvestigationTestRegistry


class NotConfigured(RuntimeError):
    pass


class UnconfiguredPublisher:
    """Stands in until a DOI publisher is configured; refuses rather than pretending to publish."""

    def publish(self, bundle: bytes, filename: str, metadata: Mapping[str, Any]) -> DoiRecord:
        raise NotConfigured("no bundle publisher is configured for this deployment")


class _NullTracker:
    def log_params(self, experiment_id: str, params: Mapping[str, Any]) -> None:
        return None

    def log_metrics(self, experiment_id: str, metrics: Mapping[str, float]) -> None:
        return None

    def set_tags(self, experiment_id: str, tags: Mapping[str, str]) -> None:
        return None


@dataclass
class Overrides:
    """Ports a deployment supplies beyond the defaults (archive gateways, curators, publishers)."""

    gateways: Mapping[str, ArchiveGateway] = field(default_factory=dict)
    curator: DialectCurator | None = None
    publisher: BundlePublisher | None = None
    protocols: ProtocolRegistry | None = None
    services: Mapping[str, Any] = field(default_factory=dict)
    investigate: InvestigatePorts | None = None


@dataclass
class Application:
    settings: Settings
    kernel: Kernel
    database: PostgresDatabase
    store: NasArtifactStore
    ledger: GitSignedLedger

    @property
    def budget(self) -> AcquisitionBudget:
        return self.settings.plan_budget


def build_ledger(settings: Settings) -> GitSignedLedger:
    return GitSignedLedger(
        root=settings.ledger_dir,
        clock=SystemClock(),
        signing_key=load_signing_key(settings.signing_key) if settings.signing_key else None,
        allowed_signers=settings.allowed_signers,
        commit=settings.ledger_commit,
        require_signatures=settings.require_signatures,
    )


def archive_transport(settings: Settings, name: str, min_seconds_between_calls: float) -> Transport:
    """The transport one archive adapter gets: live and polite, or cassettes under ``<cassette_dir>/<name>``."""
    live = PoliteTransport(UrllibTransport(), min_seconds_between_calls=min_seconds_between_calls)
    if settings.archive_mode == "live":
        return live
    if settings.cassette_dir is None:
        raise NotConfigured("SDP_ARCHIVE_MODE replay and record need SDP_CASSETTE_DIR")
    directory = settings.cassette_dir / name
    if settings.archive_mode == "replay":
        return CassetteTransport(directory, "replay")
    return CassetteTransport(directory, "record", inner=live)


def simbad_transport(settings: Settings) -> Transport:
    """SIMBAD's transport: Strasbourg first, then CDS's Harvard mirrors unless SDP_ARCHIVE_MIRRORS=0."""
    transport = archive_transport(settings, "simbad", 1.0)
    return MirrorTransport(transport, simbad.MIRRORS) if settings.archive_mirrors else transport


def reference_transport(settings: Settings, name: str) -> Transport:
    """A reference pull's transport: cassettes under ``<cassette_dir>/references/<name>`` in replay and record,
    else live with one retry and a 90 s wait, so a catalogue that is down costs the scientist minutes at
    most, not the half hour a bulk acquisition's backoff allows."""
    live = PoliteTransport(UrllibTransport(timeout_s=90.0), min_seconds_between_calls=1.0, retries=1)
    if settings.archive_mode == "live":
        return live
    if settings.cassette_dir is None:
        raise NotConfigured("SDP_ARCHIVE_MODE replay and record need SDP_CASSETTE_DIR")
    directory = settings.cassette_dir / "references" / name
    if settings.archive_mode == "replay":
        return CassetteTransport(directory, "replay")
    return CassetteTransport(directory, "record", inner=live)


def reference_catalogues(settings: Settings) -> dict[str, ReferenceCatalogue]:
    """The catalogues a candidate can be compared against (INV-024). SIMBAD fails over to CDS's Harvard copy
    and VSX to VizieR's mirrors, unless SDP_ARCHIVE_MIRRORS=0, so a CDS outage leaves the comparison working."""
    out: dict[str, ReferenceCatalogue] = {}
    for source_id, spec in TAP_CATALOGUES.items():
        transport = reference_transport(settings, source_id)
        if source_id == "simbad" and settings.archive_mirrors:
            transport = MirrorTransport(transport, simbad.MIRRORS)
        out[source_id] = TapConeCatalogue(spec, transport)
    vizier = reference_transport(settings, "vsx")
    if settings.archive_mirrors:
        vizier = MirrorTransport(vizier, {VIZIER: tuple(m for m in VIZIER_MIRRORS if m != VIZIER)})
    out["vsx"] = VsxCatalogue(vizier)
    return out


def archive_gateways(settings: Settings, store: ArtifactStore | None = None) -> dict[str, ArchiveGateway]:
    """One TAP gateway per packaged source, each with its own rate limit (§8). Gaia is read from the
    store's bulk copy instead when ``SDP_GAIA_SOURCE`` is bulk, or auto and the copy is complete (#72)."""
    gateways: dict[str, ArchiveGateway] = {}
    for source_id, source in load_sources().items():
        transport = archive_transport(settings, source_id, source.descriptor.min_seconds_between_calls)
        gateways[source_id] = TapArchiveAdapter({source_id: source}, transport, parser=IsolatedVotableParser())
    if store is not None and settings.gaia_source != "tap":
        gaia = load_sources()["gaia"]
        bulk = BulkCatalogueGateway(store, gaia.descriptor, gaia.ra_column, gaia.dec_column, gaia.key_column)
        if settings.gaia_source == "bulk" or bulk.complete():
            gateways["gaia"] = bulk
    return gateways


def cds_cross_match(settings: Settings) -> CdsXMatchAdapter:
    return CdsXMatchAdapter(archive_transport(settings, "cds-xmatch", 1.0))


def cross_match(settings: Settings) -> CrossMatchService:
    """Cross-matching per ``SDP_CROSS_MATCH``: on this machine by default, so a CDS outage never stops a
    run (DSI-018); CDS X-Match only when asked, and it is a gate input only once DSI-009 holds."""
    if settings.cross_match == "cds" or (settings.cross_match == "auto" and settings.archive_mode == "replay"):
        # a replay answers from recordings or fails: a missing or corrupt cassette is never an outage
        return cds_cross_match(settings)
    if settings.cross_match == "auto":
        return FallbackCrossMatch(cds_cross_match(settings), LocalCrossMatch())
    return LocalCrossMatch()


def known_clusters(settings: Settings) -> VizierKnownClusters:
    """Catalogues of known clusters from VizieR, for the UC-01 benchmark cross-match (DSI-015)."""
    mirrors = VIZIER_MIRRORS if settings.archive_mirrors else ()
    return VizierKnownClusters(archive_transport(settings, "vizier", 1.0), mirrors=mirrors)


def publisher(settings: Settings) -> BundlePublisher:
    """Zenodo when a token is configured; otherwise a publisher that refuses."""
    if not settings.zenodo_token:
        return UnconfiguredPublisher()
    return ZenodoPublisher(
        settings.zenodo_token,
        creators_from(settings.zenodo_creators),
        base_url=settings.zenodo_url,
        community=settings.zenodo_community,
    )


def clusterers() -> dict[str, Any]:
    """The CPU ClusterSearchAlgorithms by algorithm id: the UC-01 references and the sweep's Boruvka HDBSCAN;
    GPU ones join only after agreement."""
    return {c.algorithm_id: c for c in (SklearnHdbscan(), SklearnDbscan(), FastHdbscan())}


def observation_gateways(
    settings: Settings, mast: Transport | None = None, gaia: Transport | None = None, irsa: Transport | None = None
) -> dict[str, ObservationArchiveGateway]:
    """MAST (every mission, through ObsCore), DASCH, Gaia's DataLink products and IRSA's WISE light
    curves, each on its own polite transport (Investigate §4)."""
    return {
        "mast": MastObservationGateway(mast or archive_transport(settings, "mast", 1.0)),
        "dasch": DaschGateway(archive_transport(settings, "dasch", 1.0)),
        "gaia-datalink": GaiaDataLinkGateway(gaia or archive_transport(settings, "gaia-datalink", 1.0)),
        "irsa-wise": IrsaWiseGateway(irsa or archive_transport(settings, "irsa-wise", 1.0)),
    }


def local_image_gateways(settings: Settings) -> dict[str, LocalImageGateway]:
    """One gateway per local source that has been indexed (#71), keyed ``local:<name>``, so a profile
    names the person's own frames as it names an archive. SDP's data folder is ``SDP_DATA`` or the one
    holding the artifact store; with no sources there are no gateways."""
    try:
        data = data_dir()
    except LocalSourceError:
        data = settings.artifact_root.parent
    try:
        sources = LocalSources(data).all()
        return {s.source_id: LocalImageGateway(s, data) for s in sources.values() if FrameIndex(s, data).path.exists()}
    except Exception as exc:  # an unreadable or hand-broken registry never stops the archives' runs
        getLogger("sdp.adapters.bootstrap").warning("local sources not read", extra={"error": str(exc)})
        return {}


#: The adapter name and version MAST products are admitted under (INT-016): a compatibility record
#: for ``mast-observations`` at this version and the product type admits them.
MAST_ADAPTER = ("mast-observations", "1.0.0")
#: The adapter Gaia's DataLink products (epoch photometry, BP/RP spectra) are admitted under.
GAIA_DATALINK_ADAPTER = ("gaia-datalink", "1.0.0")
#: The adapter WISE and NEOWISE light curves from IRSA are admitted under.
IRSA_WISE_ADAPTER = ("irsa-wise", "1.0.0")


def product_envelopes(mast: Transport, gaia: Transport, irsa: Transport) -> dict[str, ProductEnvelopes]:
    """JWST products from MAST, Gaia's DataLink products and IRSA's WISE light curves are normalised by their dialects,
    validated by the same validator, and curated only when both pass (Integration v0.4.0 §3); MAST's
    other missions keep the ordinary product path. Each inventory shares its gateway's polite
    transport."""
    adapter, version = MAST_ADAPTER
    gaia_adapter, gaia_version = GAIA_DATALINK_ADAPTER
    wise_adapter, wise_version = IRSA_WISE_ADAPTER
    return {
        "gaia-datalink": ProductEnvelopes(
            gaia_adapter, gaia_version, GaiaDataLinkDialect(), EnvelopeValidator(), GaiaDataLinkInventory(gaia)
        ),
        "irsa-wise": ProductEnvelopes(
            wise_adapter, wise_version, WiseLightCurveDialect(), EnvelopeValidator(), IrsaWiseInventory(irsa)
        ),
        "mast": ProductEnvelopes(
            adapter,
            version,
            JwstDialect(),
            EnvelopeValidator(),
            MastProductInventory(mast),
            missions=frozenset({"JWST"}),
        ),
    }


def investigation_services(detector: PhotutilsDetector, refiner: TweakwcsRefiner) -> dict[str, Any]:
    """The services the installed investigation tests call: UC-01's CPU reference HDBSCAN, the other
    CPU clusterers a profile may name instead, the image tests' detector, both association methods
    (v0.4.0 §7, ADR-019) and the optional alignment to Gaia. Reproducing a dossier needs these and
    nothing else."""
    return {
        "clusterer": SklearnHdbscan(),
        "clusterers": clusterers(),
        "detector": detector,
        "associators": (BayesFactorAssociation(), LikelihoodRatioAssociation()),
        "refiner": refiner,
    }


def reproduction_tools() -> tuple[CpuExecutor, InvestigationTestRegistry, dict[str, Any]]:
    """What re-running an evidence dossier needs (INV-022), and nothing more: the installed tests and
    the services they call, with no database, archive or settings."""
    return CpuExecutor(), load_investigation_tests(), investigation_services(PhotutilsDetector(), TweakwcsRefiner())


def investigate_ports(settings: Settings, services: Mapping[str, Any]) -> InvestigatePorts:
    """Investigate wired to the ecosystem adapters (Investigate §7): the tests installed under the
    ``sdp.investigation_tests`` entry point, SIMBAD, MAST (JWST products enveloped), Gaia's DataLink
    products and IRSA's WISE light curves (enveloped) and DASCH with the
    DASCH product dialect, the person's own solved frames (``local:<name>``),
    photutils and tweakwcs, and the installed candidate rankers (the stellar module's under the
    ``stellar`` policy). The ``clusterer`` a structure test uses is UC-01's CPU reference HDBSCAN
    unless ``services`` names another."""
    mast = archive_transport(settings, "mast", 1.0)
    gaia = archive_transport(settings, "gaia-datalink", 1.0)
    irsa = archive_transport(settings, "irsa-wise", 1.0)
    detector = PhotutilsDetector()
    refiner = TweakwcsRefiner()
    local = local_image_gateways(settings)
    return InvestigatePorts(
        tests=load_investigation_tests(),
        cross_match=cross_match(settings),
        observation_gateways={**observation_gateways(settings, mast, gaia, irsa), **local},
        detector=detector,
        refiner=refiner,
        resolver=SimbadResolver(simbad_transport(settings)),
        services={**investigation_services(detector, refiner), **services},
        product_dialects={"dasch": DaschDialect(), **{k: LocalFrameDialect(g) for k, g in local.items()}},
        rankers=load_candidate_rankers(),
        product_envelopes=product_envelopes(mast, gaia, irsa),
        picture=preview_png,
    )


def capability_discovery(settings: Settings) -> VoCapabilityDiscovery:
    """What plans are matched against: the registered descriptors (migration 0003). Nothing is read
    from the VO while planning; descriptors are registered by ``sdp integration`` and the weekly job."""
    return VoCapabilityDiscovery(
        archive_transport(settings, "regtap", 1.0), PostgresCapabilityRegistry(settings.database_url)
    )


def with_local_sources(discovery: Any, local: Mapping[str, LocalImageGateway]) -> Any:
    """The planner's discovery, with each indexed local source described from its catalogue (#71)."""
    if not local:
        return discovery
    return LocalDiscovery(discovery, {k: (g.source, g.data) for k, g in local.items()})


def artifact_store(root: Path) -> NasArtifactStore:
    """The artifact store alone, for commands that need no database (``sdp archive bulk``)."""
    return NasArtifactStore(root)


def build_application(settings: Settings, overrides: Overrides | None = None, *, logging: bool = True) -> Application:
    overrides = overrides or Overrides()
    if logging:
        configure_logging(settings.log_level)
    database = PostgresDatabase(settings.database_url)
    store = NasArtifactStore(settings.artifact_root)
    ledger = build_ledger(settings)
    tracker: ExperimentTracker = (
        PrometheusTextfileTracker(settings.metrics_dir) if settings.metrics_dir else _NullTracker()
    )
    ports = KernelPorts(
        uow=database.unit_of_work,
        store=store,
        ledger=ledger,
        protocols=overrides.protocols or EntryPointProtocolRegistry(),
        executor=CpuExecutor(threads=settings.threads),
        clock=SystemClock(),
        ids=UlidGenerator(),
        environment=SystemEnvironmentProbe(repo=settings.repo_root),
        tracker=tracker,
        publisher=overrides.publisher or publisher(settings),
        curator=overrides.curator or YamlDialectCurator.packaged(),
        gateways={**archive_gateways(settings, store), **overrides.gateways},
        services={"clusterers": clusterers(), **overrides.services},
        min_free_bytes=settings.min_free_bytes,
        investigate=overrides.investigate or investigate_ports(settings, overrides.services),
        discovery=with_local_sources(capability_discovery(settings), local_image_gateways(settings)),
        references=reference_catalogues(settings),
    )
    return Application(settings, Kernel.build(ports), database, store, ledger)


def store_usage(root: Path, min_free_bytes: int) -> dict[str, Any]:
    """``sdp artifact usage``: the store's size and the free space against the DSI-010 floor (#60)."""
    usage = NasArtifactStore(root).usage()
    free = usage["free_bytes"]
    return {
        **usage,
        "min_free_bytes": min_free_bytes,
        "headroom_bytes": free - min_free_bytes if free is not None else None,
        "acquisition_allowed": free is None or free >= min_free_bytes,
    }
