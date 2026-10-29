"""Investigate ports (Investigate §3–§8, spec §6).

Driving: InvestigateField, TriageInvestigation, QueryInvestigations.
Driven: ObservationArchiveGateway, SourceDetector, WcsRefiner, ObjectResolver, SourceRegistry,
CandidateRanker and InvestigationRepository. Every ecosystem package (astroquery, photutils,
tweakwcs, pyvo, daschlab, astrocut) is bound once, behind one of these, with a fake and a
contract suite (Investigate §8).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import astropy.units as u
import numpy as np

from sdp.kernel.domain.integration import PlanGate
from sdp.kernel.domain.investigation import (
    EvidenceBundle,
    FetchedProduct,
    Field,
    Investigation,
    InvestigationSet,
    ObservationCriteria,
    ObservationIndex,
    ProductRef,
    RankedCandidate,
    RankingPolicy,
    ResolverRecord,
    TestProfile,
    TestResult,
)

# ---------------------------------------------------------------------------------------------
# Driven ports


@dataclass(frozen=True)
class ObservationTarget:
    """What an observation search is about: one object at a position, or the whole field.

    An object Identify read from Gaia carries its proper motion and the epoch of its position, so a
    gateway whose own catalogue has another epoch can follow a fast-moving star to it.
    """

    key: str
    ra_deg: float
    dec_deg: float
    pm_ra_cosdec_masyr: float | None = None
    pm_dec_masyr: float | None = None
    epoch_jyear: float | None = None

    @property
    def moving(self) -> bool:
        return None not in (self.pm_ra_cosdec_masyr, self.pm_dec_masyr, self.epoch_jyear)


class ObservationArchiveGateway(Protocol):
    """Observations, where the unit is a product rather than a row (Investigate §4).

    Catalogue rows keep coming through ArchiveGateway. The doc's ``fetch`` and ``cutout`` return a
    DatasetArtifact; here they return the bytes as read and the kernel registers the artifact,
    so registration stays in one transaction with its events (recorded in spec-deviations).
    """

    @property
    def archive_id(self) -> str: ...

    def search(self, target: ObservationTarget, criteria: ObservationCriteria) -> ObservationIndex:
        """Metadata only: mission, instrument, epoch, product type, size, licence, cloud URI if any."""
        ...

    def fetch(self, product: ProductRef) -> FetchedProduct:
        """Bytes from the archive, a cloud URI or DASCH; the caller checksums what was actually read."""
        ...

    def cutout(self, field: Field, size: u.Quantity) -> FetchedProduct:
        """A cutout of the field (astrocut for MAST, the DR7 cutout endpoint for DASCH)."""
        ...


@runtime_checkable
class ProductCutouts(Protocol):
    """A gateway that can cut one listed product down to a square around a position (INT-004).

    Optional: Retrieve fetches whole products from a gateway without it. The cutout's product id names
    the product it was cut from, so its ``obs_id`` and file stay readable.
    """

    def cutout_product(self, product: ProductRef, ra_deg: float, dec_deg: float, side_arcsec: float) -> FetchedProduct:
        """The product's pixels within ``side_arcsec`` of the position, as a FITS file of their own."""
        ...


def cutout_product_id(product_id: str, ra_deg: float, dec_deg: float, side_arcsec: float) -> str:
    """The id of a cutout: the product it comes from, then where and how big."""
    return f"{product_id}@cutout({ra_deg:.6f},{dec_deg:.6f},{side_arcsec:g}arcsec)"


@dataclass(frozen=True)
class ImageFrame:
    """An image handed to a detector: its bytes (FITS or ASDF) and media type, never a path."""

    artifact_id: str
    data: bytes
    media_type: str
    wcs: bytes | None = None


@dataclass(frozen=True)
class Detection:
    x: float
    y: float
    ra_deg: float
    dec_deg: float
    flux: float
    peak: float | None = None


@dataclass(frozen=True)
class DetectionList:
    """Detections with the WCS (as FITS header text) their sky positions were computed with."""

    detections: tuple[Detection, ...]
    wcs_header: str
    background: float
    background_rms: float
    detector_id: str
    params: Mapping[str, Any] = field(default_factory=dict)
    epoch_mjd: float | None = None
    """Mid-exposure time from the image header, when it names one (v0.4.0 §7)."""
    instrument: str = ""


class SourceDetector(Protocol):
    """Background estimation and detection (photutils in production, PlantedDetector in tests)."""

    @property
    def detector_id(self) -> str: ...

    def detect(self, frame: ImageFrame, params: Mapping[str, Any]) -> DetectionList: ...


@dataclass(frozen=True)
class RefinedWcs:
    wcs_header: str
    rms_arcsec: float
    matches: int
    refiner_id: str
    detections: tuple[Detection, ...]
    """The detections again, with sky positions recomputed under the refined WCS."""


