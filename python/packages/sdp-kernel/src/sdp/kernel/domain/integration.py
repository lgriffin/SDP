"""Integration value objects (Integration Architecture v0.4.0 §2, §3, §5, §6, §9).

Capability descriptors and matches, the ObsCore-shaped product envelope, evidence requirements,
acquisition plans with budgets and approvals, and adapter compatibility records. Each invariant
v0.4.0 §9 lists is enforced here and has a test named after it:

- a descriptor claim for a product type cannot stand without a passing conformance suite (INT-002);
- a curated product cannot exist without its raw parent and a passing semantic report (INT-007);
- an acquisition cannot run without a plan item that names it and an approval that covers it
  (INT-014, INT-015);
- a verdict cannot be issued when an adapter used lacks a passing compatibility record (INT-016).

The association invariant (no ``accepted`` without a ledger entry, INT-011) lives in
``sdp.kernel.domain.association``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import date
from enum import StrEnum
from typing import Any

from sdp.kernel.domain.archive import Box, Cone
from sdp.kernel.domain.canonical import hash_document
from sdp.kernel.domain.errors import CurationBlocked, InvariantViolation
from sdp.kernel.domain.ids import check_semver
from sdp.kernel.domain.investigation import region_contains

SCHEMA_VERSION = "1.0.0"


class IntegrationRefused(InvariantViolation):
    """An integration rule refused an action: an unplanned acquisition, an unapproved plan, an
    unadmitted adapter or an unproven capability claim (INT-002, INT-014 to INT-016)."""


# ---------------------------------------------------------------------------------------------
# Vocabularies


class ProductType(StrEnum):
    """ObsCore ``dataproduct_type`` plus the non-science kinds a product listing also returns."""

    IMAGE = "image"
    SPECTRUM = "spectrum"
    CUBE = "cube"
    TIMESERIES = "timeseries"
    MEASUREMENTS = "measurements"
    CATALOGUE = "catalogue"
    PREVIEW = "preview"
    THUMBNAIL = "thumbnail"
    AUXILIARY = "auxiliary"

    @property
    def is_science(self) -> bool:
        return self not in NON_SCIENCE


NON_SCIENCE = frozenset({ProductType.PREVIEW, ProductType.THUMBNAIL, ProductType.AUXILIARY})


class Operation(StrEnum):
    CONE = "cone"
    REGION = "region"
    CRITERIA = "criteria"
    PRODUCT_LISTING = "product_listing"
    DOWNLOAD = "download"
    CUTOUT = "cutout"


class AccessStatus(StrEnum):
    PUBLIC = "public"
    EXCLUSIVE_ACCESS = "exclusive_access"
    LOGIN = "login"


class MatchStatus(StrEnum):
    SATISFIED = "satisfied"
    PARTIAL = "partial"
    UNSATISFIED = "unsatisfied"


class RelationshipSemantics(StrEnum):
    """DataLink ``semantics`` vocabulary terms SDP keeps (v0.4.0 §3)."""

    THIS = "#this"
    PROGENITOR = "#progenitor"
    AUXILIARY = "#auxiliary"
    PREVIEW = "#preview"
    CALIBRATION = "#calibration"
    DERIVATION = "#derivation"


class ConformanceKind(StrEnum):
    """The four kinds an adapter passes per product type before a gate admits it (v0.4.0 §6)."""

    CONTRACT = "contract"
    OPERATIONAL = "operational"
    SEMANTIC = "semantic"
    SCIENTIFIC = "scientific"


def _jsonable(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | frozenset | set):
        items = [_jsonable(v) for v in value]
        return sorted(items) if isinstance(value, frozenset | set) else items
    return value


def _range(low: float | None, high: float | None, what: str) -> tuple[float | None, float | None]:
    for v in (low, high):
        if v is not None and not math.isfinite(v):
            raise InvariantViolation(f"{what} bounds must be finite or unknown")
    if low is not None and high is not None and low > high:
        raise InvariantViolation(f"{what} lower bound exceeds upper bound")
    return low, high


def _overlaps(a: tuple[float | None, float | None], b: tuple[float | None, float | None]) -> bool | None:
    """True, False, or None when either side is unknown."""
    if None in a or None in b:
        return None
    a_lo, a_hi = a
    b_lo, b_hi = b
    assert a_lo is not None and a_hi is not None and b_lo is not None and b_hi is not None
    return a_lo <= b_hi and b_lo <= a_hi


# ---------------------------------------------------------------------------------------------
# Capability descriptor (§2)


@dataclass(frozen=True)
class ProtocolEndpoint:
    """One IVOA protocol a source speaks: standard, version and base URL (from VOSI)."""

    standard: str
    """TAP, SIA2, SSA, SCS, DataLink or SODA."""
    version: str
    base_url: str

    def to_document(self) -> dict[str, Any]:
        return {"standard": self.standard, "version": self.version, "base_url": self.base_url}


@dataclass(frozen=True)
class Coverage:
    """Spatial, temporal and spectral extent where published; ``None`` is stated as unknown.

    Spatial coverage is ``all_sky`` or a list of bounded cones; time is in MJD; wavelength in metres.
    """

    all_sky: bool | None = None
    regions: tuple[Cone, ...] = ()
    t_min_mjd: float | None = None
    t_max_mjd: float | None = None
    em_min_m: float | None = None
    em_max_m: float | None = None

    def __post_init__(self) -> None:
        _range(self.t_min_mjd, self.t_max_mjd, "time coverage")
        _range(self.em_min_m, self.em_max_m, "spectral coverage")

    def covers_position(self, ra_deg: float, dec_deg: float) -> bool | None:
        if self.all_sky:
            return True
        if self.regions:
            return any(region_contains(r, ra_deg, dec_deg) for r in self.regions)
        return None if self.all_sky is None else False

    def to_document(self) -> dict[str, Any]:
        return {
            "all_sky": self.all_sky,
            "regions": [r.to_document() for r in self.regions],
            "t_min_mjd": self.t_min_mjd,
            "t_max_mjd": self.t_max_mjd,
            "em_min_m": self.em_min_m,
            "em_max_m": self.em_max_m,
        }


@dataclass(frozen=True)
class ServiceLimits:
    max_rows: int | None = None
    page_size: int | None = None
    requests_per_second: float | None = None
    max_concurrent: int | None = None
    upload: bool = False
    throughput_bytes_per_s: float | None = None
    """Measured in the spike or the weekly smoke; the planner's wall-time estimate reads it."""

    def to_document(self) -> dict[str, Any]:
        return {
            "max_rows": self.max_rows,
            "page_size": self.page_size,
            "requests_per_second": self.requests_per_second,
            "max_concurrent": self.max_concurrent,
            "upload": self.upload,
            "throughput_bytes_per_s": self.throughput_bytes_per_s,
        }


