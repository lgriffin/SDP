"""Investigate (UC-03): Field, TestProfile, ObjectSet, EvidenceBundle, TestResult and the
Investigation aggregate (Investigate doc §1–§6, spec §5).

Investigate is exploratory by construction. An Investigation never carries a Verdict; every
TestResult is labelled EXPLORATORY; promotion to a confirmatory claim goes only through a frozen
predeclaration that names evidence the investigation did not use (INV-008, INV-009).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from sdp.kernel.domain import events as ev
from sdp.kernel.domain.archive import Box, Cone, Filter, region_from_document
from sdp.kernel.domain.canonical import hash_document
from sdp.kernel.domain.errors import InvalidTransition, InvariantViolation, PromotionRefused
from sdp.kernel.domain.ids import ArtifactId, check_semver
from sdp.kernel.domain.predeclaration import FrozenPredeclaration

EXPLORATORY = "EXPLORATORY"
SCHEMA_VERSION = "1.0.0"

_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _slug(value: str, what: str) -> str:
    if not _SLUG.match(value):
        raise InvariantViolation(f"{what} must be a lowercase slug: {value!r}")
    return value


def _finite(value: float | None, what: str) -> float | None:
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        raise InvariantViolation(f"{what} must be finite")
    return value


def _jsonable(value: Any) -> Any:
    """Plain JSON values only; NaN and infinity become null so canonical JSON can carry them."""
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item") and callable(value.item):  # numpy scalars
        return _jsonable(value.item())
    return value


# ---------------------------------------------------------------------------------------------
# Field


@dataclass(frozen=True)
class Polygon:
    """A spherical polygon given by its ICRS vertices in degrees (at least three)."""

    vertices: tuple[tuple[float, float], ...]
    shape: Literal["polygon"] = "polygon"

    def __post_init__(self) -> None:
        if len(self.vertices) < 3:
            raise InvariantViolation("a polygon needs at least three vertices")
        for ra, dec in self.vertices:
            if not (0 <= ra < 360 and -90 <= dec <= 90):
                raise InvariantViolation(f"polygon vertex out of range: ({ra}, {dec})")
        if self.bounding_cone().radius_deg > 10:
            raise InvariantViolation("polygon wider than a 10 degree cone; fields must stay bounded")

    def _plane(self) -> tuple[float, float, list[tuple[float, float]]]:
        ra0 = (
            math.degrees(
                math.atan2(
                    sum(math.sin(math.radians(r)) for r, _ in self.vertices),
                    sum(math.cos(math.radians(r)) for r, _ in self.vertices),
                )
            )
            % 360.0
        )
        dec0 = sum(d for _, d in self.vertices) / len(self.vertices)
        return ra0, dec0, [_tangent(ra, dec, ra0, dec0) for ra, dec in self.vertices]

    def bounding_cone(self) -> Cone:
        ra0, dec0, _ = self._plane()
        radius = max(_separation_deg(ra0, dec0, ra, dec) for ra, dec in self.vertices)
        return Cone(ra0, dec0, min(10.0, max(radius * 1.0001, 1e-6)))

    def contains(self, ra: float, dec: float) -> bool:
        ra0, dec0, poly = self._plane()
        if _separation_deg(ra0, dec0, ra, dec) >= 90:
            return False
        x, y = _tangent(ra, dec, ra0, dec0)
        inside = False
        j = len(poly) - 1
        for i in range(len(poly)):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside

    def to_document(self) -> dict[str, Any]:
        return {"shape": "polygon", "vertices": [[ra, dec] for ra, dec in self.vertices]}


FieldRegion = Cone | Box | Polygon


def _separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    h = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h))))


def _tangent(ra: float, dec: float, ra0: float, dec0: float) -> tuple[float, float]:
    """Gnomonic projection about (ra0, dec0), in degrees."""
    r, d, r0, d0 = map(math.radians, (ra, dec, ra0, dec0))
    cos_c = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(r - r0)
    x = math.cos(d) * math.sin(r - r0) / cos_c
    y = (math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(r - r0)) / cos_c
    return math.degrees(x), math.degrees(y)


def region_contains(region: FieldRegion, ra: float, dec: float) -> bool:
    if isinstance(region, Cone):
        return _separation_deg(region.ra_deg, region.dec_deg, ra, dec) <= region.radius_deg
    if isinstance(region, Box):
        return region.ra_min_deg <= ra <= region.ra_max_deg and region.dec_min_deg <= dec <= region.dec_max_deg
    return region.contains(ra, dec)


def region_centre(region: FieldRegion) -> tuple[float, float]:
    if isinstance(region, Cone):
        return region.ra_deg, region.dec_deg
    if isinstance(region, Box):
        return region.centre
    cone = region.bounding_cone()
    return cone.ra_deg, cone.dec_deg


def field_region_from_document(doc: Mapping[str, Any]) -> FieldRegion:
    if doc.get("shape") == "polygon":
        return Polygon(tuple((float(ra), float(dec)) for ra, dec in doc["vertices"]))
    return region_from_document(doc)


_REGION_TEXT = re.compile(r"^\s*(?:(icrs|fk5)\s*;\s*)?(circle|box|polygon)\s*\(([^)]*)\)\s*$", re.IGNORECASE)


def parse_region(text: str) -> FieldRegion:
    """Parse the DS9-style text the CLI takes: ``icrs;circle(ra,dec,r)``, ``icrs;box(ra,dec,w,h)``
    or ``icrs;polygon(ra1,dec1,ra2,dec2,...)``, all in degrees."""
    m = _REGION_TEXT.match(text)
    if not m:
        raise InvariantViolation(f"not a region: {text!r}; expected e.g. icrs;circle(120.0,-10.0,0.5)")
    shape = m.group(2).lower()
    try:
        values = [float(v.strip().rstrip("d")) for v in m.group(3).split(",")]
    except ValueError:
        raise InvariantViolation(f"region values must be numbers in degrees: {text!r}") from None
    if shape == "circle":
        if len(values) != 3:
            raise InvariantViolation("circle takes ra, dec, radius")
        return Cone(values[0] % 360.0, values[1], values[2])
    if shape == "box":
        if len(values) != 4:
            raise InvariantViolation("box takes ra, dec, width, height")
        ra, dec, width, height = values
        half_ra = width / 2 / max(math.cos(math.radians(dec)), 1e-6)
        return Box(ra - half_ra, ra + half_ra, dec - height / 2, dec + height / 2)
    if len(values) < 6 or len(values) % 2:
        raise InvariantViolation("polygon takes at least three ra, dec pairs")
    return Polygon(tuple((values[i] % 360.0, values[i + 1]) for i in range(0, len(values), 2)))


@dataclass(frozen=True)
class FieldImage:
    """An image of the field: its bytes and, when the WCS lives apart from it, the WCS artifact."""

    image_id: ArtifactId
    wcs_id: ArtifactId | None = None
    band: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id.value,
            "wcs_id": self.wcs_id.value if self.wcs_id else None,
            "band": self.band,
        }


@dataclass(frozen=True)
class Field:
    """A sky region (cone, box or polygon) with optional images and an epoch (Investigate §1)."""

    region: FieldRegion
    epoch_jyear: float = 2016.0
    images: tuple[FieldImage, ...] = ()
    label: str = ""

    def __post_init__(self) -> None:
        if not (1800.0 <= self.epoch_jyear <= 2200.0):
            raise InvariantViolation(f"field epoch {self.epoch_jyear} is not a plausible Julian year")

    @property
    def field_id(self) -> str:
        return "field-" + hash_document(self.to_document()).removeprefix("sha256:")[:16]

    def contains(self, ra: float, dec: float) -> bool:
        return region_contains(self.region, ra, dec)

    def query_region(self, margin_deg: float = 0.0) -> Cone | Box:
        """The bounded region a catalogue query uses: the field itself, or its bounding cone."""
        if isinstance(self.region, Polygon):
            cone = self.region.bounding_cone()
        elif isinstance(self.region, Box):
            if not margin_deg:
                return self.region
            r = self.region
            return Box(
                r.ra_min_deg - margin_deg,
                r.ra_max_deg + margin_deg,
                r.dec_min_deg - margin_deg,
                r.dec_max_deg + margin_deg,
            )
        else:
            cone = self.region
        return Cone(cone.ra_deg, cone.dec_deg, min(10.0, cone.radius_deg + margin_deg))

    def to_document(self) -> dict[str, Any]:
        return {
            "region": self.region.to_document(),
            "epoch_jyear": self.epoch_jyear,
            "images": [i.to_document() for i in self.images],
            "label": self.label,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Field:
        return cls(
            region=field_region_from_document(doc["region"]),
            epoch_jyear=float(doc.get("epoch_jyear", 2016.0)),
            images=tuple(
                FieldImage(
                    ArtifactId(i["image_id"]), ArtifactId(i["wcs_id"]) if i.get("wcs_id") else None, i.get("band")
                )
                for i in doc.get("images", [])
            ),
            label=doc.get("label", ""),
        )


# ---------------------------------------------------------------------------------------------
# Test profile


class IdentifyRoute(StrEnum):
    CATALOGUE = "catalogue"
    IMAGE = "image"


class ArchiveRole(StrEnum):
    CATALOGUE = "catalogue"
    OBSERVATIONS = "observations"


@dataclass(frozen=True)
class IdentifySpec:
    """How Identify finds objects: the leading route, the Gaia cone and its recorded quality cuts."""

    route: IdentifyRoute
    source_id: str
    release_label: str
    columns: tuple[str, ...]
    quality_cuts: tuple[Filter, ...] = ()
    match_radius_arcsec: float = 1.0
    row_cap: int = 100_000
    resolve: bool = True
    resolver_radius_arcsec: float = 2.0

    def __post_init__(self) -> None:
        if not self.columns:
            raise InvariantViolation("Identify names the catalogue columns it reads")
        if not (0 < self.match_radius_arcsec <= 60):
            raise InvariantViolation("match radius must be in (0, 60] arcsec")

    def to_document(self) -> dict[str, Any]:
        return {
            "route": self.route.value,
            "source_id": self.source_id,
            "release_label": self.release_label,
            "columns": list(self.columns),
            "quality_cuts": [f.to_document() for f in self.quality_cuts],
            "match_radius_arcsec": self.match_radius_arcsec,
            "row_cap": self.row_cap,
            "resolve": self.resolve,
            "resolver_radius_arcsec": self.resolver_radius_arcsec,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> IdentifySpec:
        return cls(
            route=IdentifyRoute(doc.get("route", "catalogue")),
            source_id=doc["source_id"],
            release_label=doc["release_label"],
            columns=tuple(doc["columns"]),
            quality_cuts=tuple(Filter(f["column"], f["op"], f.get("value")) for f in doc.get("quality_cuts", [])),
            match_radius_arcsec=float(doc.get("match_radius_arcsec", 1.0)),
            row_cap=int(doc.get("row_cap", 100_000)),
            resolve=bool(doc.get("resolve", True)),
            resolver_radius_arcsec=float(doc.get("resolver_radius_arcsec", 2.0)),
        )


@dataclass(frozen=True)
class ArchiveSelection:
    """One archive the profile consults in Retrieve, and how."""

    source_id: str
    role: ArchiveRole
    release_label: str
    columns: tuple[str, ...] = ()
    match_radius_arcsec: float = 1.0
    missions: tuple[str, ...] = ()
    product_types: tuple[str, ...] = ()
    cutout_arcsec: float | None = None
    """Fetch a square cutout of this side around the target instead of the whole product, where the
    product supports one (INT-004). ``None`` fetches whole products."""

    def __post_init__(self) -> None:
        if self.role is ArchiveRole.CATALOGUE and not self.columns:
            raise InvariantViolation(f"catalogue archive {self.source_id} must name its columns")
        if self.cutout_arcsec is not None and not 0 < self.cutout_arcsec <= 3600:
            raise InvariantViolation("a cutout side is more than 0 and at most 3600 arcsec")

    def to_document(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "role": self.role.value,
            "release_label": self.release_label,
            "columns": list(self.columns),
            "match_radius_arcsec": self.match_radius_arcsec,
            "missions": list(self.missions),
            "product_types": list(self.product_types),
            # Only when set, so profiles written before cutouts keep their hash.
            **({"cutout_arcsec": self.cutout_arcsec} if self.cutout_arcsec is not None else {}),
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ArchiveSelection:
        return cls(
            source_id=doc["source_id"],
            role=ArchiveRole(doc["role"]),
            release_label=doc["release_label"],
            columns=tuple(doc.get("columns", ())),
            match_radius_arcsec=float(doc.get("match_radius_arcsec", 1.0)),
            missions=tuple(doc.get("missions", ())),
            product_types=tuple(doc.get("product_types", ())),
            cutout_arcsec=float(doc["cutout_arcsec"]) if doc.get("cutout_arcsec") is not None else None,
        )


@dataclass(frozen=True)
class TestSelection:
    """A test by name and version, with its thresholds; the null model is the registered test's."""

    __test__ = False  # not a pytest class

    name: str
    version: str
    thresholds: Mapping[str, Any] = dataclass_field(default_factory=dict)
    needs_products: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _slug(self.name, "test name")
        check_semver(self.version)

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def to_document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "thresholds": _jsonable(dict(self.thresholds)),
            "needs_products": list(self.needs_products),
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> TestSelection:
        return cls(
            name=doc["name"],
            version=doc["version"],
            thresholds=dict(doc.get("thresholds", {})),
            needs_products=tuple(doc.get("needs_products", ())),
        )