class WcsRefiner(Protocol):
    """WCS refinement against a reference catalogue (tweakwcs in production)."""

    @property
    def refiner_id(self) -> str: ...

    def refine(
        self, detections: DetectionList, reference_ra: np.ndarray, reference_dec: np.ndarray, params: Mapping[str, Any]
    ) -> RefinedWcs: ...


class ObjectResolver(Protocol):
    """Known objects: SIMBAD by name or position, so a rediscovery is recognised at stage one."""

    @property
    def resolver_id(self) -> str: ...

    @property
    def release_label(self) -> str: ...

    def by_position(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> ResolverRecord | None: ...

    def by_name(self, name: str) -> ResolverRecord | None: ...


@runtime_checkable
class FieldResolver(Protocol):
    """An ObjectResolver that can resolve a whole field of positions at once (#65).

    Optional: Identify asks ``by_position`` star by star of a resolver without it. Each answer is what
    ``by_position`` would give for that position and radius.
    """

    def resolve_positions(
        self, positions: Sequence[tuple[float, float]], radius_arcsec: float
    ) -> Sequence[ResolverRecord | None]: ...


@dataclass(frozen=True)
class ServiceRecord:
    """A VO service that covers a field: the registry's "find" (pyvo)."""

    ivoid: str
    title: str
    standard: str
    access_url: str
    waveband: tuple[str, ...] = ()


class SourceRegistry(Protocol):
    """Which services cover a field, by standard (TAP, SIA, SSA, cone search)."""

    def services(self, field: Field, standard: str) -> Sequence[ServiceRecord]: ...


class CandidateRanker(Protocol):
    """Scores subjects from their test results under a versioned ranking policy."""

    @property
    def ranker_id(self) -> str: ...

    def rank(self, results: Sequence[TestResult], policy: RankingPolicy) -> Sequence[RankedCandidate]: ...


class InvestigationRepository(Protocol):
    """Investigation sets and investigations as documents. Nothing is deleted (INV-010)."""

    def save_set(self, investigation_set: InvestigationSet) -> None: ...

    def get_set(self, set_id: str) -> InvestigationSet | None: ...

    def list_sets(self) -> Sequence[InvestigationSet]: ...

    def save(self, investigation: Investigation) -> None: ...

    def get(self, investigation_id: str) -> Investigation | None: ...

    def for_set(self, set_id: str) -> Sequence[Investigation]: ...


@dataclass(frozen=True)
class CuratedProduct:
    """A product in one of the canonical formats tests read (``sdp.kernel.science.products``)."""

    data: bytes
    media_type: str


class ProductDialect(Protocol):
    """Turns an archive's native product into a canonical one, as a catalogue dialect turns native
    rows into canonical astrometry (DSI-005). The kernel registers the result as a curated child of
    the raw product, naming the dialect and its version."""

    @property
    def dialect_id(self) -> str: ...

    @property
    def dialect_version(self) -> str: ...

    def curate(self, product_type: str, raw: FetchedProduct) -> CuratedProduct | None:
        """The canonical form for ``product_type``, or None when this dialect does not curate it
        (the raw product is then what the test reads)."""
        ...


# ---------------------------------------------------------------------------------------------
# Driving ports


@dataclass(frozen=True)
class InvestigateFieldCommand:
    field: Field
    profile: TestProfile
    seed: int = 0
    actor: str = "kernel"
    set_id: str | None = None
    """Resume an existing set: completed stages are read back, not recomputed."""
    gate: PlanGate | None = None
    """When the run executes an approved plan, every acquisition goes through it (INT-015)."""


@dataclass(frozen=True)
class InvestigationRun:
    investigation_set: InvestigationSet
    investigations: tuple[Investigation, ...]
    bundles: tuple[EvidenceBundle, ...]
    manifest: Mapping[str, Any]


class InvestigateField(Protocol):
    def execute(self, command: InvestigateFieldCommand) -> InvestigationRun: ...


@dataclass(frozen=True)
class TriageCommand:
    investigation_id: str
    action: str
    """park, reopen, close, annotate or promote."""
    actor: str
    reason: str = ""
    predeclaration_hash: str | None = None
    experiment_id: str | None = None


class TriageInvestigation(Protocol):
    def execute(self, command: TriageCommand) -> Investigation: ...


class QueryInvestigations(Protocol):
    def sets(self) -> Sequence[InvestigationSet]: ...

    def set(self, set_id: str) -> InvestigationSet | None: ...

    def investigations(self, set_id: str) -> Sequence[Investigation]: ...

    def investigation(self, investigation_id: str) -> Investigation | None: ...

    def manifest(self, set_id: str) -> dict[str, Any] | None: ...

    def dossier(self, investigation_id: str) -> dict[str, Any] | None: ...