@dataclass(frozen=True)
class Rights:
    licence: str
    citation: str
    acknowledgement: str = ""

    def to_document(self) -> dict[str, Any]:
        return {"licence": self.licence, "citation": self.citation, "acknowledgement": self.acknowledgement}


@dataclass(frozen=True)
class AdapterInfo:
    """Which SDP adapter serves the source, and what its conformance says per product type."""

    name: str
    version: str
    last_compatibility_check: date | None = None
    conformance: Mapping[str, bool] = dataclass_field(default_factory=dict)
    """product type -> passed all four kinds in the current release."""

    def __post_init__(self) -> None:
        check_semver(self.version)

    def to_document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "last_compatibility_check": (
                self.last_compatibility_check.isoformat() if self.last_compatibility_check else None
            ),
            "conformance": dict(sorted(self.conformance.items())),
        }


@dataclass(frozen=True)
class SourceCapabilityDescriptor:
    """What a source can do, read from RegTAP, VOSI and ObsCore where published (ADR-017).

    ``origin`` is ``registry`` for a descriptor read from the VO and ``hand-written`` for a native
    service; the weekly smoke checks a hand-written one against the live service (INT-001).
    """

    source_id: str
    title: str
    publisher: str
    verified_on: date
    origin: str
    ivoid: str | None = None
    protocols: tuple[ProtocolEndpoint, ...] = ()
    data_models: tuple[str, ...] = ()
    product_types: frozenset[ProductType] = frozenset()
    missions: tuple[str, ...] = ()
    instruments: tuple[str, ...] = ()
    coverage: Coverage = dataclass_field(default_factory=Coverage)
    operations: frozenset[Operation] = frozenset()
    cutout_support: Mapping[str, str] = dataclass_field(default_factory=dict)
    """product type -> ``soda`` | ``astrocut`` | ``per-product`` | ``none``; never one global flag (INT-004)."""
    access: AccessStatus = AccessStatus.PUBLIC
    limits: ServiceLimits = dataclass_field(default_factory=ServiceLimits)
    rights: Rights = dataclass_field(default_factory=lambda: Rights("unknown", "unknown"))
    adapter: AdapterInfo | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise InvariantViolation("a descriptor names its source")
        if self.origin not in ("registry", "hand-written"):
            raise InvariantViolation(f"descriptor origin must be registry or hand-written, not {self.origin!r}")
        if self.origin == "registry" and not self.ivoid:
            raise InvariantViolation("a descriptor read from the registry carries its ivoid")
        for key in self.cutout_support:
            ProductType(key)

    def claims(self, product_type: ProductType) -> bool:
        return product_type in self.product_types

    def strip_unproven_claims(self) -> tuple[SourceCapabilityDescriptor, tuple[ProductType, ...]]:
        """Remove every product-type claim without a passing conformance suite (INT-002).

        Returns the stripped descriptor and the claims removed; the harness fails the release
        report when the second element is non-empty. Previews, thumbnails and auxiliary files are
        never evidence (INT-008), so they need no admission and are never stripped.
        """
        proven = dict(self.adapter.conformance) if self.adapter else {}
        removed = tuple(sorted(t for t in self.product_types if t.is_science and not proven.get(t.value, False)))
        if not removed:
            return self, ()
        kept = frozenset(t for t in self.product_types if t not in removed)
        return replace(self, product_types=kept), removed

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_id": self.source_id,
            "ivoid": self.ivoid,
            "title": self.title,
            "publisher": self.publisher,
            "origin": self.origin,
            "verified_on": self.verified_on.isoformat(),
            "protocols": [p.to_document() for p in self.protocols],
            "data_models": list(self.data_models),
            "product_types": sorted(t.value for t in self.product_types),
            "missions": list(self.missions),
            "instruments": list(self.instruments),
            "coverage": self.coverage.to_document(),
            "operations": sorted(o.value for o in self.operations),
            "cutout_support": dict(sorted(self.cutout_support.items())),
            "access": self.access.value,
            "limits": self.limits.to_document(),
            "rights": self.rights.to_document(),
            "adapter": self.adapter.to_document() if self.adapter else None,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> SourceCapabilityDescriptor:
        cov = doc.get("coverage") or {}
        lim = doc.get("limits") or {}
        rights = doc.get("rights") or {}
        adapter = doc.get("adapter")
        return cls(
            source_id=doc["source_id"],
            ivoid=doc.get("ivoid"),
            title=doc["title"],
            publisher=doc["publisher"],
            origin=doc["origin"],
            verified_on=date.fromisoformat(doc["verified_on"]),
            protocols=tuple(ProtocolEndpoint(**p) for p in doc.get("protocols", ())),
            data_models=tuple(doc.get("data_models", ())),
            product_types=frozenset(ProductType(t) for t in doc.get("product_types", ())),
            missions=tuple(doc.get("missions", ())),
            instruments=tuple(doc.get("instruments", ())),
            coverage=Coverage(
                all_sky=cov.get("all_sky"),
                regions=tuple(Cone(r["ra_deg"], r["dec_deg"], r["radius_deg"]) for r in cov.get("regions", ())),
                t_min_mjd=cov.get("t_min_mjd"),
                t_max_mjd=cov.get("t_max_mjd"),
                em_min_m=cov.get("em_min_m"),
                em_max_m=cov.get("em_max_m"),
            ),
            operations=frozenset(Operation(o) for o in doc.get("operations", ())),
            cutout_support=dict(doc.get("cutout_support", {})),
            access=AccessStatus(doc.get("access", "public")),
            limits=ServiceLimits(**lim),
            rights=Rights(**rights) if rights else Rights("unknown", "unknown"),
            adapter=(
                AdapterInfo(
                    name=adapter["name"],
                    version=adapter["version"],
                    last_compatibility_check=(
                        date.fromisoformat(adapter["last_compatibility_check"])
                        if adapter.get("last_compatibility_check")
                        else None
                    ),
                    conformance=dict(adapter.get("conformance", {})),
                )
                if adapter
                else None
            ),
        )


