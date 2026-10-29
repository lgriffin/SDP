"""YamlDialectCurator: native columns to the canonical astrometry schema, driven by data (§8, DSI-005).

A dialect is ``dialects/<dialect_id>.yaml`` for one source release: which native column feeds each
canonical column and in what unit (converted with astropy.units, ADR-010), the epoch, the source-key
template, the correlation columns in Gaia order, the native quality columns kept verbatim and the
rule for the normalised quality class. ``dialects/golden/<dialect_id>.json`` pins it: native rows
in, canonical rows out, compared exactly by the dialect contract test. Adding Gaia DR4 is a new
dialect file and its golden rows.
"""

from __future__ import annotations

import json
import math
import string
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import astropy.units as u
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import yaml

from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.domain.dataset import DialectRef

#: Canonical columns, their order in curated Parquet and the unit of each physical one.
CANONICAL: tuple[tuple[str, str | None], ...] = (
    ("source_key", None),
    ("ra_deg", "deg"),
    ("dec_deg", "deg"),
    ("epoch_jyear", "yr"),
    ("parallax_mas", "mas"),
    ("parallax_mas_err", "mas"),
    ("pm_ra_cosdec_masyr", "mas / yr"),
    ("pm_ra_cosdec_masyr_err", "mas / yr"),
    ("pm_dec_masyr", "mas / yr"),
    ("pm_dec_masyr_err", "mas / yr"),
    ("astrometric_corr", None),
    ("g_mag", "mag"),
    ("bp_rp", "mag"),
    ("ruwe", None),
    ("quality_flags", None),
    ("quality_class", None),
    ("zero_point_corrected", None),
    ("source_release", None),
)
CANONICAL_COLUMNS = tuple(name for name, _ in CANONICAL)
_PHYSICAL = {name: unit for name, unit in CANONICAL if unit is not None}
_FLOATS = (*_PHYSICAL, "ruwe")

SCHEMA = pa.schema(
    [
        ("source_key", pa.string()),
        *(
            (name, pa.float64())
            for name in (
                "ra_deg",
                "dec_deg",
                "epoch_jyear",
                "parallax_mas",
                "parallax_mas_err",
                "pm_ra_cosdec_masyr",
                "pm_ra_cosdec_masyr_err",
                "pm_dec_masyr",
                "pm_dec_masyr_err",
            )
        ),
        ("astrometric_corr", pa.list_(pa.float64())),
        ("g_mag", pa.float64()),
        ("bp_rp", pa.float64()),
        ("ruwe", pa.float64()),
        ("quality_flags", pa.string()),
        ("quality_class", pa.string()),
        ("zero_point_corrected", pa.bool_()),
        ("source_release", pa.string()),
    ]
)

_JD_J2000 = 2451545.0
_MJD_OFFSET = 2400000.5


class DialectError(ValueError):
    pass


def _number(value: Any) -> float | None:
    if value is None:
        return None
    f = float(value)
    return None if math.isnan(f) else f


