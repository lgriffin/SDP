"""The production composition root: the kernel wired to real adapters from Settings (§4).

This is the only module that imports more than one adapter; the CLI and the API both start here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sdp.adapters.archives.dasch import DaschDialect, DaschGateway
from sdp.adapters.archives.dialect import YamlDialectCurator
from sdp.adapters.archives.mast import MastObservationGateway
from sdp.adapters.archives.simbad import SimbadResolver
from sdp.adapters.archives.tap import TapArchiveAdapter, load_sources
from sdp.adapters.archives.transport import CassetteTransport, PoliteTransport, Transport, UrllibTransport
from sdp.adapters.archives.untrusted import IsolatedVotableParser
from sdp.adapters.archives.xmatch import CdsXMatchAdapter
from sdp.adapters.compute.cpu import CpuExecutor
from sdp.adapters.imaging.photutils_detector import PhotutilsDetector
from sdp.adapters.imaging.tweakwcs_refiner import TweakwcsRefiner
from sdp.adapters.ledger.git import GitSignedLedger, load_signing_key
from sdp.adapters.logs import configure_logging
from sdp.adapters.postgres import PostgresDatabase
from sdp.adapters.publication.zenodo import ZenodoPublisher, creators_from
from sdp.adapters.settings import Settings
from sdp.adapters.storage.nas import NasArtifactStore
from sdp.adapters.system import SystemClock, SystemEnvironmentProbe, UlidGenerator
from sdp.adapters.telemetry.prometheus import PrometheusTextfileTracker
from sdp.kernel.application.investigate import InvestigatePorts, load_investigation_tests
from sdp.kernel.application.kernel import Kernel, KernelPorts
from sdp.kernel.application.plugins import EntryPointProtocolRegistry
from sdp.kernel.ports.archives import ArchiveGateway, DialectCurator
from sdp.kernel.ports.evidence import ExperimentTracker
from sdp.kernel.ports.investigate import ObservationArchiveGateway
from sdp.kernel.ports.plugins import ProtocolRegistry
from sdp.kernel.ports.publication import BundlePublisher, DoiRecord


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


def archive_gateways(settings: Settings) -> dict[str, ArchiveGateway]:
    """One TAP gateway per packaged source, each with its own rate limit (§8)."""
    gateways: dict[str, ArchiveGateway] = {}
    for source_id, source in load_sources().items():
        transport = archive_transport(settings, source_id, source.descriptor.min_seconds_between_calls)
        gateways[source_id] = TapArchiveAdapter({source_id: source}, transport, parser=IsolatedVotableParser())
    return gateways


def cross_match(settings: Settings) -> CdsXMatchAdapter:
    return CdsXMatchAdapter(archive_transport(settings, "cds-xmatch", 1.0))


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


def observation_gateways(settings: Settings) -> dict[str, ObservationArchiveGateway]:
    """MAST (every mission, through ObsCore) and DASCH, each on its own polite transport (Investigate §4)."""
    return {
        "mast": MastObservationGateway(archive_transport(settings, "mast", 1.0)),
        "dasch": DaschGateway(archive_transport(settings, "dasch", 1.0)),
    }


def investigate_ports(settings: Settings, services: Mapping[str, Any]) -> InvestigatePorts:
    """Investigate wired to the ecosystem adapters (Investigate §7): the tests installed under the
    ``sdp.investigation_tests`` entry point, SIMBAD, MAST and DASCH with the DASCH product dialect,
    photutils and tweakwcs. Kernel services a test needs (``clusterer``) come from ``services``."""
    return InvestigatePorts(
        tests=load_investigation_tests(),
        cross_match=cross_match(settings),
        observation_gateways=observation_gateways(settings),
        detector=PhotutilsDetector(),
        refiner=TweakwcsRefiner(),
        resolver=SimbadResolver(archive_transport(settings, "simbad", 1.0)),
        services=dict(services),
        product_dialects={"dasch": DaschDialect()},
    )


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
        gateways={**archive_gateways(settings), **overrides.gateways},
        services=dict(overrides.services),
        min_free_bytes=settings.min_free_bytes,
        investigate=overrides.investigate or investigate_ports(settings, overrides.services),
    )
    return Application(settings, Kernel.build(ports), database, store, ledger)