# ---------------------------------------------------------------------------------------------
# Evidence requirements and capability matching (§2, §5)


@dataclass(frozen=True)
class EvidenceRequirement:
    """What one registered test needs: product types, calibration, bands, epochs, coverage, bytes.

    Derived from the profile's tests, never from a hand-written archive list (v0.4.0 §5).
    """

    requirement_id: str
    test_kind: str
    product_types: frozenset[ProductType]
    min_calibration_level: int | None = None
    missions: tuple[str, ...] = ()
    instruments: tuple[str, ...] = ()
    epoch_min_mjd: float | None = None
    epoch_max_mjd: float | None = None
    em_min_m: float | None = None
    em_max_m: float | None = None
    operations: frozenset[Operation] = frozenset()
    needs_bytes: bool = True
    """False when the test reads only an index (metadata), so no product download is planned."""
    public_only: bool = True

    def __post_init__(self) -> None:
        if not self.requirement_id or not self.test_kind:
            raise InvariantViolation("an evidence requirement names itself and the test that needs it")
        if not self.product_types:
            raise InvariantViolation("an evidence requirement names at least one product type")
        if any(not t.is_science for t in self.product_types):
            raise InvariantViolation("a test cannot require previews, thumbnails or auxiliaries as evidence")
        if self.min_calibration_level is not None and not 0 <= self.min_calibration_level <= 4:
            raise InvariantViolation("calibration level is ObsCore 0 to 4")
        _range(self.epoch_min_mjd, self.epoch_max_mjd, "epoch range")
        _range(self.em_min_m, self.em_max_m, "spectral range")

    def to_document(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "test_kind": self.test_kind,
            "product_types": sorted(t.value for t in self.product_types),
            "min_calibration_level": self.min_calibration_level,
            "missions": list(self.missions),
            "instruments": list(self.instruments),
            "epoch_min_mjd": self.epoch_min_mjd,
            "epoch_max_mjd": self.epoch_max_mjd,
            "em_min_m": self.em_min_m,
            "em_max_m": self.em_max_m,
            "operations": sorted(o.value for o in self.operations),
            "needs_bytes": self.needs_bytes,
            "public_only": self.public_only,
        }


@dataclass(frozen=True)
class FieldReason:
    """One descriptor field checked against one requirement: passed, failed or unknown, and why."""

    field: str
    outcome: MatchStatus
    reason: str

    def to_document(self) -> dict[str, Any]:
        return {"field": self.field, "outcome": self.outcome.value, "reason": self.reason}


@dataclass(frozen=True)
class CapabilityMatch:
    """One source against one requirement, with a reason per descriptor field (INT-003)."""

    source_id: str
    requirement_id: str
    status: MatchStatus
    reasons: tuple[FieldReason, ...]
    descriptor_verified_on: date

    def __post_init__(self) -> None:
        if not self.reasons:
            raise InvariantViolation("a capability match records at least one field reason")

    def to_document(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "requirement_id": self.requirement_id,
            "status": self.status.value,
            "reasons": [r.to_document() for r in self.reasons],
            "descriptor_verified_on": self.descriptor_verified_on.isoformat(),
        }


def _centre(region: Cone | Box) -> tuple[float, float]:
    if isinstance(region, Cone):
        return region.ra_deg, region.dec_deg
    return region.centre


