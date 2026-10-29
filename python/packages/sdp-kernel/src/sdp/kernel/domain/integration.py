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

import copy
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from sdp.kernel.domain.archive import Box, Cone
from sdp.kernel.domain.canonical import hash_document
from sdp.kernel.domain.errors import CurationBlocked, IntegrityError, InvariantViolation
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
    CUTOUT = "#cutout"
    """On a cutout's envelope: the product it was cut from."""


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
    calibration_levels: frozenset[int] = frozenset()
    """ObsCore ``calib_level`` values the source serves; empty when it does not publish them."""
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
        if any(not 0 <= level <= 4 for level in self.calibration_levels):
            raise InvariantViolation("calibration level is ObsCore 0 to 4")

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
            "calibration_levels": sorted(self.calibration_levels),
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
            calibration_levels=frozenset(int(level) for level in doc.get("calibration_levels", ())),
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

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> EvidenceRequirement:
        return cls(
            requirement_id=doc["requirement_id"],
            test_kind=doc["test_kind"],
            product_types=frozenset(ProductType(t) for t in doc["product_types"]),
            min_calibration_level=doc.get("min_calibration_level"),
            missions=tuple(doc.get("missions", ())),
            instruments=tuple(doc.get("instruments", ())),
            epoch_min_mjd=doc.get("epoch_min_mjd"),
            epoch_max_mjd=doc.get("epoch_max_mjd"),
            em_min_m=doc.get("em_min_m"),
            em_max_m=doc.get("em_max_m"),
            operations=frozenset(Operation(o) for o in doc.get("operations", ())),
            needs_bytes=bool(doc.get("needs_bytes", True)),
            public_only=bool(doc.get("public_only", True)),
        )