@dataclass(frozen=True)
class RankingPolicy:
    """Versioned like a test; never silently changes between runs (Investigate §6)."""

    policy_id: str
    version: str
    threshold: float
    alpha: float = 0.01

    def __post_init__(self) -> None:
        _slug(self.policy_id, "ranking policy id")
        check_semver(self.version)
        if not (0 < self.alpha < 1):
            raise InvariantViolation("alpha must be in (0, 1)")

    def to_document(self) -> dict[str, Any]:
        return {"policy_id": self.policy_id, "version": self.version, "threshold": self.threshold, "alpha": self.alpha}

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> RankingPolicy:
        return cls(doc["policy_id"], doc["version"], float(doc["threshold"]), float(doc.get("alpha", 0.01)))


@dataclass(frozen=True)
class NullFieldPlan:
    """How many shuffled and synthetic-injection versions of the field each test also runs on."""

    trials: int = 20

    def __post_init__(self) -> None:
        if self.trials < 1:
            raise InvariantViolation("every profile runs at least one null field (Investigate §2)")

    def to_document(self) -> dict[str, Any]:
        return {"trials": self.trials}


@dataclass(frozen=True)
class TestProfile:
    """A versioned document naming the tests, thresholds, null fields and archives (INV-001).

    Its hash is recorded before anything runs, so a run cannot be tuned against its own output;
    a changed profile is a new version and a new run.
    """

    __test__ = False

    profile_id: str
    version: str
    identify: IdentifySpec
    tests: tuple[TestSelection, ...]
    ranking: RankingPolicy
    archives: tuple[ArchiveSelection, ...] = ()
    null_fields: NullFieldPlan = dataclass_field(default_factory=NullFieldPlan)
    description: str = ""

    def __post_init__(self) -> None:
        _slug(self.profile_id, "profile id")
        check_semver(self.version)
        if not self.tests:
            raise InvariantViolation("a profile runs at least one test")
        names = [t.name for t in self.tests]
        if len(names) != len(set(names)):
            raise InvariantViolation("a profile selects each test once")
        sources = [a.source_id for a in self.archives]
        if len(sources) != len(set(sources)):
            raise InvariantViolation("a profile lists each archive once")

    @property
    def profile_hash(self) -> str:
        return hash_document(self.to_document())

    def archive(self, source_id: str) -> ArchiveSelection:
        for a in self.archives:
            if a.source_id == source_id:
                return a
        raise KeyError(source_id)

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "version": self.version,
            "description": self.description,
            "identify": self.identify.to_document(),
            "archives": [a.to_document() for a in self.archives],
            "tests": [t.to_document() for t in self.tests],
            "ranking": self.ranking.to_document(),
            "null_fields": self.null_fields.to_document(),
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> TestProfile:
        return cls(
            profile_id=doc["profile_id"],
            version=doc["version"],
            description=doc.get("description", ""),
            identify=IdentifySpec.from_document(doc["identify"]),
            archives=tuple(ArchiveSelection.from_document(a) for a in doc.get("archives", [])),
            tests=tuple(TestSelection.from_document(t) for t in doc["tests"]),
            ranking=RankingPolicy.from_document(doc["ranking"]),
            null_fields=NullFieldPlan(int(doc.get("null_fields", {}).get("trials", 20))),
        )