def match_requirement(
    descriptor: SourceCapabilityDescriptor, field_region: Cone | Box, need: EvidenceRequirement
) -> CapabilityMatch:
    """Match a requirement against a descriptor field by field (v0.4.0 §2).

    Every field is reported. Any failure makes the match ``unsatisfied``; any unknown with no
    failure makes it ``partial``; otherwise ``satisfied``. Pure and deterministic, so a match list
    is reproducible from the descriptors recorded in a plan.
    """
    reasons: list[FieldReason] = []

    def add(name: str, ok: bool | None, why_ok: str, why_not: str, why_unknown: str = "") -> None:
        if ok is None:
            reasons.append(FieldReason(name, MatchStatus.PARTIAL, why_unknown or f"{name} not published"))
        else:
            reasons.append(
                FieldReason(name, MatchStatus.SATISFIED if ok else MatchStatus.UNSATISFIED, why_ok if ok else why_not)
            )

    wanted = sorted(t.value for t in need.product_types)
    offered = sorted(t.value for t in descriptor.product_types)
    common = sorted(set(wanted) & set(offered))
    add(
        "product_types",
        bool(common),
        f"offers {', '.join(common)}",
        f"needs one of {', '.join(wanted)}; offers {', '.join(offered) or 'none'}",
    )

    if need.missions:
        if descriptor.missions:
            hit = sorted(set(need.missions) & set(descriptor.missions))
            add("missions", bool(hit), f"serves {', '.join(hit)}", f"does not serve {', '.join(need.missions)}")
        else:
            add("missions", None, "", "", "missions not published")
    if need.instruments:
        if descriptor.instruments:
            hit = sorted(set(need.instruments) & set(descriptor.instruments))
            add("instruments", bool(hit), f"serves {', '.join(hit)}", f"does not serve {', '.join(need.instruments)}")
        else:
            add("instruments", None, "", "", "instruments not published")

    ra, dec = _centre(field_region)
    add(
        "coverage.spatial",
        descriptor.coverage.covers_position(ra, dec),
        "field centre is covered",
        "field centre is outside the published coverage",
        "spatial coverage unknown",
    )
    if need.epoch_min_mjd is not None or need.epoch_max_mjd is not None:
        lo = need.epoch_min_mjd if need.epoch_min_mjd is not None else -math.inf
        hi = need.epoch_max_mjd if need.epoch_max_mjd is not None else math.inf
        cov = (descriptor.coverage.t_min_mjd, descriptor.coverage.t_max_mjd)
        add(
            "coverage.temporal",
            _overlaps((lo, hi), cov),
            "epoch range overlaps",
            "epoch range outside published time coverage",
            "time coverage unknown",
        )
    if need.em_min_m is not None and need.em_max_m is not None:
        cov = (descriptor.coverage.em_min_m, descriptor.coverage.em_max_m)
        add(
            "coverage.spectral",
            _overlaps((need.em_min_m, need.em_max_m), cov),
            "band overlaps",
            "band outside published spectral coverage",
            "spectral coverage unknown",
        )

    ops = set(need.operations)
    if need.needs_bytes:
        ops.add(Operation.DOWNLOAD)
    missing = sorted(o.value for o in ops - set(descriptor.operations))
    add("operations", not missing, "supports every operation needed", f"lacks {', '.join(missing)}")

    if need.public_only:
        add(
            "access",
            descriptor.access is AccessStatus.PUBLIC,
            "public access",
            f"access is {descriptor.access.value}; the plan never retries with credentials",
        )

    proven = dict(descriptor.adapter.conformance) if descriptor.adapter else {}
    admitted = sorted(t for t in common if proven.get(t, False))
    add(
        "adapter.conformance",
        bool(admitted) if common else False,
        f"adapter admitted for {', '.join(admitted)}",
        "no adapter admitted for the product types needed" if common else "no product type in common",
    )

    outcomes = {r.outcome for r in reasons}
    status = (
        MatchStatus.UNSATISFIED
        if MatchStatus.UNSATISFIED in outcomes
        else MatchStatus.PARTIAL
        if MatchStatus.PARTIAL in outcomes
        else MatchStatus.SATISFIED
    )
    return CapabilityMatch(descriptor.source_id, need.requirement_id, status, tuple(reasons), descriptor.verified_on)


def order_matches(matches: Iterable[CapabilityMatch]) -> tuple[CapabilityMatch, ...]:
    """Satisfied first, then partial, then unsatisfied; ties by source id, so the order is stable."""
    rank = {MatchStatus.SATISFIED: 0, MatchStatus.PARTIAL: 1, MatchStatus.UNSATISFIED: 2}
    return tuple(sorted(matches, key=lambda m: (m.requirement_id, rank[m.status], m.source_id)))


# ---------------------------------------------------------------------------------------------
# Scientific product model (§3)


@dataclass(frozen=True)
class ProductRelationship:
    """A DataLink-style link from this product to another (``#progenitor`` for a mosaic's inputs)."""

    semantics: RelationshipSemantics
    product_id: str
    description: str = ""

    def to_document(self) -> dict[str, Any]:
        return {"semantics": self.semantics.value, "product_id": self.product_id, "description": self.description}


@dataclass(frozen=True)
class PayloadExtension:
    """One extension of the raw file and what it carries."""

    name: str
    role: str
    """``data``, ``error``, ``quality``, ``variance`` or ``other``."""
    unit: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {"name": self.name, "role": self.role, "unit": self.unit}