@dataclass(frozen=True)
class FieldReason:
    """One descriptor field checked against one requirement: passed, failed or unknown, and why."""

    field: str
    outcome: MatchStatus
    reason: str

    def to_document(self) -> dict[str, Any]:
        return {"field": self.field, "outcome": self.outcome.value, "reason": self.reason}

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> FieldReason:
        return cls(doc["field"], MatchStatus(doc["outcome"]), doc["reason"])


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

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> CapabilityMatch:
        return cls(
            doc["source_id"],
            doc["requirement_id"],
            MatchStatus(doc["status"]),
            tuple(FieldReason.from_document(r) for r in doc["reasons"]),
            date.fromisoformat(doc["descriptor_verified_on"]),
        )


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
    if need.em_min_m is not None or need.em_max_m is not None:
        lo = need.em_min_m if need.em_min_m is not None else -math.inf
        hi = need.em_max_m if need.em_max_m is not None else math.inf
        cov = (descriptor.coverage.em_min_m, descriptor.coverage.em_max_m)
        add(
            "coverage.spectral",
            _overlaps((lo, hi), cov),
            "band overlaps",
            "band outside published spectral coverage",
            "spectral coverage unknown",
        )

    if need.min_calibration_level is not None:
        levels = sorted(descriptor.calibration_levels)
        add(
            "calibration_level",
            levels[-1] >= need.min_calibration_level if levels else None,
            f"serves calibration level {levels[-1] if levels else ''}",
            f"needs calibration level {need.min_calibration_level}; serves at most {levels[-1] if levels else ''}",
            "calibration levels not published",
        )

    ops = set(need.operations)
    if need.needs_bytes:
        ops.add(Operation.DOWNLOAD)
    missing = sorted(o.value for o in ops - set(descriptor.operations))
    add("operations", not missing, "supports every operation needed", f"lacks {', '.join(missing)}")
    if Operation.CUTOUT in ops and common:
        support = {t: descriptor.cutout_support.get(t) for t in common}
        cutting = sorted(t for t, how in support.items() if how not in (None, "none"))
        add(
            "cutout_support",
            True if cutting else None if None in support.values() else False,
            f"cutouts for {', '.join(cutting)}",
            f"no cutouts for {', '.join(common)}",
            f"cutout support not published for {', '.join(t for t, how in support.items() if how is None)}",
        )

    if need.public_only:
        add(
            "access",
            descriptor.access is AccessStatus.PUBLIC,
            "public access",
            f"access is {descriptor.access.value}; the plan never retries with credentials",
        )

    proven = dict(descriptor.adapter.conformance) if descriptor.adapter else {}
    admitted = sorted(t for t in common if proven.get(t, False))
    # An adapter not yet admitted can still feed an exploratory run, which never issues a verdict;
    # a verdict on its evidence stays refused until a passing record admits it (INT-016).
    add(
        "adapter.conformance",
        (True if admitted else None) if common else False,
        f"adapter admitted for {', '.join(admitted)}",
        "no product type in common",
        f"no adapter admitted for {', '.join(common)} yet: exploratory evidence only, no verdict until one is",
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


class CutoutMethod(StrEnum):
    """How a cutout of one product is made (INT-004)."""

    SODA = "soda"
    """The product's DataLink row names a SODA service (``#cutout``)."""
    RANGE = "range"
    """An uncompressed FITS image whose host answered a byte-range request: SDP reads the header and
    the rows around the field, never the whole file."""
    NONE = "none"


@dataclass(frozen=True)
class CutoutSupport:
    """Whether and how one product can be cut out, with the evidence it was read from (INT-004)."""

    method: CutoutMethod
    evidence: str
    service_url: str | None = None

    def __post_init__(self) -> None:
        if not self.evidence:
            raise InvariantViolation("cutout support records the evidence it was read from (INT-004)")
        if self.method is CutoutMethod.SODA and not self.service_url:
            raise InvariantViolation("SODA cutout support names its service")

    @property
    def supported(self) -> bool:
        return self.method is not CutoutMethod.NONE

    def to_document(self) -> dict[str, Any]:
        return {"method": self.method.value, "service_url": self.service_url, "evidence": self.evidence}


#: DataLink ``semantics`` for a server-side cutout service.
CUTOUT_SEMANTICS = "#cutout"
FITS_FORMATS = frozenset({"application/fits", "image/fits", "application/x-fits"})


def cutout_support(
    links: Sequence[Mapping[str, Any]], media_type: str, range_status: int | None, url: str
) -> CutoutSupport:
    """Read per product, never assumed per archive (INT-004): a DataLink ``#cutout`` row with a SODA
    service wins; otherwise a FITS file whose host answered a one-block range request with 206 can be
    cut by range; otherwise none, saying why."""
    for row in links:
        if str(row.get("semantics")) == CUTOUT_SEMANTICS and row.get("service_def") and row.get("access_url"):
            return CutoutSupport(
                CutoutMethod.SODA, f"DataLink #cutout row, service {row['service_def']}", str(row["access_url"])
            )
    if media_type.split(";")[0].strip().lower() not in FITS_FORMATS:
        return CutoutSupport(CutoutMethod.NONE, f"{media_type} is not FITS and DataLink names no cutout service")
    if range_status == 206:
        return CutoutSupport(CutoutMethod.RANGE, f"HTTP 206 to a byte-range request for {url}")
    answered = "not probed" if range_status is None else f"HTTP {range_status}"
    return CutoutSupport(CutoutMethod.NONE, f"no DataLink cutout service; a byte-range request was {answered}")


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
    cutout: CutoutSupport | None = None
    """How this product can be cut out, read per product (INT-004); ``None`` when not recorded."""

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

    def digest(self) -> str:
        """The hash of the canonical envelope; a semantic report records it (v0.4.0 §3)."""
        return hash_document(self.to_document())

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
            # Only when recorded, so envelopes written before cutouts keep their digest.
            **({"cutout": self.cutout.to_document()} if self.cutout is not None else {}),
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
    envelope_digest: str
    """The digest of the envelope these checks ran on, so a report never validates a changed one."""

    def __post_init__(self) -> None:
        check_semver(self.validator_version)
        if not self.envelope_digest:
            raise InvariantViolation("a semantic report names the envelope it checked")

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
            "envelope_digest": self.envelope_digest,
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
        if self.report is not None:
            _require_report_for(self.metadata, self.report)
        if self.curated_artifact_id is not None:
            _require_curatable(self.metadata, self.report)

    @property
    def product_type(self) -> ProductType:
        return self.metadata.product_type

    def validated(self, report: SemanticValidationReport) -> ScientificDataProduct:
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


def _require_report_for(metadata: ProductMetadata, report: SemanticValidationReport) -> None:
    if report.product_id != metadata.product_id:
        raise InvariantViolation("a semantic report validates the product it names")
    if report.envelope_digest != metadata.digest():
        raise InvariantViolation("a semantic report validates the envelope it checked, not a changed one")


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
        if self.status is ReferenceStatus.VERIFIED and not any(v.strip() for v in self.verified_by):
            raise InvariantViolation("a verified reference record names who verified it")
        if not self.documentation:
            raise InvariantViolation("a reference record cites the documentation it was read from")

    @property
    def admits(self) -> bool:
        """Only a verified record can admit an adapter to a gate."""
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

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> AcquisitionBudget:
        return cls(
            max_bytes=int(doc["max_bytes"]),
            max_requests_per_source=int(doc["max_requests_per_source"]),
            max_wall_seconds=float(doc["max_wall_seconds"]),
            min_nas_headroom_bytes=int(doc["min_nas_headroom_bytes"]),
            approval_threshold_bytes=int(doc["approval_threshold_bytes"]),
        )


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

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> PlanItem:
        return cls(
            item_id=doc["item_id"],
            source_id=doc["source_id"],
            operation=Operation(doc["operation"]),
            target=doc["target"],
            expected_bytes=int(doc["expected_bytes"]),
            expected_rows=doc.get("expected_rows"),
            calibration_level=doc.get("calibration_level"),
            access_status=AccessStatus(doc["access_status"]),
            licence=doc["licence"],
            requirement_id=doc["requirement_id"],
            test_kind=doc["test_kind"],
        )


@dataclass(frozen=True)
class PlanGap:
    requirement_id: str
    reason: str

    def to_document(self) -> dict[str, Any]:
        return {"requirement_id": self.requirement_id, "reason": self.reason}

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> PlanGap:
        return cls(doc["requirement_id"], doc["reason"])


@dataclass(frozen=True)
class PlanCost:
    total_bytes: int
    requests_per_source: Mapping[str, int]
    estimated_wall_seconds: float
    nas_headroom_after_bytes: int | None
    """None when the store cannot report headroom; DSI-010 still refuses at acquisition time."""

    def __post_init__(self) -> None:
        # The plan id hashes this mapping, so it is copied and frozen rather than kept by reference.
        object.__setattr__(self, "requests_per_source", MappingProxyType(dict(self.requests_per_source)))

    def to_document(self) -> dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "requests_per_source": dict(sorted(self.requests_per_source.items())),
            "estimated_wall_seconds": self.estimated_wall_seconds,
            "nas_headroom_after_bytes": self.nas_headroom_after_bytes,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> PlanCost:
        return cls(
            int(doc["total_bytes"]),
            {k: int(v) for k, v in doc["requests_per_source"].items()},
            float(doc["estimated_wall_seconds"]),
            doc.get("nas_headroom_after_bytes"),
        )


class ApprovalDecision(StrEnum):
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    REFUSED = "refused"


class SignedApproval(Protocol):
    """What :meth:`AcquisitionPlan.decide` reads from the ledger entry that approves a plan; the
    ledger's ``LedgerEntry`` has this shape. The caller fetches it from the ledger, which verifies
    the signature and the chain; the plan checks it is an approval of this plan by this actor."""

    @property
    def entry_id(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def subject_hash(self) -> str: ...

    @property
    def signer(self) -> str: ...


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

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> PlanApproval:
        return cls(
            doc["plan_id"],
            ApprovalDecision(doc["decision"]),
            doc["actor"],
            int(doc["threshold_bytes"]),
            doc.get("ledger_entry_id"),
            doc.get("reason", ""),
        )


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
        # The plan id hashes these, so they are copied rather than kept by reference.
        object.__setattr__(self, "field", MappingProxyType(copy.deepcopy(dict(self.field))))
        object.__setattr__(self, "constraints", MappingProxyType(copy.deepcopy(dict(self.constraints))))
        ids = [i.item_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise InvariantViolation("plan item ids are unique")
        known = {r.requirement_id: r for r in self.requirements}
        for item in self.items:
            need = known.get(item.requirement_id)
            if need is None:
                raise InvariantViolation(f"plan item {item.item_id} serves no requirement the plan derived")
            floor = need.min_calibration_level
            if floor is not None and (item.calibration_level is None or item.calibration_level < floor):
                raise IntegrationRefused(
                    f"plan item {item.item_id} is calibration level {item.calibration_level}; "
                    f"{need.requirement_id} needs {floor} or above"
                )
        if self.cost.total_bytes != sum(i.expected_bytes for i in self.items):
            raise InvariantViolation("plan cost totals its items")
        self._check_budget()
        if self.approval is not None:
            self._check_approval(self.approval)

    def _check_budget(self) -> None:
        budget, cost = self.budget, self.cost
        if cost.total_bytes > budget.max_bytes:
            raise IntegrationRefused("a plan cannot exceed its profile's byte budget")
        over = sorted(s for s, n in cost.requests_per_source.items() if n > budget.max_requests_per_source)
        if over:
            raise IntegrationRefused(
                f"a plan cannot exceed {budget.max_requests_per_source} requests per source: {', '.join(over)}"
            )
        if not (math.isfinite(cost.estimated_wall_seconds) and cost.estimated_wall_seconds <= budget.max_wall_seconds):
            raise IntegrationRefused("a plan cannot exceed its profile's wall-time budget")
        if cost.nas_headroom_after_bytes is not None and cost.nas_headroom_after_bytes < budget.min_nas_headroom_bytes:
            raise IntegrationRefused("a plan cannot leave less NAS headroom than its profile requires (DSI-010)")

    def _check_approval(self, approval: PlanApproval) -> None:
        if approval.plan_id != self.plan_id:
            raise InvariantViolation("an approval covers the plan it names")
        if approval.decision is ApprovalDecision.REFUSED:
            return
        if approval.threshold_bytes != self.budget.approval_threshold_bytes:
            raise IntegrationRefused("an approval was given against this plan's own approval threshold")
        if self.requires_signed_approval and approval.decision is not ApprovalDecision.APPROVED:
            raise IntegrationRefused(
                "a plan above its approval threshold is approved only by a signed ledger entry (INT-014)"
            )

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
        self, *, actor: str, ledger_entry: SignedApproval | None = None, refuse_reason: str | None = None
    ) -> AcquisitionPlan:
        """Approve or refuse: automatic below the threshold; above it, the ledger entry that approves
        this plan is required. The entry must be an ``approval`` whose subject is this plan's id,
        signed by ``actor``; an entry id alone, or an entry for another plan, approves nothing."""
        if ledger_entry is not None:
            if ledger_entry.kind != "approval" or ledger_entry.subject_hash != self.plan_id:
                raise IntegrationRefused(f"ledger entry {ledger_entry.entry_id} does not approve plan {self.plan_id}")
            if ledger_entry.signer != actor:
                raise IntegrationRefused(f"ledger entry {ledger_entry.entry_id} was signed by another actor")
        ledger_entry_id = ledger_entry.entry_id if ledger_entry is not None else None
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
        self._check_approval(self.approval)
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

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> AcquisitionPlan:
        """The plan a document records; a document whose ``plan_id`` does not hash its intent is
        refused, so a stored plan cannot be edited into another one."""
        intent = doc["intent"]
        constraints = dict(doc["constraints"])
        budget = AcquisitionBudget.from_document(constraints.pop("budget"))
        approval = doc.get("approval")
        plan = cls(
            field=intent["field"],
            profile_hash=intent["profile_hash"],
            planner_id=intent["planner_id"],
            planner_version=intent["planner_version"],
            requirements=tuple(EvidenceRequirement.from_document(r) for r in intent["requirements"]),
            matches=tuple(CapabilityMatch.from_document(m) for m in doc["discovery"]),
            items=tuple(PlanItem.from_document(i) for i in doc["items"]),
            cost=PlanCost.from_document(doc["cost"]),
            budget=budget,
            gaps=tuple(PlanGap.from_document(g) for g in doc.get("gaps", ())),
            constraints=constraints,
        )
        if doc.get("plan_id") not in (None, plan.plan_id):
            raise IntegrityError(f"plan document names {doc['plan_id']} but hashes to {plan.plan_id}")
        return replace(plan, approval=PlanApproval.from_document(approval)) if approval else plan


def select_items(
    requirements: Sequence[EvidenceRequirement],
    matches: Sequence[CapabilityMatch],
    candidates: Sequence[PlanItem],
) -> tuple[tuple[PlanItem, ...], tuple[PlanGap, ...]]:
    """The minimum product set: the candidates whose source satisfies, or partly satisfies, the
    requirement they would serve, each acquisition once, and a gap for every requirement left
    without one (v0.4.0 §5).

    Pure and deterministic, so the harness re-derives a plan's items from the requirements,
    matches and candidates it records (``rederive-plan``). A candidate serving several requirements
    is planned once, under the first requirement in the plan's order; item ids are assigned after
    sorting by source, operation and target.
    """
    order = {r.requirement_id: n for n, r in enumerate(requirements)}
    status = {(m.source_id, m.requirement_id): m.status for m in matches}
    usable = (MatchStatus.SATISFIED, MatchStatus.PARTIAL)
    chosen: dict[tuple[str, str, str], PlanItem] = {}
    served: set[str] = set()
    for c in sorted(candidates, key=lambda c: (order.get(c.requirement_id, len(order)), c.source_id, c.target)):
        if c.requirement_id not in order or status.get((c.source_id, c.requirement_id)) not in usable:
            continue
        served.add(c.requirement_id)
        chosen.setdefault((c.source_id, c.operation.value, c.target), c)
    items = tuple(replace(c, item_id=f"item-{n:03d}") for n, (_, c) in enumerate(sorted(chosen.items()), start=1))
    gaps = []
    for r in requirements:
        if r.requirement_id in served:
            continue
        offered = sorted({c.source_id for c in candidates if c.requirement_id == r.requirement_id})
        if not offered:
            reason = "no source in the profile lists anything for it"
        else:
            reason = "no source that lists it satisfies it: " + ", ".join(
                f"{src} ({status[(src, r.requirement_id)].value})"
                if (src, r.requirement_id) in status
                else f"{src} (no registered descriptor)"
                for src in offered
            )
        gaps.append(PlanGap(r.requirement_id, reason))
    return items, tuple(gaps)


@dataclass
class PlanGate:
    """The executor's check on every acquisition one run makes under an approved plan (INT-015).

    An acquisition the plan names is authorised and its artifact recorded against the item; one it
    does not name is refused and the refusal kept, so the execution record says what was asked for
    and turned away as well as what was fetched.
    """

    plan: AcquisitionPlan
    acquired: dict[str, str] = dataclass_field(default_factory=dict)
    refused: list[dict[str, str]] = dataclass_field(default_factory=list)

    def __post_init__(self) -> None:
        if self.plan.approval is None or not self.plan.approval.approves:
            raise IntegrationRefused(f"plan {self.plan.plan_id} is not approved; nothing may be acquired under it")

    def authorise(self, source_id: str, operation: Operation, target: str) -> PlanItem | None:
        try:
            return self.plan.authorise(source_id, operation, target)
        except IntegrationRefused as exc:
            self.refused.append(
                {"source_id": source_id, "operation": operation.value, "target": target, "reason": str(exc)}
            )
            return None

    def record(self, item: PlanItem, artifact_id: str) -> None:
        self.acquired.setdefault(item.item_id, artifact_id)

    @property
    def unavailable(self) -> tuple[str, ...]:
        return tuple(i.item_id for i in self.plan.items if i.item_id not in self.acquired)


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


def require_admitted(
    records: Sequence[AdapterCompatibilityRecord], used: Iterable[tuple[str, str, ProductType]]
) -> None:
    """Refuse a verdict when any (adapter, version, product type) used lacks a passing record, or has
    a failing one beside it (INT-016). A record for another version of the same adapter admits nothing."""
    passing = {(r.adapter, r.adapter_version, r.product_type) for r in records if r.passed}
    failing = {(r.adapter, r.adapter_version, r.product_type) for r in records if not r.passed}
    triples = set(used)
    missing = sorted((a, v, t.value) for a, v, t in triples - passing)
    conflicting = sorted((a, v, t.value) for a, v, t in triples & passing & failing)

    def named(found: list[tuple[str, str, str]]) -> str:
        return ", ".join(f"{a} {v} for {t}" for a, v, t in found)

    problems = [f"no passing adapter compatibility record: {named(missing)}"] if missing else []
    if conflicting:
        problems.append(f"a failing adapter compatibility record contradicts the passing one: {named(conflicting)}")
    if problems:
        raise IntegrationRefused(f"{'; '.join(problems)}; no verdict (INT-016)")
