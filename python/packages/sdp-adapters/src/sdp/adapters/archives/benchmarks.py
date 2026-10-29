"""Catalogues of known star clusters from VizieR, for the UC-01 benchmark cross-match (STL-005, DSI-015).

A candidate cluster is "potentially new" only when it matches no cluster in two published catalogues
(§10). The two the UC-01 predeclaration names are Hunt & Reffert 2023 (``hr23``) and Cantat-Gaudin et
al. 2020 (``cga20``); both are VizieR tables. VizieR names columns after the paper (``RA_ICRS``,
``pmRA*``, ``Plx``), so each column is chosen by its UCD, the IVOA label of what it measures, read from
the service's own ``TAP_SCHEMA``; its unit is checked against the one the cross-match assumes. Both
questions travel through a Transport, so CI replays them and never calls VizieR (ADR-015).

The table that comes back has the benchmark columns ``sdp_stellar.benchmark`` reads (``name``,
``ra_deg``, ``dec_deg``, ``pm_ra_cosdec_masyr``, ``pm_dec_masyr`` and, where the catalogue has it,
``parallax_mas``) and schema metadata naming its release, licence and citation (STL-G-02), and which
native column filled each one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from sdp.adapters.archives.transport import HttpRequest, Transport
from sdp.adapters.archives.vo import literal, run_sync, sync_request

TAPVIZIER = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap"
LICENCE = "CC BY 4.0 (VizieR; cite the catalogue's paper, https://cds.unistra.fr/conditions-of-use/)"
#: rows asked for; the largest catalogue (hr23) has about 7,200 clusters
MAXREC = 100_000


@dataclass(frozen=True)
class KnownClusterCatalogue:
    benchmark_id: str
    vizier_table: str
    #: the label the UC-01 predeclaration names in ``parameters.benchmarks``
    release_label: str
    citation: str
    description: str
    licence: str = LICENCE


CATALOGUES: dict[str, KnownClusterCatalogue] = {
    "hr23": KnownClusterCatalogue(
        "hr23",
        "J/A+A/673/A114/clusters",
        "hr23:v1.0",
        "Hunt & Reffert 2023, A&A 673, A114 (doi:10.1051/0004-6361/202346285)",
        "7,167 clusters found by HDBSCAN in all of Gaia DR3",
    ),
    "cga20": KnownClusterCatalogue(
        "cga20",
        "J/A+A/640/A1/table1",
        "cga20:v1",
        "Cantat-Gaudin et al. 2020, A&A 640, A1 (doi:10.1051/0004-6361/202038192)",
        "2,017 open clusters with Gaia DR2 members and ages",
    ),
}


@dataclass(frozen=True)
class Wanted:
    column: str
    ucds: tuple[str, ...]
    units: tuple[str, ...]
    required: bool = True


#: Each benchmark column, the UCDs that fill it (first preferred) and the units it may come in.
WANTED: tuple[Wanted, ...] = (
    Wanted("name", ("meta.id;meta.main", "meta.id"), ("",)),
    Wanted("ra_deg", ("pos.eq.ra;meta.main", "pos.eq.ra"), ("deg",)),
    Wanted("dec_deg", ("pos.eq.dec;meta.main", "pos.eq.dec"), ("deg",)),
    Wanted("pm_ra_cosdec_masyr", ("pos.pm;pos.eq.ra;meta.main", "pos.pm;pos.eq.ra"), ("mas/yr", "mas/a")),
    Wanted("pm_dec_masyr", ("pos.pm;pos.eq.dec;meta.main", "pos.pm;pos.eq.dec"), ("mas/yr", "mas/a")),
    Wanted("parallax_mas", ("pos.parallax.trig;meta.main", "pos.parallax.trig", "pos.parallax"), ("mas",), False),
)


class CatalogueSchemaError(ValueError):
    """VizieR's description of a table lacks a column the cross-match needs, or gives it another unit."""


def _norm(text: object) -> str:
    return "" if text is None else str(text).strip().lower()