@dataclass(frozen=True)
class ProductMetadata:
    """The ObsCore 1.1-shaped envelope a dialect fills (ADR-018).

    Unknown is stated as ``None``, never zero; the frame and the time scale are always stated.
    """

    product_id: str
    """Globally namespaced, e.g. ``mast:JWST/product/<file>``."""
    product_type: ProductType
    mission: str
    instrument: str | None
    detector: str | None
    filter_or_grating: str | None
    observation_id: str
    programme_id: str | None
    ra_deg: float | None
    dec_deg: float | None
    frame: str
    s_region: str | None
    time_start_mjd: float | None
    time_end_mjd: float | None
    time_scale: str
    em_min_m: float | None
    em_max_m: float | None
    spatial_resolution_arcsec: float | None
    field_of_view_deg: float | None
    calibration_level: int | None
    pipeline_version: str | None
    media_type: str
    dimensions: tuple[int, ...]
    units: str | None
    extensions: tuple[PayloadExtension, ...]
    has_wcs: bool
    access_status: AccessStatus
    exclusive_access_ends: date | None
    rights: Rights
    source_id: str
    rendered_query: str
    checksum: str
    size_bytes: int
    relationships: tuple[ProductRelationship, ...] = ()
    calibration_references: tuple[tuple[str, str], ...] = ()
    """Calibration reference files named in the header (keyword, file), recorded as provenance."""

    def __post_init__(self) -> None:
        if not self.frame:
            raise InvariantViolation("the envelope always states its frame")
        if not self.time_scale:
            raise InvariantViolation("the envelope always states its time scale")
        if self.calibration_level is not None and not 0 <= self.calibration_level <= 4:
            raise InvariantViolation("calibration level is ObsCore 0 to 4")
        if self.size_bytes < 0:
            raise InvariantViolation("size cannot be negative")
        _range(self.time_start_mjd, self.time_end_mjd, "observation time")
        _range(self.em_min_m, self.em_max_m, "spectral range")

    @property
    def has_uncertainty(self) -> bool:
        return any(e.role in ("error", "variance") for e in self.extensions)

    @property
    def has_quality(self) -> bool:
        return any(e.role == "quality" for e in self.extensions)

    def progenitors(self) -> tuple[str, ...]:
        return tuple(r.product_id for r in self.relationships if r.semantics is RelationshipSemantics.PROGENITOR)

    def to_document(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_type": self.product_type.value,
            "mission": self.mission,
            "instrument": self.instrument,
            "detector": self.detector,
            "filter_or_grating": self.filter_or_grating,
            "observation_id": self.observation_id,
            "programme_id": self.programme_id,
            "target_position": {"ra_deg": self.ra_deg, "dec_deg": self.dec_deg, "frame": self.frame},
            "s_region": self.s_region,
            "time": {"start_mjd": self.time_start_mjd, "end_mjd": self.time_end_mjd, "scale": self.time_scale},
            "spectral_range_m": [self.em_min_m, self.em_max_m],
            "spatial_resolution_arcsec": self.spatial_resolution_arcsec,
            "field_of_view_deg": self.field_of_view_deg,
            "calibration_level": self.calibration_level,
            "pipeline_version": self.pipeline_version,
            "format": self.media_type,
            "dimensions": list(self.dimensions),
            "units": self.units,
            "extensions": [e.to_document() for e in self.extensions],
            "has_wcs": self.has_wcs,
            "uncertainty_present": self.has_uncertainty,
            "quality_present": self.has_quality,
            "access_status": self.access_status.value,
            "exclusive_access_ends": self.exclusive_access_ends.isoformat() if self.exclusive_access_ends else None,
            "rights": self.rights.to_document(),
            "source_id": self.source_id,
            "query": self.rendered_query,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "relationships": [r.to_document() for r in self.relationships],
            "calibration_references": [{"keyword": k, "file": f} for k, f in self.calibration_references],
        }


@dataclass(frozen=True)
class SemanticCheck:
    field: str
    passed: bool
    detail: str = ""

    def to_document(self) -> dict[str, Any]:
        return {"field": self.field, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class SemanticValidationReport:
    """The validator's verdict on one filled envelope; stored beside the product (v0.4.0 §3)."""

    product_id: str
    validator_id: str
    validator_version: str
    checks: tuple[SemanticCheck, ...]

    def __post_init__(self) -> None:
        check_semver(self.validator_version)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    def failures(self) -> tuple[SemanticCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)

    def to_document(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "validator_id": self.validator_id,
            "validator_version": self.validator_version,
            "passed": self.passed,
            "checks": [c.to_document() for c in self.checks],
        }


@dataclass(frozen=True)
class ScientificDataProduct:
    """A registered raw artifact, kept byte for byte, plus its envelope (ADR-018).

    ``curated_artifact_id`` is set only through :meth:`curate`, which enforces the preservation
    rules: a raw parent, a passing semantic report, and never a preview as science (INT-007, INT-008).
    """

    raw_artifact_id: str
    metadata: ProductMetadata
    report: SemanticValidationReport | None = None
    curated_artifact_id: str | None = None

    def __post_init__(self) -> None:
        if not self.raw_artifact_id:
            raise InvariantViolation("a product keeps its raw artifact (INT-005)")
        if self.metadata.checksum != self.raw_artifact_id:
            raise InvariantViolation("the envelope checksum is the SHA-256 of the raw bytes actually read")
        if self.curated_artifact_id is not None:
            _require_curatable(self.metadata, self.report)

    @property
    def product_type(self) -> ProductType:
        return self.metadata.product_type

    def validated(self, report: SemanticValidationReport) -> ScientificDataProduct:
        if report.product_id != self.metadata.product_id:
            raise InvariantViolation("a semantic report validates the product it names")
        return replace(self, report=report)

    def curate(self, curated_artifact_id: str) -> ScientificDataProduct:
        return replace(self, curated_artifact_id=curated_artifact_id)

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "raw_artifact_id": self.raw_artifact_id,
            "curated_artifact_id": self.curated_artifact_id,
            "metadata": self.metadata.to_document(),
            "validation": self.report.to_document() if self.report else None,
        }