# ---------------------------------------------------------------------------------------------
# Identify


class IdentificationMethod(StrEnum):
    CATALOGUE = "catalogue"
    DETECTION = "detection"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class ResolverRecord:
    """What SIMBAD (or its fixture fake) says a position or name is."""

    main_id: str
    object_type: str
    bibcodes: tuple[str, ...] = ()
    ra_deg: float | None = None
    dec_deg: float | None = None
    separation_arcsec: float | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "main_id": self.main_id,
            "object_type": self.object_type,
            "bibcodes": list(self.bibcodes),
            "ra_deg": self.ra_deg,
            "dec_deg": self.dec_deg,
            "separation_arcsec": self.separation_arcsec,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ResolverRecord:
        return cls(
            main_id=doc["main_id"],
            object_type=doc["object_type"],
            bibcodes=tuple(doc.get("bibcodes", ())),
            ra_deg=doc.get("ra_deg"),
            dec_deg=doc.get("dec_deg"),
            separation_arcsec=doc.get("separation_arcsec"),
        )


UNMATCHED = "unmatched"


@dataclass(frozen=True)
class IdentifiedObject:
    """One row of the ObjectSet: every object says how it was found (INV-003)."""

    object_key: str
    method: IdentificationMethod
    ra_deg: float
    dec_deg: float
    source_artifacts: tuple[ArtifactId, ...]
    gaia_source_key: str | None = None
    pixel_xy: tuple[float, float] | None = None
    wcs_artifact: ArtifactId | None = None
    image_artifact: ArtifactId | None = None
    match_radius_arcsec: float | None = None
    match_distance_arcsec: float | None = None
    resolver_record: ResolverRecord | None = None
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.object_key:
            raise InvariantViolation("an identified object needs a key")
        if not self.source_artifacts:
            raise InvariantViolation(f"{self.object_key}: an identification names the artifacts it came from")
        if self.pixel_xy is not None and self.wcs_artifact is None:
            raise InvariantViolation(f"{self.object_key}: pixel positions travel with their WCS artifact")
        if self.method is IdentificationMethod.CATALOGUE and not self.gaia_source_key:
            raise InvariantViolation(f"{self.object_key}: a catalogue identification is a Gaia source key")
        if self.method is IdentificationMethod.DETECTION:
            if self.match_radius_arcsec is None:
                raise InvariantViolation(f"{self.object_key}: a detection records its declared match radius")
            if self.gaia_source_key is None and UNMATCHED not in self.flags:
                raise InvariantViolation(f"{self.object_key}: an unmatched detection is flagged, not dropped")
            if self.gaia_source_key is not None and (
                self.match_distance_arcsec is None or self.match_distance_arcsec > self.match_radius_arcsec
            ):
                raise InvariantViolation(f"{self.object_key}: a match lies within its declared radius")

    @property
    def unmatched(self) -> bool:
        return UNMATCHED in self.flags

    def with_resolution(self, record: ResolverRecord | None) -> IdentifiedObject:
        return IdentifiedObject(
            object_key=self.object_key,
            method=self.method,
            ra_deg=self.ra_deg,
            dec_deg=self.dec_deg,
            source_artifacts=self.source_artifacts,
            gaia_source_key=self.gaia_source_key,
            pixel_xy=self.pixel_xy,
            wcs_artifact=self.wcs_artifact,
            image_artifact=self.image_artifact,
            match_radius_arcsec=self.match_radius_arcsec,
            match_distance_arcsec=self.match_distance_arcsec,
            resolver_record=record,
            flags=self.flags,
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "object_key": self.object_key,
            "identification_method": self.method.value,
            "sky_icrs": [self.ra_deg, self.dec_deg],
            "pixel_xy": list(self.pixel_xy) if self.pixel_xy else None,
            "wcs_artifact": self.wcs_artifact.value if self.wcs_artifact else None,
            "image_artifact": self.image_artifact.value if self.image_artifact else None,
            "gaia_source_key": self.gaia_source_key,
            "match_radius_arcsec": self.match_radius_arcsec,
            "match_distance_arcsec": self.match_distance_arcsec,
            "resolver_record": self.resolver_record.to_document() if self.resolver_record else None,
            "source_artifacts": [a.value for a in self.source_artifacts],
            "flags": list(self.flags),
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> IdentifiedObject:
        pixel = doc.get("pixel_xy")
        return cls(
            object_key=doc["object_key"],
            method=IdentificationMethod(doc["identification_method"]),
            ra_deg=float(doc["sky_icrs"][0]),
            dec_deg=float(doc["sky_icrs"][1]),
            source_artifacts=tuple(ArtifactId(a) for a in doc["source_artifacts"]),
            gaia_source_key=doc.get("gaia_source_key"),
            pixel_xy=(float(pixel[0]), float(pixel[1])) if pixel else None,
            wcs_artifact=ArtifactId(doc["wcs_artifact"]) if doc.get("wcs_artifact") else None,
            image_artifact=ArtifactId(doc["image_artifact"]) if doc.get("image_artifact") else None,
            match_radius_arcsec=doc.get("match_radius_arcsec"),
            match_distance_arcsec=doc.get("match_distance_arcsec"),
            resolver_record=ResolverRecord.from_document(doc["resolver_record"])
            if doc.get("resolver_record")
            else None,
            flags=tuple(doc.get("flags", ())),
        )


@dataclass(frozen=True)
class ObjectSet:
    field_id: str
    objects: tuple[IdentifiedObject, ...]

    def __post_init__(self) -> None:
        keys = [o.object_key for o in self.objects]
        if len(keys) != len(set(keys)):
            raise InvariantViolation("object keys are unique within a set")

    def keys(self) -> tuple[str, ...]:
        return tuple(o.object_key for o in self.objects)

    def to_document(self) -> dict[str, Any]:
        return {"field_id": self.field_id, "objects": [o.to_document() for o in self.objects]}

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ObjectSet:
        return cls(doc["field_id"], tuple(IdentifiedObject.from_document(o) for o in doc["objects"]))


# ---------------------------------------------------------------------------------------------
# Retrieve


@dataclass(frozen=True)
class ProductRef:
    """One archive product: the archive, its product id, the URI it is served from and, when the
    archive also serves it from the cloud, that URI (INV-013)."""

    archive: str
    product_id: str
    uri: str
    cloud_uri: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {"archive": self.archive, "product_id": self.product_id, "uri": self.uri, "cloud_uri": self.cloud_uri}

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ProductRef:
        return cls(doc["archive"], doc["product_id"], doc["uri"], doc.get("cloud_uri"))


@dataclass(frozen=True)
class ObservationCriteria:
    missions: tuple[str, ...] = ()
    product_types: tuple[str, ...] = ()
    radius_arcsec: float = 5.0
    epoch_min_mjd: float | None = None
    epoch_max_mjd: float | None = None

    def __post_init__(self) -> None:
        if not (0 < self.radius_arcsec <= 3600):
            raise InvariantViolation("an observation search radius stays within a degree")

    def to_document(self) -> dict[str, Any]:
        return {
            "missions": list(self.missions),
            "product_types": list(self.product_types),
            "radius_arcsec": self.radius_arcsec,
            "epoch_min_mjd": self.epoch_min_mjd,
            "epoch_max_mjd": self.epoch_max_mjd,
        }


@dataclass(frozen=True)
class ObservationRecord:
    """Metadata only: mission, instrument, epoch, product type, size, licence and URIs."""

    product: ProductRef
    mission: str
    instrument: str
    product_type: str
    epoch_mjd: float | None
    size_bytes: int | None
    licence: str
    citation: str
    epoch_end_mjd: float | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "product": self.product.to_document(),
            "mission": self.mission,
            "instrument": self.instrument,
            "product_type": self.product_type,
            "epoch_mjd": self.epoch_mjd,
            "epoch_end_mjd": self.epoch_end_mjd,
            "size_bytes": self.size_bytes,
            "licence": self.licence,
            "citation": self.citation,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ObservationRecord:
        return cls(
            product=ProductRef.from_document(doc["product"]),
            mission=doc["mission"],
            instrument=doc["instrument"],
            product_type=doc["product_type"],
            epoch_mjd=doc.get("epoch_mjd"),
            epoch_end_mjd=doc.get("epoch_end_mjd"),
            size_bytes=doc.get("size_bytes"),
            licence=doc["licence"],
            citation=doc["citation"],
        )


@dataclass(frozen=True)
class ObservationIndex:
    """What one archive holds for a target, as metadata; cheap, and always fetched (Investigate §4)."""

    archive: str
    release_label: str
    query_text: str
    target: str
    records: tuple[ObservationRecord, ...] = ()
    licence: str = ""
    citation: str = ""

    @property
    def reach_back_mjd(self) -> float | None:
        epochs = [r.epoch_mjd for r in self.records if r.epoch_mjd is not None]
        return min(epochs) if epochs else None

    def to_document(self) -> dict[str, Any]:
        return {
            "archive": self.archive,
            "release_label": self.release_label,
            "query_text": self.query_text,
            "target": self.target,
            "licence": self.licence,
            "citation": self.citation,
            "records": [r.to_document() for r in self.records],
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ObservationIndex:
        return cls(
            archive=doc["archive"],
            release_label=doc["release_label"],
            query_text=doc["query_text"],
            target=doc["target"],
            records=tuple(ObservationRecord.from_document(r) for r in doc.get("records", [])),
            licence=doc.get("licence", ""),
            citation=doc.get("citation", ""),
        )


@dataclass(frozen=True)
class FetchedProduct:
    """Bytes as actually read, and where they were read from (INV-013)."""

    product: ProductRef
    data: bytes
    media_type: str
    release_label: str
    licence: str
    citation: str
    read_from: str

    def __post_init__(self) -> None:
        if self.read_from not in (self.product.uri, self.product.cloud_uri):
            raise InvariantViolation("a product is read from its archive URI or its cloud URI")


@dataclass(frozen=True)
class ProductEvidence:
    """A product a test may read. ``artifact_id`` is what the test reads: the curated child when a
    product dialect curated the product, else the raw product itself (``raw_artifact_id`` is then None)."""

    product: ProductRef
    product_type: str
    artifact_id: ArtifactId
    read_from: str
    raw_artifact_id: ArtifactId | None = None
    envelope_artifact_id: ArtifactId | None = None
    """The product's ScientificDataProduct envelope with its validation report, when the source has a
    normaliser (Integration v0.4.0 §3)."""

    def to_document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "product": self.product.to_document(),
            "product_type": self.product_type,
            "artifact_id": self.artifact_id.value,
            "read_from": self.read_from,
        }
        if self.raw_artifact_id is not None:
            doc["raw_artifact_id"] = self.raw_artifact_id.value
        if self.envelope_artifact_id is not None:
            doc["envelope_artifact_id"] = self.envelope_artifact_id.value
        return doc

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ProductEvidence:
        return cls(
            ProductRef.from_document(doc["product"]),
            doc["product_type"],
            ArtifactId(doc["artifact_id"]),
            doc["read_from"],
            ArtifactId(doc["raw_artifact_id"]) if doc.get("raw_artifact_id") else None,
            ArtifactId(doc["envelope_artifact_id"]) if doc.get("envelope_artifact_id") else None,
        )


@dataclass(frozen=True)
class EvidenceBundle:
    """What the archives hold about one object: the only thing a test may read (Investigate §4–5).

    ``catalogue_rows`` maps a source id to the canonical rows matched to the object;
    ``catalogue_artifacts`` maps the same source id to the registered DatasetArtifact the rows
    came from (INV-005).
    """

    object: IdentifiedObject
    catalogue_rows: Mapping[str, tuple[Mapping[str, Any], ...]]
    catalogue_artifacts: Mapping[str, ArtifactId]
    observation_indices: tuple[ObservationIndex, ...] = ()
    observation_artifacts: tuple[ArtifactId, ...] = ()
    products: tuple[ProductEvidence, ...] = ()
    releases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        unregistered = sorted(set(self.catalogue_rows) - set(self.catalogue_artifacts))
        if unregistered:
            raise InvariantViolation(f"rows from {unregistered} have no registered DatasetArtifact (INV-005)")
        if len(self.observation_artifacts) != len(self.observation_indices):
            raise InvariantViolation("every observation index is registered as a DatasetArtifact (INV-005)")

    @property
    def object_key(self) -> str:
        return self.object.object_key

    @property
    def bundle_id(self) -> str:
        return "bundle-" + hash_document(self.to_document()).removeprefix("sha256:")[:24]

    def rows(self, source_id: str) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.catalogue_rows.get(source_id, ()))

    def artifact_ids(self) -> tuple[ArtifactId, ...]:
        ids = [*self.catalogue_artifacts.values(), *self.observation_artifacts, *(p.artifact_id for p in self.products)]
        return tuple(dict.fromkeys(ids))

    def to_document(self) -> dict[str, Any]:
        return {
            "object": self.object.to_document(),
            "catalogue_rows": {k: [_jsonable(dict(r)) for r in v] for k, v in sorted(self.catalogue_rows.items())},
            "catalogue_artifacts": {k: v.value for k, v in sorted(self.catalogue_artifacts.items())},
            "observation_indices": [i.to_document() for i in self.observation_indices],
            "observation_artifacts": [a.value for a in self.observation_artifacts],
            "products": [p.to_document() for p in self.products],
            "releases": sorted(self.releases),
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> EvidenceBundle:
        return cls(
            object=IdentifiedObject.from_document(doc["object"]),
            catalogue_rows={k: tuple(dict(r) for r in v) for k, v in doc.get("catalogue_rows", {}).items()},
            catalogue_artifacts={k: ArtifactId(v) for k, v in doc.get("catalogue_artifacts", {}).items()},
            observation_indices=tuple(ObservationIndex.from_document(i) for i in doc.get("observation_indices", [])),
            observation_artifacts=tuple(ArtifactId(a) for a in doc.get("observation_artifacts", [])),
            products=tuple(ProductEvidence.from_document(p) for p in doc.get("products", [])),
            releases=tuple(doc.get("releases", ())),
        )


# ---------------------------------------------------------------------------------------------
# Test


@dataclass(frozen=True)
class Subject:
    """What a result is about: one object, or one structure with members — never conflated."""

    object_key: str | None = None
    structure_id: str | None = None
    members: tuple[str, ...] = ()
    membership: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if (self.object_key is None) == (self.structure_id is None):
            raise InvariantViolation("a subject is either one object or one structure")
        if self.structure_id is not None:
            if len(self.members) < 2:
                raise InvariantViolation("a structure lists at least two members")
            if self.membership and len(self.membership) != len(self.members):
                raise InvariantViolation("one membership probability per member")
            if any(not (0 <= p <= 1) for p in self.membership):
                raise InvariantViolation("membership probabilities lie in [0, 1]")
        elif self.members:
            raise InvariantViolation("an object subject has no members")

    @property
    def key(self) -> str:
        return self.object_key if self.object_key is not None else f"structure:{self.structure_id}"

    @classmethod
    def of_object(cls, object_key: str) -> Subject:
        return cls(object_key=object_key)

    def to_document(self) -> dict[str, Any]:
        return {
            "object_key": self.object_key,
            "structure_id": self.structure_id,
            "members": list(self.members),
            "membership": list(self.membership),
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Subject:
        return cls(
            object_key=doc.get("object_key"),
            structure_id=doc.get("structure_id"),
            members=tuple(doc.get("members", ())),
            membership=tuple(float(p) for p in doc.get("membership", ())),
        )


@dataclass(frozen=True)
class TestResult:
    """Statistic, uncertainty, p-value or posterior summary, effect size and seed (Investigate §5).

    Interpretation is left to ranking and triage; the label is always EXPLORATORY (INV-008).
    """

    __test__ = False

    test_name: str
    test_version: str
    subject: Subject
    statistic: float
    seed: int
    uncertainty: float | None = None
    p_value: float | None = None
    effect_size: float | None = None
    posterior: Mapping[str, float] | None = None
    details: Mapping[str, Any] = dataclass_field(default_factory=dict)
    label: str = EXPLORATORY

    def __post_init__(self) -> None:
        if self.label != EXPLORATORY:
            raise InvariantViolation("investigation results are EXPLORATORY and never carry a verdict (INV-008)")
        _finite(self.statistic, "statistic")
        _finite(self.uncertainty, "uncertainty")
        _finite(self.effect_size, "effect size")
        if self.p_value is not None and not (0.0 <= self.p_value <= 1.0):
            raise InvariantViolation("a p-value lies in [0, 1]")

    @property
    def test_key(self) -> str:
        return f"{self.test_name}@{self.test_version}"

    def to_document(self) -> dict[str, Any]:
        return {
            "test_name": self.test_name,
            "test_version": self.test_version,
            "subject": self.subject.to_document(),
            "statistic": self.statistic,
            "uncertainty": self.uncertainty,
            "p_value": self.p_value,
            "effect_size": self.effect_size,
            "posterior": _jsonable(dict(self.posterior)) if self.posterior is not None else None,
            "seed": self.seed,
            "details": _jsonable(dict(self.details)),
            "label": self.label,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> TestResult:
        return cls(
            test_name=doc["test_name"],
            test_version=doc["test_version"],
            subject=Subject.from_document(doc["subject"]),
            statistic=float(doc["statistic"]),
            seed=int(doc["seed"]),
            uncertainty=doc.get("uncertainty"),
            p_value=doc.get("p_value"),
            effect_size=doc.get("effect_size"),
            posterior=doc.get("posterior"),
            details=dict(doc.get("details", {})),
            label=doc.get("label", EXPLORATORY),
        )


@dataclass(frozen=True)
class NullFieldResult:
    """A profile's empirical false-positive rate for one test on this field (Investigate §2)."""

    test_name: str
    test_version: str
    trials: int
    examined: int
    false_positives: int
    alpha: float
    null_model: str

    def __post_init__(self) -> None:
        if self.trials < 1 or self.examined < 0 or self.false_positives < 0:
            raise InvariantViolation("null-field counts are non-negative and cover at least one trial")
        if self.false_positives > self.examined:
            raise InvariantViolation("false positives cannot exceed what was examined")

    @property
    def false_positive_rate(self) -> float:
        return self.false_positives / self.examined if self.examined else 0.0

    def to_document(self) -> dict[str, Any]:
        return {
            "test_name": self.test_name,
            "test_version": self.test_version,
            "trials": self.trials,
            "examined": self.examined,
            "false_positives": self.false_positives,
            "false_positive_rate": self.false_positive_rate,
            "alpha": self.alpha,
            "null_model": self.null_model,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> NullFieldResult:
        return cls(
            test_name=doc["test_name"],
            test_version=doc["test_version"],
            trials=int(doc["trials"]),
            examined=int(doc["examined"]),
            false_positives=int(doc["false_positives"]),
            alpha=float(doc["alpha"]),
            null_model=doc["null_model"],
        )


# ---------------------------------------------------------------------------------------------
# Candidates


@dataclass(frozen=True)
class Rank:
    score: float
    policy_id: str
    policy_version: str

    def to_document(self) -> dict[str, Any]:
        return {"score": self.score, "policy_id": self.policy_id, "policy_version": self.policy_version}

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Rank:
        return cls(float(doc["score"]), doc["policy_id"], doc["policy_version"])


@dataclass(frozen=True)
class RankedCandidate:
    subject: Subject
    score: float
    results: tuple[TestResult, ...]


def rank_by_significance(results: Sequence[TestResult], policy: RankingPolicy) -> list[RankedCandidate]:
    """The kernel's reference ranking: the fraction of a subject's tests significant at ``alpha``.

    Every result is kept with its subject, including the ones that argue against it. Science
    modules may supply a richer CandidateRanker through the port; this one is what the kernel
    falls back to and what the fakes use.
    """
    by_subject: dict[str, list[TestResult]] = {}
    subjects: dict[str, Subject] = {}
    for r in results:
        by_subject.setdefault(r.subject.key, []).append(r)
        subjects.setdefault(r.subject.key, r.subject)
    ranked = []
    for key, rs in by_subject.items():
        judged = [r for r in rs if r.p_value is not None]
        score = (
            sum(1 for r in judged if r.p_value is not None and r.p_value < policy.alpha) / len(judged)
            if judged
            else 0.0
        )
        ranked.append(RankedCandidate(subjects[key], score, tuple(rs)))
    ranked.sort(key=lambda c: (-c.score, c.subject.key))
    return ranked


class InvestigationState(StrEnum):
    OPEN = "open"
    PARKED = "parked"
    PROMOTED = "promoted"
    CLOSED = "closed"


_ALLOWED: dict[InvestigationState, frozenset[InvestigationState]] = {
    InvestigationState.OPEN: frozenset(
        {InvestigationState.PARKED, InvestigationState.PROMOTED, InvestigationState.CLOSED}
    ),
    InvestigationState.PARKED: frozenset(
        {InvestigationState.OPEN, InvestigationState.PROMOTED, InvestigationState.CLOSED}
    ),
    # Promoted is read-only except for recording that the confirmation failed.
    InvestigationState.PROMOTED: frozenset({InvestigationState.CLOSED}),
    InvestigationState.CLOSED: frozenset(),
}


@dataclass(frozen=True)
class Transition:
    from_state: InvestigationState | None
    to_state: InvestigationState
    reason: str
    actor: str
    at: datetime

    def to_document(self) -> dict[str, Any]:
        return {
            "from": self.from_state.value if self.from_state else None,
            "to": self.to_state.value,
            "reason": self.reason,
            "actor": self.actor,
            "at": self.at.isoformat(),
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Transition:
        return cls(
            InvestigationState(doc["from"]) if doc.get("from") else None,
            InvestigationState(doc["to"]),
            doc["reason"],
            doc["actor"],
            datetime.fromisoformat(doc["at"]),
        )


@dataclass(frozen=True)
class Note:
    text: str
    author: str
    at: datetime

    def to_document(self) -> dict[str, Any]:
        return {"text": self.text, "author": self.author, "at": self.at.isoformat()}

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Note:
        return cls(doc["text"], doc["author"], datetime.fromisoformat(doc["at"]))


EvidenceKind = Literal["archive", "release", "epoch", "region"]
_EVIDENCE_KINDS = ("archive", "release", "epoch", "region")


@dataclass(frozen=True)
class EvidenceUsed:
    """What an investigation's exploration consumed, so promotion can demand something else."""

    archives: tuple[str, ...]
    releases: tuple[str, ...]
    region: Mapping[str, Any]
    epoch_min_jyear: float | None = None
    epoch_max_jyear: float | None = None

    def covers(self, kind: str, value: str) -> bool:
        if kind == "archive":
            return value in self.archives
        if kind == "release":
            return value in self.releases
        if kind == "epoch":
            epoch = float(value)
            if self.epoch_min_jyear is None or self.epoch_max_jyear is None:
                return False
            return self.epoch_min_jyear <= epoch <= self.epoch_max_jyear
        if kind == "region":
            ra, dec = region_centre(parse_region(value))
            return region_contains(field_region_from_document(self.region), ra, dec)
        raise InvariantViolation(f"unknown evidence kind {kind!r}; expected one of {_EVIDENCE_KINDS}")

    def to_document(self) -> dict[str, Any]:
        return {
            "archives": sorted(self.archives),
            "releases": sorted(self.releases),
            "region": dict(self.region),
            "epoch_min_jyear": self.epoch_min_jyear,
            "epoch_max_jyear": self.epoch_max_jyear,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> EvidenceUsed:
        return cls(
            tuple(doc.get("archives", ())),
            tuple(doc.get("releases", ())),
            dict(doc["region"]),
            doc.get("epoch_min_jyear"),
            doc.get("epoch_max_jyear"),
        )


CONFIRMATORY_EVIDENCE = "confirmatory_evidence"


def confirmatory_evidence(predeclaration: FrozenPredeclaration) -> tuple[tuple[str, str], ...]:
    """The evidence a confirmatory predeclaration names, from ``parameters.confirmatory_evidence``:
    a list of ``{"kind": archive|release|epoch|region, "value": ...}``."""
    raw = predeclaration.predeclaration.parameters.get(CONFIRMATORY_EVIDENCE, [])
    items = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, Mapping) and item.get("kind") in _EVIDENCE_KINDS and item.get("value") is not None:
            items.append((str(item["kind"]), str(item["value"])))
    return tuple(items)


@dataclass
class Investigation:
    """One candidate, its dossier, state and provenance back to the field, profile and archive
    releases. There is deliberately no verdict attribute (INV-008)."""

    investigation_id: str
    set_id: str
    field_id: str
    candidate: Subject
    evidence_bundle_ids: tuple[str, ...]
    test_results: tuple[TestResult, ...]
    rank: Rank
    evidence_used: EvidenceUsed
    state: InvestigationState = InvestigationState.OPEN
    transitions: list[Transition] = dataclass_field(default_factory=list)
    notes: list[Note] = dataclass_field(default_factory=list)
    links: dict[str, str] = dataclass_field(default_factory=dict)
    label: str = EXPLORATORY
    _events: list[ev.DomainEvent] = dataclass_field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.label != EXPLORATORY:
            raise InvariantViolation("an investigation is EXPLORATORY (INV-008)")
        if not self.evidence_bundle_ids:
            raise InvariantViolation("an investigation names the evidence bundles its tests read")

    @classmethod
    def open(
        cls,
        *,
        investigation_id: str,
        set_id: str,
        field_id: str,
        candidate: RankedCandidate,
        evidence_bundle_ids: tuple[str, ...],
        policy: RankingPolicy,
        evidence_used: EvidenceUsed,
        at: datetime,
        actor: str = "kernel",
    ) -> Investigation:
        inv = cls(
            investigation_id=investigation_id,
            set_id=set_id,
            field_id=field_id,
            candidate=candidate.subject,
            evidence_bundle_ids=evidence_bundle_ids,
            test_results=candidate.results,
            rank=Rank(candidate.score, policy.policy_id, policy.version),
            evidence_used=evidence_used,
        )
        inv.transitions.append(Transition(None, InvestigationState.OPEN, "opened by the run", actor, at))
        inv._events.append(
            ev.InvestigationOpened(
                investigation_id,
                at,
                {"set_id": set_id, "candidate": candidate.subject.key, "score": candidate.score},
            )
        )
        return inv

    @property
    def read_only(self) -> bool:
        return self.state in (InvestigationState.PROMOTED, InvestigationState.CLOSED)

    def _move(self, to: InvestigationState, reason: str, actor: str, at: datetime) -> None:
        if to not in _ALLOWED[self.state]:
            raise InvalidTransition(f"investigation {self.investigation_id} cannot move from {self.state} to {to}")
        if not reason.strip():
            raise InvariantViolation("every triage transition records its reason")
        if not actor.strip():
            raise InvariantViolation("every triage transition records its actor")
        self.transitions.append(Transition(self.state, to, reason, actor, at))
        self.state = to

    def park(self, reason: str, actor: str, at: datetime) -> None:
        self._move(InvestigationState.PARKED, reason, actor, at)
        self._events.append(ev.InvestigationParked(self.investigation_id, at, {"reason": reason, "actor": actor}))

    def reopen(self, reason: str, actor: str, at: datetime) -> None:
        self._move(InvestigationState.OPEN, reason, actor, at)
        self._events.append(ev.InvestigationReopened(self.investigation_id, at, {"reason": reason, "actor": actor}))

    def close(self, reason: str, actor: str, at: datetime) -> None:
        self._move(InvestigationState.CLOSED, reason, actor, at)
        self._events.append(ev.InvestigationClosed(self.investigation_id, at, {"reason": reason, "actor": actor}))

    def unused_evidence(self, predeclaration: FrozenPredeclaration) -> tuple[tuple[str, str], ...]:
        return tuple((k, v) for k, v in confirmatory_evidence(predeclaration) if not self.evidence_used.covers(k, v))

    def promote(
        self,
        predeclaration: FrozenPredeclaration,
        *,
        actor: str,
        at: datetime,
        experiment_id: str | None = None,
    ) -> None:
        """Promotion needs a frozen confirmatory predeclaration naming evidence this investigation
        did not use (INV-009). The caller checks the hash is in the ledger; this checks the rest."""
        if InvestigationState.PROMOTED not in _ALLOWED[self.state]:
            raise InvalidTransition(f"investigation {self.investigation_id} is {self.state}; it cannot be promoted")
        unused = self.unused_evidence(predeclaration)
        if not unused:
            raise PromotionRefused(
                f"predeclaration {predeclaration.hash} names no evidence that investigation "
                f"{self.investigation_id} did not already use (INV-009)"
            )
        reason = "confirmatory predeclaration frozen: " + ", ".join(f"{k}={v}" for k, v in unused)
        self._move(InvestigationState.PROMOTED, reason, actor, at)
        self.links["predeclaration_hash"] = predeclaration.hash
        if experiment_id:
            self.links["experiment_id"] = experiment_id
        self._events.append(
            ev.InvestigationPromoted(
                self.investigation_id,
                at,
                {
                    "predeclaration_hash": predeclaration.hash,
                    "experiment_id": experiment_id,
                    "unused_evidence": [{"kind": k, "value": v} for k, v in unused],
                    "actor": actor,
                },
            )
        )

    def annotate(self, text: str, author: str, at: datetime) -> None:
        """Notes are appended, never edited; a promoted or closed investigation is read-only."""
        if self.read_only:
            raise InvalidTransition(f"investigation {self.investigation_id} is {self.state} and read-only")
        if not text.strip():
            raise InvariantViolation("an empty note says nothing")
        self.notes.append(Note(text, author, at))
        self._events.append(ev.InvestigationAnnotated(self.investigation_id, at, {"author": author}))

    def link(self, name: str, value: str) -> None:
        self.links[name] = value

    def pull_events(self) -> list[ev.DomainEvent]:
        events, self._events = self._events, []
        return events

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "investigation_id": self.investigation_id,
            "set_id": self.set_id,
            "field_id": self.field_id,
            "candidate": self.candidate.to_document(),
            "evidence_bundle_ids": list(self.evidence_bundle_ids),
            "test_results": [r.to_document() for r in self.test_results],
            "rank": self.rank.to_document(),
            "evidence_used": self.evidence_used.to_document(),
            "state": self.state.value,
            "transitions": [t.to_document() for t in self.transitions],
            "notes": [n.to_document() for n in self.notes],
            "links": dict(sorted(self.links.items())),
            "label": self.label,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Investigation:
        return cls(
            investigation_id=doc["investigation_id"],
            set_id=doc["set_id"],
            field_id=doc["field_id"],
            candidate=Subject.from_document(doc["candidate"]),
            evidence_bundle_ids=tuple(doc["evidence_bundle_ids"]),
            test_results=tuple(TestResult.from_document(r) for r in doc["test_results"]),
            rank=Rank.from_document(doc["rank"]),
            evidence_used=EvidenceUsed.from_document(doc["evidence_used"]),
            state=InvestigationState(doc["state"]),
            transitions=[Transition.from_document(t) for t in doc.get("transitions", [])],
            notes=[Note.from_document(n) for n in doc.get("notes", [])],
            links=dict(doc.get("links", {})),
            label=doc.get("label", EXPLORATORY),
        )


# ---------------------------------------------------------------------------------------------
# The set


class Stage(StrEnum):
    IDENTIFY = "identify"
    RETRIEVE = "retrieve"
    TEST = "test"
    CANDIDATES = "candidates"


STAGES = (Stage.IDENTIFY, Stage.RETRIEVE, Stage.TEST, Stage.CANDIDATES)


class StageState(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StageRecord:
    stage: Stage
    state: StageState = StageState.PENDING
    artifact_id: ArtifactId | None = None
    finished_at: datetime | None = None
    error: str | None = None
    summary: dict[str, Any] = dataclass_field(default_factory=dict)

    def to_document(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "state": self.state.value,
            "artifact_id": self.artifact_id.value if self.artifact_id else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
            "summary": _jsonable(self.summary),
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> StageRecord:
        return cls(
            stage=Stage(doc["stage"]),
            state=StageState(doc["state"]),
            artifact_id=ArtifactId(doc["artifact_id"]) if doc.get("artifact_id") else None,
            finished_at=datetime.fromisoformat(doc["finished_at"]) if doc.get("finished_at") else None,
            error=doc.get("error"),
            summary=dict(doc.get("summary", {})),
        )


@dataclass
class InvestigationSet:
    """One run of Investigate over a field under a profile: the multiplicity is recorded here
    (objects examined, tests applied), so false-discovery control can be applied at set level."""

    set_id: str
    field: Field
    profile: TestProfile
    seed: int
    started_at: datetime
    stages: dict[Stage, StageRecord] = dataclass_field(default_factory=lambda: {s: StageRecord(s) for s in STAGES})
    objects_examined: tuple[str, ...] = ()
    tests_applied: tuple[Mapping[str, Any], ...] = ()
    archive_releases: tuple[str, ...] = ()
    null_results: tuple[NullFieldResult, ...] = ()
    investigation_ids: tuple[str, ...] = ()
    manifest_id: ArtifactId | None = None
    label: str = EXPLORATORY
    plan_artifact_id: str | None = None
    """The approved AcquisitionPlan this run executed, when it was planned (ADR-020)."""
    _events: list[ev.DomainEvent] = dataclass_field(default_factory=list, repr=False, compare=False)

    @property
    def profile_hash(self) -> str:
        return self.profile.profile_hash

    @property
    def complete(self) -> bool:
        return all(s.state is StageState.COMPLETED for s in self.stages.values())

    def stage_done(self, stage: Stage) -> bool:
        return self.stages[stage].state is StageState.COMPLETED

    def complete_stage(self, stage: Stage, artifact_id: ArtifactId, at: datetime, **summary: Any) -> None:
        for earlier in STAGES[: STAGES.index(stage)]:
            if not self.stage_done(earlier):
                raise InvalidTransition(f"stage {stage} cannot complete before {earlier}")
        self.stages[stage] = StageRecord(stage, StageState.COMPLETED, artifact_id, at, None, dict(summary))
        self._events.append(
            ev.InvestigationStageCompleted(
                self.set_id, at, {"stage": stage.value, "artifact_id": artifact_id.value, **_jsonable(summary)}
            )
        )

    def fail_stage(self, stage: Stage, error: str, at: datetime) -> None:
        """A failed stage leaves everything before it intact (Investigate §1)."""
        self.stages[stage] = StageRecord(stage, StageState.FAILED, None, at, error)
        self._events.append(ev.InvestigationStageFailed(self.set_id, at, {"stage": stage.value, "error": error}))

    def pull_events(self) -> list[ev.DomainEvent]:
        events, self._events = self._events, []
        return events

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "set_id": self.set_id,
            "field_id": self.field.field_id,
            "field": self.field.to_document(),
            "profile_hash": self.profile_hash,
            "profile": self.profile.to_document(),
            "seed": self.seed,
            "started_at": self.started_at.isoformat(),
            "stages": [self.stages[s].to_document() for s in STAGES],
            "objects_examined": list(self.objects_examined),
            "tests_applied": [_jsonable(dict(t)) for t in self.tests_applied],
            "archive_releases": sorted(self.archive_releases),
            "null_results": [n.to_document() for n in self.null_results],
            "investigation_ids": list(self.investigation_ids),
            "manifest_id": self.manifest_id.value if self.manifest_id else None,
            "label": self.label,
            "plan_artifact_id": self.plan_artifact_id,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> InvestigationSet:
        stages = {s: StageRecord(s) for s in STAGES}
        for sd in doc.get("stages", []):
            record = StageRecord.from_document(sd)
            stages[record.stage] = record
        return cls(
            set_id=doc["set_id"],
            field=Field.from_document(doc["field"]),
            profile=TestProfile.from_document(doc["profile"]),
            seed=int(doc["seed"]),
            started_at=datetime.fromisoformat(doc["started_at"]),
            stages=stages,
            objects_examined=tuple(doc.get("objects_examined", ())),
            tests_applied=tuple(dict(t) for t in doc.get("tests_applied", ())),
            archive_releases=tuple(doc.get("archive_releases", ())),
            null_results=tuple(NullFieldResult.from_document(n) for n in doc.get("null_results", ())),
            investigation_ids=tuple(doc.get("investigation_ids", ())),
            manifest_id=ArtifactId(doc["manifest_id"]) if doc.get("manifest_id") else None,
            label=doc.get("label", EXPLORATORY),
            plan_artifact_id=doc.get("plan_artifact_id"),
        )