@dataclass(frozen=True)
class Dialect:
    ref: DialectRef
    source_id: str
    source_release: str
    key_template: str
    epoch: Mapping[str, Any]
    columns: Mapping[str, Mapping[str, Any]]
    correlations: tuple[str, ...] | None
    quality_flags: tuple[str, ...]
    quality_class: Mapping[str, Any]
    zero_point_corrected: bool
    #: Native proper motion in RA without the cos(dec) factor; the dialect multiplies it in.
    pm_ra_needs_cos_dec: bool = False

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Dialect:
        unknown = set(doc.get("columns", {})) - set(_FLOATS)
        if unknown:
            raise DialectError(f"{doc.get('dialect_id')}: {sorted(unknown)} are not canonical numeric columns")
        for name, spec in doc.get("columns", {}).items():
            if name in _PHYSICAL:
                native = u.Unit(spec.get("unit", _PHYSICAL[name]))
                if not native.is_equivalent(u.Unit(_PHYSICAL[name])):
                    raise DialectError(
                        f"{doc.get('dialect_id')}: {name} in {native} cannot convert to {_PHYSICAL[name]}"
                    )
        corr = doc.get("astrometric_corr")
        if corr is not None and len(corr) != 10:
            raise DialectError(f"{doc.get('dialect_id')}: astrometric_corr lists 10 columns in Gaia order or is null")
        return cls(
            ref=DialectRef(str(doc["dialect_id"]), str(doc["dialect_version"])),
            source_id=str(doc["source_id"]),
            source_release=str(doc["source_release"]),
            key_template=str(doc["source_key"]),
            epoch=dict(doc["epoch"]),
            columns={k: dict(v) for k, v in doc.get("columns", {}).items()},
            correlations=tuple(corr) if corr is not None else None,
            quality_flags=tuple(doc.get("quality_flags", ())),
            quality_class=dict(doc.get("quality_class", {})),
            zero_point_corrected=bool(doc.get("zero_point_corrected", False)),
            pm_ra_needs_cos_dec=bool(doc.get("pm_ra_needs_cos_dec", False)),
        )

    def native_columns(self) -> set[str]:
        needed = {spec["column"] for spec in self.columns.values()}
        needed |= set(self.correlations or ()) | set(self.quality_flags)
        if "column" in self.epoch:
            needed.add(self.epoch["column"])
        if "column" in self.quality_class:
            needed.add(self.quality_class["column"])
        return needed

    def _native_epoch(self, row: Mapping[str, Any]) -> float | None:
        """The native epoch value, or None where the archive left it blank or wrote a declared
        ``missing`` sentinel (PS1 writes -999 for objects without a mean epoch)."""
        value = _number(row.get(self.epoch["column"]))
        return None if value is None or value in self.epoch.get("missing", ()) else value

    def _epoch(self, row: Mapping[str, Any]) -> float:
        if "constant" in self.epoch:
            return float(self.epoch["constant"])
        value = self._native_epoch(row)
        if value is None:
            raise DialectError(f"{self.ref.dialect_id}: row without an epoch")
        kind = self.epoch.get("kind", "jyear")
        if kind == "jyear":
            return value
        if kind == "jd":
            return 2000.0 + (value - _JD_J2000) / 365.25
        if kind == "mjd":
            return 2000.0 + (value + _MJD_OFFSET - _JD_J2000) / 365.25
        raise DialectError(f"{self.ref.dialect_id}: unknown epoch kind {kind!r}")

    def _quality(self, row: Mapping[str, Any]) -> str:
        rule = self.quality_class
        if not rule:
            return "unknown"
        value = row.get(rule["column"])
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "unknown"
        if "map" in rule:
            return str(rule["map"].get(str(value), rule.get("default", "unknown")))
        for limit, label in rule["below"]:
            if float(value) < float(limit):
                return str(label)
        return str(rule.get("otherwise", "poor"))

    def row(self, native: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = dict.fromkeys(CANONICAL_COLUMNS)
        try:
            out["source_key"] = self.key_template.format(**native)
        except KeyError as exc:
            raise DialectError(f"{self.ref.dialect_id}: source key needs native column {exc}") from exc
        for name, spec in self.columns.items():
            value = _number(native.get(spec["column"]))
            if value is not None and name in _PHYSICAL:
                value = float((value * u.Unit(spec.get("unit", _PHYSICAL[name]))).to_value(_PHYSICAL[name]))
            out[name] = value
        if self.pm_ra_needs_cos_dec and out["dec_deg"] is not None:
            factor = math.cos(math.radians(out["dec_deg"]))
            for name in ("pm_ra_cosdec_masyr", "pm_ra_cosdec_masyr_err"):
                if out[name] is not None:
                    out[name] *= factor
        out["epoch_jyear"] = self._epoch(native)
        if self.correlations is not None:
            corr = [_number(native.get(c)) for c in self.correlations]
            out["astrometric_corr"] = None if all(c is None for c in corr) else corr
        out["quality_flags"] = canonical_json({f: native.get(f) for f in self.quality_flags})
        out["quality_class"] = self._quality(native)
        out["zero_point_corrected"] = self.zero_point_corrected
        out["source_release"] = self.source_release
        return out

    def curate(self, native: pa.Table) -> pa.Table:
        missing = self.native_columns() - set(native.column_names)
        if missing:
            raise DialectError(f"{self.ref.dialect_id}: native table lacks {sorted(missing)}")
        rows = native.to_pylist()
        kept = rows
        if self.epoch.get("exclude_rows_without_epoch") and "column" in self.epoch:
            kept = [r for r in rows if self._native_epoch(r) is not None]
        table = pa.Table.from_pylist([self.row(r) for r in kept], schema=SCHEMA)
        excluded = len(rows) - len(kept)
        if not excluded:
            return table
        # The canonical row needs an epoch; rows without one are left out and counted where the
        # curated Parquet carries it, never dropped silently.
        note = {"count": excluded, "reason": f"no {self.epoch['column']} (blank or a declared missing value)"}
        return table.replace_schema_metadata({b"sdp.excluded_rows": canonical_json(note).encode()})


def _floats(native: pa.Table, name: str) -> np.ndarray:
    """A native column as float64, null as NaN (what ``_number`` reads as None)."""
    col = native.column(name)
    if not (pa.types.is_floating(col.type) or pa.types.is_integer(col.type)):
        col = pc.cast(col, pa.float64())
    return np.asarray(pc.fill_null(pc.cast(col, pa.float64()), np.nan).to_numpy(zero_copy_only=False), dtype=np.float64)


def _nullable(values: np.ndarray) -> pa.Array:
    return pa.array(values, mask=np.isnan(values), type=pa.float64())


def curate_columnar(dialect: Dialect, native: pa.Table) -> pa.Table:
    """The table ``dialect.curate(native)`` returns, computed a column at a time (DSI-016).

    Row-by-row curation converts units once per value and costs about 0.7 ms a row, which is a day
    for the stars of a whole-sky sweep; here each unit conversion runs once per column. Only the
    source key, the quality flags and the correlation lists are built per row, from plain values."""
    missing = dialect.native_columns() - set(native.column_names)
    if missing:
        raise DialectError(f"{dialect.ref.dialect_id}: native table lacks {sorted(missing)}")
    excluded = 0
    if "constant" not in dialect.epoch:
        raw_epoch = _floats(native, dialect.epoch["column"])
        absent = np.isnan(raw_epoch) | np.isin(raw_epoch, np.asarray(dialect.epoch.get("missing", ()), dtype=float))
        if absent.any():
            if not dialect.epoch.get("exclude_rows_without_epoch"):
                raise DialectError(f"{dialect.ref.dialect_id}: row without an epoch")
            excluded = int(absent.sum())
            native = native.filter(pa.array(~absent))
    n = native.num_rows
    out: dict[str, pa.Array] = {}
    fields = [f for _, f, _, _ in string.Formatter().parse(dialect.key_template) if f]
    values = {f: native.column(f).to_pylist() for f in fields}
    out["source_key"] = pa.array(
        [dialect.key_template.format(**{f: values[f][i] for f in fields}) for i in range(n)], pa.string()
    )
    numbers: dict[str, np.ndarray] = {}
    for name in CANONICAL_COLUMNS:
        if name not in dialect.columns:
            continue
        spec = dialect.columns[name]
        v = _floats(native, spec["column"])
        if name in _PHYSICAL:
            v = np.asarray((v * u.Unit(spec.get("unit", _PHYSICAL[name]))).to_value(_PHYSICAL[name]), dtype=np.float64)
        numbers[name] = v
    if dialect.pm_ra_needs_cos_dec and "dec_deg" in numbers:
        factor = np.cos(np.radians(numbers["dec_deg"]))
        for name in ("pm_ra_cosdec_masyr", "pm_ra_cosdec_masyr_err"):
            if name in numbers:
                numbers[name] = numbers[name] * factor
    if "constant" in dialect.epoch:
        epoch = np.full(n, float(dialect.epoch["constant"]))
    else:
        epoch = _floats(native, dialect.epoch["column"])
        kind = dialect.epoch.get("kind", "jyear")
        if kind == "jd":
            epoch = 2000.0 + (epoch - _JD_J2000) / 365.25
        elif kind == "mjd":
            epoch = 2000.0 + (epoch + _MJD_OFFSET - _JD_J2000) / 365.25
        elif kind != "jyear":
            raise DialectError(f"{dialect.ref.dialect_id}: unknown epoch kind {kind!r}")
    if dialect.correlations is not None:
        corr = np.column_stack([_floats(native, c) for c in dialect.correlations]) if n else np.empty((0, 10))
        lists: list[list[float | None] | None] = []
        for row in corr.tolist():
            items = [None if math.isnan(x) else x for x in row]
            lists.append(None if all(x is None for x in items) else items)
        correlations = pa.array(lists, pa.list_(pa.float64()))
    else:
        correlations = pa.nulls(n, pa.list_(pa.float64()))
    flags = {f: native.column(f).to_pylist() for f in dialect.quality_flags}
    for name in CANONICAL_COLUMNS:
        if name == "source_key":
            continue
        if name == "epoch_jyear":
            out[name] = pa.array(epoch, pa.float64())
        elif name in numbers:
            out[name] = _nullable(numbers[name])
        elif name in _FLOATS:
            out[name] = pa.nulls(n, pa.float64())
        elif name == "astrometric_corr":
            out[name] = correlations
        elif name == "quality_flags":
            out[name] = pa.array(
                [canonical_json({f: flags[f][i] for f in dialect.quality_flags}) for i in range(n)], pa.string()
            )
        elif name == "quality_class":
            out[name] = pa.array(_quality_classes(dialect, native), pa.string())
        elif name == "zero_point_corrected":
            out[name] = pa.array([dialect.zero_point_corrected] * n, pa.bool_())
        elif name == "source_release":
            out[name] = pa.array([dialect.source_release] * n, pa.string())
    table = pa.Table.from_arrays([out[name] for name in CANONICAL_COLUMNS], schema=SCHEMA)
    if not excluded:
        return table
    note = {"count": excluded, "reason": f"no {dialect.epoch['column']} (blank or a declared missing value)"}
    return table.replace_schema_metadata({b"sdp.excluded_rows": canonical_json(note).encode()})


def _quality_classes(dialect: Dialect, native: pa.Table) -> list[str]:
    rule = dialect.quality_class
    n = native.num_rows
    if not rule:
        return ["unknown"] * n
    if "map" in rule:
        return [dialect._quality({rule["column"]: v}) for v in native.column(rule["column"]).to_pylist()]
    value = _floats(native, rule["column"])
    labels = np.full(n, str(rule.get("otherwise", "poor")), dtype=object)
    decided = np.isnan(value)
    labels[decided] = "unknown"
    for limit, label in rule["below"]:
        hit = ~decided & (value < float(limit))
        labels[hit] = str(label)
        decided |= hit
    return [str(x) for x in labels.tolist()]


def _root(directory: Path | None) -> Path:
    return Path(directory) if directory else Path(str(files("sdp.adapters.archives").joinpath("dialects")))


def load_dialects(directory: Path | None = None) -> dict[str, Dialect]:
    out: dict[str, Dialect] = {}
    for path in sorted(_root(directory).glob("*.yaml")):
        dialect = Dialect.from_document(yaml.safe_load(path.read_text("utf-8")))
        out[dialect.ref.dialect_id] = dialect
    return out


def load_golden(dialect_id: str, directory: Path | None = None) -> dict[str, Any]:
    return dict(json.loads((_root(directory) / "golden" / f"{dialect_id}.json").read_text("utf-8")))


@dataclass
class YamlDialectCurator:
    dialects: Mapping[str, Dialect]

    @classmethod
    def packaged(cls) -> YamlDialectCurator:
        return cls(load_dialects())

    def _find(self, source_id: str, release_label: str) -> Dialect:
        for d in self.dialects.values():
            if d.source_id == source_id and d.source_release == release_label:
                return d
        raise DialectError(f"no dialect declared for {source_id} {release_label}; it cannot be curated (DSI-001)")

    def dialect_for(self, source_id: str, release_label: str) -> DialectRef:
        return self._find(source_id, release_label).ref

    def curate(self, native: pa.Table, dialect: DialectRef) -> pa.Table:
        d = self.dialects.get(dialect.dialect_id)
        if d is None or d.ref != dialect:
            raise DialectError(f"dialect {dialect.dialect_id}@{dialect.dialect_version} is not loaded")
        return d.curate(native)