def _require_curatable(metadata: ProductMetadata, report: SemanticValidationReport | None) -> None:
    if not metadata.product_type.is_science:
        raise CurationBlocked(f"a {metadata.product_type.value} product is never curated as science (INT-008)")
    if report is None or not report.passed:
        failed = ", ".join(c.field for c in report.failures()) if report else "no semantic report"
        raise CurationBlocked(f"curation blocked by semantic validation: {failed} (INT-007)")


class ReferenceStatus(StrEnum):
    """Verification status of a reference record, as for the relativity oracles (v0.4.0 §6, §14)."""

    TO_VERIFY = "to_verify"
    VERIFIED = "verified"
    SYNTHETIC = "synthetic"
    """Truth built with the fixture, for code tests; never admits an adapter to a gate."""


@dataclass(frozen=True)
class ReferenceRecord:
    """The hand-verified meaning of one fixture product, written from the mission documentation.

    T5s normalises the fixture through the adapter and dialect and compares field by field; any
    difference fails with the field named (v0.4.0 §6). ``documentation`` cites the page each value
    was read from.
    """

    product_id: str
    product_type: ProductType
    mission: str
    instrument: str | None
    frame: str
    time_scale: str
    units: str | None
    calibration_level: int | None
    extensions: Mapping[str, str]
    """Extension (or ``HDU:COLUMN``) name -> role."""
    has_wcs: bool
    documentation: str
    status: ReferenceStatus
    detector: str | None = None
    filter_or_grating: str | None = None
    time_start_mjd: float | None = None
    time_end_mjd: float | None = None
    progenitors: tuple[str, ...] = ()
    verified_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status is ReferenceStatus.VERIFIED and len(set(self.verified_by)) < 2:
            raise InvariantViolation("a verified reference record carries two independent passes")
        if not self.documentation:
            raise InvariantViolation("a reference record cites the documentation it was read from")

    @property
    def admits(self) -> bool:
        """Only a second-pass verified record can admit an adapter to a gate."""
        return self.status is ReferenceStatus.VERIFIED

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ReferenceRecord:
        return cls(
            product_id=doc["product_id"],
            product_type=ProductType(doc["product_type"]),
            mission=doc["mission"],
            instrument=doc.get("instrument"),
            detector=doc.get("detector"),
            filter_or_grating=doc.get("filter_or_grating"),
            frame=doc["frame"],
            time_scale=doc["time_scale"],
            time_start_mjd=doc.get("time_start_mjd"),
            time_end_mjd=doc.get("time_end_mjd"),
            units=doc.get("units"),
            calibration_level=doc.get("calibration_level"),
            extensions=dict(doc.get("extensions", {})),
            has_wcs=bool(doc["has_wcs"]),
            progenitors=tuple(doc.get("progenitors", ())),
            documentation=doc["documentation"],
            status=ReferenceStatus(doc["status"]),
            verified_by=tuple(doc.get("verified_by", ())),
        )


TIME_TOLERANCE_DAYS = 1e-6
"""About 0.09 s: tighter than any exposure boundary a test depends on, looser than float round-trips."""


def compare_to_reference(metadata: ProductMetadata, ref: ReferenceRecord) -> tuple[SemanticCheck, ...]:
    """The T5s comparison: the normalised envelope against its reference record, field by field."""
    checks: list[SemanticCheck] = []

    def same(field_name: str, got: Any, want: Any) -> None:
        a = None if got is None else str(got).strip().upper()
        b = None if want is None else str(want).strip().upper()
        checks.append(SemanticCheck(field_name, a == b, f"got {got!r}, reference {want!r}"))

    same("product_id", metadata.product_id, ref.product_id)
    same("product_type", metadata.product_type.value, ref.product_type.value)
    same("mission", metadata.mission, ref.mission)
    same("instrument", metadata.instrument, ref.instrument)
    same("detector", metadata.detector, ref.detector)
    same("filter_or_grating", metadata.filter_or_grating, ref.filter_or_grating)
    same("frame", metadata.frame, ref.frame)
    same("time_scale", metadata.time_scale, ref.time_scale)
    same("units", metadata.units, ref.units)
    same("calibration_level", metadata.calibration_level, ref.calibration_level)
    same("has_wcs", metadata.has_wcs, ref.has_wcs)
    for name, got_t, want_t in (
        ("time_start_mjd", metadata.time_start_mjd, ref.time_start_mjd),
        ("time_end_mjd", metadata.time_end_mjd, ref.time_end_mjd),
    ):
        if want_t is None:
            continue
        ok = got_t is not None and abs(got_t - want_t) <= TIME_TOLERANCE_DAYS
        checks.append(SemanticCheck(name, ok, f"got {got_t}, reference {want_t}"))
    got_ext = {e.name: e.role for e in metadata.extensions}
    for name, role in sorted(ref.extensions.items()):
        checks.append(
            SemanticCheck(
                f"extensions.{name}", got_ext.get(name) == role, f"got {got_ext.get(name)!r}, reference {role!r}"
            )
        )
    for name in sorted(set(got_ext) - set(ref.extensions)):
        if got_ext[name] != "other":
            checks.append(SemanticCheck(f"extensions.{name}", False, "not in the reference record"))
    if ref.progenitors:
        got_p = sorted(metadata.progenitors())
        checks.append(
            SemanticCheck(
                "relationships.progenitors",
                got_p == sorted(ref.progenitors),
                f"got {got_p}, reference {sorted(ref.progenitors)}",
            )
        )
    return tuple(checks)