def choose_columns(
    schema: Sequence[Mapping[str, object]], overrides: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Benchmark column -> native column, from ``TAP_SCHEMA.columns`` rows (column_name, ucd, unit) in
    the service's order. An override names the native column outright; its unit is still checked."""
    overrides = dict(overrides or {})
    unknown = sorted(set(overrides) - {w.column for w in WANTED})
    if unknown:
        raise CatalogueSchemaError(f"no benchmark column {unknown}; the columns are {[w.column for w in WANTED]}")
    by_name = {str(r["column_name"]): r for r in schema}
    chosen: dict[str, str] = {}
    for want in WANTED:
        if want.column in overrides:
            row = by_name.get(overrides[want.column])
            if row is None:
                raise CatalogueSchemaError(f"{want.column}: the table has no column {overrides[want.column]!r}")
        else:
            row = next(
                (r for ucd in want.ucds for r in schema if _norm(r.get("ucd")) == ucd),
                None,
            )
            if row is None:
                if want.required:
                    raise CatalogueSchemaError(
                        f"{want.column}: no column with UCD {' or '.join(want.ucds)}; name one with --column"
                    )
                continue
        unit = _norm(row.get("unit"))
        if want.units != ("",) and unit not in want.units:
            raise CatalogueSchemaError(
                f"{want.column}: column {row['column_name']} is in {unit or 'no unit'!r}, not {' or '.join(want.units)}"
            )
        chosen[want.column] = str(row["column_name"])
    return chosen


def _identifier(name: str) -> str:
    """A VizieR column name as an ADQL delimited identifier (``pmRA*`` is not a regular one)."""
    return '"' + name.replace('"', '""') + '"'


def _float(table: pa.Table, name: str) -> pa.Array:
    values = np.asarray(table.column(name).to_numpy(zero_copy_only=False), dtype=np.float64)
    return pa.array(values, mask=~np.isfinite(values), type=pa.float64())


@dataclass
class VizierKnownClusters:
    """Fetches one of ``CATALOGUES`` as a benchmark table."""

    transport: Transport
    tap_url: str = TAPVIZIER

    def schema_request(self, catalogue: KnownClusterCatalogue) -> HttpRequest:
        adql = (
            "SELECT column_name, ucd, unit FROM TAP_SCHEMA.columns "
            f"WHERE table_name = {literal(catalogue.vizier_table)}"
        )
        return sync_request(self.tap_url, adql, 1000)

    def rows_request(self, catalogue: KnownClusterCatalogue, columns: Mapping[str, str]) -> HttpRequest:
        select = ", ".join(f"{_identifier(native)} AS {mine}" for mine, native in columns.items())
        return sync_request(self.tap_url, f"SELECT {select} FROM {_identifier(catalogue.vizier_table)}", MAXREC)

    def fetch(self, benchmark_id: str, overrides: Mapping[str, str] | None = None) -> pa.Table:
        if benchmark_id not in CATALOGUES:
            raise KeyError(f"unknown catalogue {benchmark_id!r}; known: {sorted(CATALOGUES)}")
        catalogue = CATALOGUES[benchmark_id]
        described = run_sync(self.transport, self.schema_request(catalogue), f"VizieR {catalogue.vizier_table}")
        if described.num_rows == 0:
            raise CatalogueSchemaError(f"VizieR does not describe {catalogue.vizier_table}")
        columns = choose_columns(described.to_pylist(), overrides)
        raw = run_sync(self.transport, self.rows_request(catalogue, columns), f"VizieR {catalogue.vizier_table}")
        if raw.num_rows >= MAXREC:
            raise CatalogueSchemaError(f"{catalogue.vizier_table} filled the {MAXREC}-row limit; it was cut off")
        return benchmark_table(catalogue, raw, columns)


def benchmark_table(catalogue: KnownClusterCatalogue, raw: pa.Table, columns: Mapping[str, str]) -> pa.Table:
    """The benchmark table: typed columns, rows without a position dropped, sorted by name, and the
    metadata STL-G-02 checks."""
    names = pc.utf8_trim_whitespace(pc.cast(raw.column("name"), pa.string()))
    data = {"name": names}
    for want in WANTED[1:]:
        if want.column in columns:
            data[want.column] = _float(raw, want.column)
    table = pa.table(data)
    placed = pc.and_(pc.is_valid(table.column("ra_deg")), pc.is_valid(table.column("dec_deg")))
    table = table.filter(placed).sort_by([("name", "ascending")])
    return table.replace_schema_metadata(
        {
            "sdp.benchmark.release": catalogue.release_label,
            "sdp.benchmark.licence": catalogue.licence,
            "sdp.benchmark.citation": catalogue.citation,
            "sdp.benchmark.source": f"VizieR {catalogue.vizier_table}",
            "sdp.benchmark.columns": json.dumps(dict(columns), sort_keys=True),
        }
    )
