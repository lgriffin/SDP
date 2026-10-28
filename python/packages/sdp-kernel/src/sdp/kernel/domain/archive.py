"""Archive value objects (§8). A CatalogueQuery is a value object, not an ADQL string."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from sdp.kernel.domain.canonical import hash_document
from sdp.kernel.domain.errors import InvariantViolation

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ColumnDescriptor:
    name: str
    datatype: str
    unit: str | None = None
    description: str = ""


@dataclass(frozen=True)
class SourceDescriptor:
    """Release label, column list, licence, citation, rate limits and max rows per call."""

    source_id: str
    release_label: str
    table: str
    columns: tuple[ColumnDescriptor, ...]
    licence: str
    citation: str
    max_rows_per_call: int
    min_seconds_between_calls: float
    dialect_id: str
    doi: str | None = None

    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


@dataclass(frozen=True)
class Cone:
    ra_deg: float
    dec_deg: float
    radius_deg: float
    shape: Literal["cone"] = "cone"

    def __post_init__(self) -> None:
        if not (0 <= self.ra_deg < 360 and -90 <= self.dec_deg <= 90 and 0 < self.radius_deg <= 10):
            raise InvariantViolation("cone out of range; remote queries must stay bounded (radius <= 10 deg)")

    def to_document(self) -> dict[str, Any]:
        return {"shape": "cone", "ra_deg": self.ra_deg, "dec_deg": self.dec_deg, "radius_deg": self.radius_deg}


@dataclass(frozen=True)
class Box:
    ra_min_deg: float
    ra_max_deg: float
    dec_min_deg: float
    dec_max_deg: float
    shape: Literal["box"] = "box"

    def __post_init__(self) -> None:
        if not (self.ra_min_deg < self.ra_max_deg and self.dec_min_deg < self.dec_max_deg):
            raise InvariantViolation("box bounds must be ordered")
        area = (self.ra_max_deg - self.ra_min_deg) * (self.dec_max_deg - self.dec_min_deg)
        if area > 100:
            raise InvariantViolation("box larger than 100 square degrees; remote queries must stay bounded")

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.ra_min_deg + self.ra_max_deg) / 2, (self.dec_min_deg + self.dec_max_deg) / 2)

    def to_document(self) -> dict[str, Any]:
        return {
            "shape": "box",
            "ra_min_deg": self.ra_min_deg,
            "ra_max_deg": self.ra_max_deg,
            "dec_min_deg": self.dec_min_deg,
            "dec_max_deg": self.dec_max_deg,
        }


Region = Cone | Box


def region_from_document(doc: Mapping[str, Any]) -> Region:
    if doc.get("shape", "cone") == "cone":
        return Cone(float(doc["ra_deg"]), float(doc["dec_deg"]), float(doc["radius_deg"]))
    return Box(float(doc["ra_min_deg"]), float(doc["ra_max_deg"]), float(doc["dec_min_deg"]), float(doc["dec_max_deg"]))


_OPS = ("<", "<=", ">", ">=", "=", "!=", "is_not_null")


@dataclass(frozen=True)
class Filter:
    column: str
    op: str
    value: float | int | str | None = None

    def __post_init__(self) -> None:
        if not _IDENT.match(self.column):
            raise InvariantViolation(f"not a column identifier: {self.column!r}")
        if self.op not in _OPS:
            raise InvariantViolation(f"unsupported filter operator {self.op!r}")
        if self.op != "is_not_null" and self.value is None:
            raise InvariantViolation("filter needs a value")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise InvariantViolation("filter values must be finite")

    def to_document(self) -> dict[str, Any]:
        return {"column": self.column, "op": self.op, "value": self.value}


@dataclass(frozen=True)
class CatalogueQuery:
    """A bounded query: a cone or box, a column subset, filters and a row cap."""

    source_id: str
    release_label: str
    region: Region
    columns: tuple[str, ...]
    filters: tuple[Filter, ...] = ()
    row_cap: int = 100_000
    chunk_rows: int = 50_000
    order_by: str | None = None

    def __post_init__(self) -> None:
        if not self.columns:
            raise InvariantViolation("a query names its columns; SELECT * is not bounded")
        for c in self.columns:
            if not _IDENT.match(c):
                raise InvariantViolation(f"not a column identifier: {c!r}")
        if not (0 < self.chunk_rows <= self.row_cap):
            raise InvariantViolation("chunk_rows must be positive and no larger than row_cap")
        if self.order_by is not None and not _IDENT.match(self.order_by):
            raise InvariantViolation(f"not a column identifier: {self.order_by!r}")

    def to_document(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "release_label": self.release_label,
            "region": self.region.to_document(),
            "columns": list(self.columns),
            "filters": [f.to_document() for f in self.filters],
            "row_cap": self.row_cap,
            "chunk_rows": self.chunk_rows,
            "order_by": self.order_by,
        }


def idempotency_key(rendered_query: str, source_release: str, columns: tuple[str, ...]) -> str:
    """SHA-256 of rendered query text + source release + column list (§8 acquisition policy)."""
    return hash_document({"query": rendered_query, "release": source_release, "columns": list(columns)})


@dataclass(frozen=True)
class AcquisitionHandle:
    """A resumable handle: the rendered text, its key and the part to resume from."""

    query: CatalogueQuery
    rendered_text: str
    idempotency_key: str
    descriptor: SourceDescriptor
    resume_from_part: int = 0
    state: Mapping[str, Any] = field(default_factory=dict)

    def resumed_at(self, part: int) -> AcquisitionHandle:
        return AcquisitionHandle(
            self.query, self.rendered_text, self.idempotency_key, self.descriptor, part, self.state
        )