# ---------------------------------------------------------------------------------------------
# Acquisition planning (§5)


@dataclass(frozen=True)
class AcquisitionBudget:
    """Profile-level limits on what a plan may fetch, and the threshold above which a signed
    ledger approval is required (INT-014). DSI-010 still refuses on NAS headroom regardless."""

    max_bytes: int
    max_requests_per_source: int
    max_wall_seconds: float
    min_nas_headroom_bytes: int
    approval_threshold_bytes: int

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_requests_per_source, self.min_nas_headroom_bytes) < 0:
            raise InvariantViolation("budget limits cannot be negative")
        if not 0 <= self.approval_threshold_bytes <= self.max_bytes:
            raise InvariantViolation("the approval threshold lies within the byte budget")
        if not (math.isfinite(self.max_wall_seconds) and self.max_wall_seconds > 0):
            raise InvariantViolation("wall-time budget must be positive and finite")

    def to_document(self) -> dict[str, Any]:
        return {
            "max_bytes": self.max_bytes,
            "max_requests_per_source": self.max_requests_per_source,
            "max_wall_seconds": self.max_wall_seconds,
            "min_nas_headroom_bytes": self.min_nas_headroom_bytes,
            "approval_threshold_bytes": self.approval_threshold_bytes,
        }


@dataclass(frozen=True)
class PlanItem:
    """One acquisition the plan authorises, and why: the requirement and the test it serves."""

    item_id: str
    source_id: str
    operation: Operation
    target: str
    """The rendered query, or the product id for a download or cutout."""
    expected_bytes: int
    expected_rows: int | None
    calibration_level: int | None
    access_status: AccessStatus
    licence: str
    requirement_id: str
    test_kind: str

    def __post_init__(self) -> None:
        if self.expected_bytes < 0:
            raise InvariantViolation("expected bytes cannot be negative")
        if self.access_status is not AccessStatus.PUBLIC:
            raise InvariantViolation("a plan item is public data; restricted products are reported as gaps")

    def to_document(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "source_id": self.source_id,
            "operation": self.operation.value,
            "target": self.target,
            "expected_bytes": self.expected_bytes,
            "expected_rows": self.expected_rows,
            "calibration_level": self.calibration_level,
            "access_status": self.access_status.value,
            "licence": self.licence,
            "requirement_id": self.requirement_id,
            "test_kind": self.test_kind,
        }


@dataclass(frozen=True)
class PlanGap:
    requirement_id: str
    reason: str

    def to_document(self) -> dict[str, Any]:
        return {"requirement_id": self.requirement_id, "reason": self.reason}


@dataclass(frozen=True)
class PlanCost:
    total_bytes: int
    requests_per_source: Mapping[str, int]
    estimated_wall_seconds: float
    nas_headroom_after_bytes: int | None

    def to_document(self) -> dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "requests_per_source": dict(sorted(self.requests_per_source.items())),
            "estimated_wall_seconds": self.estimated_wall_seconds,
            "nas_headroom_after_bytes": self.nas_headroom_after_bytes,
        }


class ApprovalDecision(StrEnum):
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    REFUSED = "refused"


@dataclass(frozen=True)
class PlanApproval:
    """The decision on one plan: automatic below the threshold, a signed ledger entry above it."""

    plan_id: str
    decision: ApprovalDecision
    actor: str
    threshold_bytes: int
    ledger_entry_id: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.decision is ApprovalDecision.APPROVED and not self.ledger_entry_id:
            raise IntegrationRefused("an above-threshold approval is a signed ledger entry (INT-014)")
        if self.decision is ApprovalDecision.REFUSED and not self.reason:
            raise InvariantViolation("a refusal records why")

    @property
    def approves(self) -> bool:
        return self.decision is not ApprovalDecision.REFUSED

    def to_document(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "decision": self.decision.value,
            "actor": self.actor,
            "threshold_bytes": self.threshold_bytes,
            "ledger_entry_id": self.ledger_entry_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AcquisitionPlan:
    """The content-addressed plan between PlanInvestigation and ApprovePlan (ADR-020).

    The plan id is the hash of everything but the approval, so approving a plan does not change
    which plan it is, and the harness can re-derive the item list from the recorded requirements
    and capability matches (``rederive-plan``).
    """

    field: Mapping[str, Any]
    profile_hash: str
    planner_id: str
    planner_version: str
    requirements: tuple[EvidenceRequirement, ...]
    matches: tuple[CapabilityMatch, ...]
    items: tuple[PlanItem, ...]
    cost: PlanCost
    budget: AcquisitionBudget
    gaps: tuple[PlanGap, ...] = ()
    constraints: Mapping[str, Any] = dataclass_field(default_factory=dict)
    approval: PlanApproval | None = None

    def __post_init__(self) -> None:
        check_semver(self.planner_version)
        ids = [i.item_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise InvariantViolation("plan item ids are unique")
        known = {r.requirement_id for r in self.requirements}
        for item in self.items:
            if item.requirement_id not in known:
                raise InvariantViolation(f"plan item {item.item_id} serves no requirement the plan derived")
        if self.cost.total_bytes != sum(i.expected_bytes for i in self.items):
            raise InvariantViolation("plan cost totals its items")
        if self.cost.total_bytes > self.budget.max_bytes:
            raise IntegrationRefused("a plan cannot exceed its profile's byte budget")
        if self.approval is not None and self.approval.plan_id != self.plan_id:
            raise InvariantViolation("an approval covers the plan it names")

    def intent_document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "intent": {
                "field": _jsonable(self.field),
                "profile_hash": self.profile_hash,
                "planner_id": self.planner_id,
                "planner_version": self.planner_version,
                "requirements": [r.to_document() for r in self.requirements],
            },
            "discovery": [m.to_document() for m in self.matches],
            "items": [i.to_document() for i in self.items],
            "cost": self.cost.to_document(),
            "constraints": {**_jsonable(self.constraints), "budget": self.budget.to_document()},
            "gaps": [g.to_document() for g in self.gaps],
        }

    @property
    def plan_id(self) -> str:
        return hash_document(self.intent_document())

    @property
    def requires_signed_approval(self) -> bool:
        return self.cost.total_bytes > self.budget.approval_threshold_bytes

    def decide(
        self, *, actor: str, ledger_entry_id: str | None = None, refuse_reason: str | None = None
    ) -> AcquisitionPlan:
        """Approve or refuse: automatic below the threshold; above it, a ledger entry is required."""
        if refuse_reason:
            approval = PlanApproval(
                self.plan_id,
                ApprovalDecision.REFUSED,
                actor,
                self.budget.approval_threshold_bytes,
                ledger_entry_id,
                refuse_reason,
            )
        elif self.requires_signed_approval:
            if not ledger_entry_id:
                raise IntegrationRefused(
                    f"plan needs {self.cost.total_bytes} bytes, above the approval threshold of "
                    f"{self.budget.approval_threshold_bytes}; a signed ledger approval is required (INT-014)"
                )
            approval = PlanApproval(
                self.plan_id, ApprovalDecision.APPROVED, actor, self.budget.approval_threshold_bytes, ledger_entry_id
            )
        else:
            approval = PlanApproval(
                self.plan_id,
                ApprovalDecision.AUTO_APPROVED,
                actor,
                self.budget.approval_threshold_bytes,
                ledger_entry_id,
            )
        return replace(self, approval=approval)

    def authorise(self, source_id: str, operation: Operation, target: str) -> PlanItem:
        """The item that authorises this acquisition, or a refusal (INT-014, INT-015)."""
        if self.approval is None or not self.approval.approves:
            raise IntegrationRefused(f"plan {self.plan_id} is not approved; nothing may be acquired under it")
        for item in self.items:
            if (item.source_id, item.operation, item.target) == (source_id, operation, target):
                return item
        raise IntegrationRefused(
            f"no approved plan item names {operation.value} {target!r} at {source_id}; acquisition refused (INT-015)"
        )

    def to_document(self) -> dict[str, Any]:
        doc = self.intent_document()
        doc["plan_id"] = self.plan_id
        doc["approval"] = self.approval.to_document() if self.approval else None
        return doc


# ---------------------------------------------------------------------------------------------
# Adapter compatibility (§6)


@dataclass(frozen=True)
class ConformanceResult:
    kind: ConformanceKind
    passed: bool
    ci_run_id: str

    def to_document(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "passed": self.passed, "ci_run_id": self.ci_run_id}


@dataclass(frozen=True)
class AdapterCompatibilityRecord:
    """Four-kind conformance for one adapter and one product type, in the manifest (ADR-021)."""

    adapter: str
    adapter_version: str
    product_type: ProductType
    results: tuple[ConformanceResult, ...]
    upstream_versions: Mapping[str, str] = dataclass_field(default_factory=dict)
    live_smoke_on: date | None = None
    fixture_digests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check_semver(self.adapter_version)
        kinds = [r.kind for r in self.results]
        if len(kinds) != len(set(kinds)):
            raise InvariantViolation("one result per conformance kind")

    @property
    def passed(self) -> bool:
        got = {r.kind: r.passed for r in self.results}
        return all(got.get(k, False) for k in ConformanceKind)

    def missing(self) -> tuple[ConformanceKind, ...]:
        got = {r.kind: r.passed for r in self.results}
        return tuple(k for k in ConformanceKind if not got.get(k, False))

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "adapter": self.adapter,
            "adapter_version": self.adapter_version,
            "product_type": self.product_type.value,
            "upstream_versions": dict(sorted(self.upstream_versions.items())),
            "results": [r.to_document() for r in self.results],
            "passed": self.passed,
            "live_smoke_on": self.live_smoke_on.isoformat() if self.live_smoke_on else None,
            "fixture_digests": list(self.fixture_digests),
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> AdapterCompatibilityRecord:
        return cls(
            adapter=doc["adapter"],
            adapter_version=doc["adapter_version"],
            product_type=ProductType(doc["product_type"]),
            results=tuple(
                ConformanceResult(ConformanceKind(r["kind"]), bool(r["passed"]), r["ci_run_id"])
                for r in doc.get("results", ())
            ),
            upstream_versions=dict(doc.get("upstream_versions", {})),
            live_smoke_on=date.fromisoformat(doc["live_smoke_on"]) if doc.get("live_smoke_on") else None,
            fixture_digests=tuple(doc.get("fixture_digests", ())),
        )


def require_admitted(records: Sequence[AdapterCompatibilityRecord], used: Iterable[tuple[str, ProductType]]) -> None:
    """Refuse a verdict when any (adapter, product type) used lacks a passing record (INT-016)."""
    passing = {(r.adapter, r.product_type) for r in records if r.passed}
    missing = sorted({(a, t.value) for a, t in used if (a, t) not in passing})
    if missing:
        named = ", ".join(f"{a} for {t}" for a, t in missing)
        raise IntegrationRefused(f"no passing adapter compatibility record: {named}; no verdict (INT-016)")
