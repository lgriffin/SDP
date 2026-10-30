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

import io
import json
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from sdp.adapters.archives import votable
from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.adapters.archives.untrusted import UntrustedInput, validate_votable
from sdp.adapters.archives.vo import literal, run_sync, sync_request
from sdp.adapters.archives.votable import TapQueryError

log = logging.getLogger("sdp.adapters.benchmarks")

TAPVIZIER = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap"
#: VizieR's own copies of every catalogue, in the order tried when TAPVizieR gives no answer: CDS's
#: classic interface first (it can be up when TAP is not), then the official mirrors. Each answers the
#: same ASU query with a VOTable whose FIELDs carry the UCD and unit, so columns are chosen as over TAP.
VIZIER_MIRRORS: tuple[str, ...] = (
    "https://vizier.cds.unistra.fr",
    "https://vizier.cfa.harvard.edu",
    "https://vizier.iucaa.in",
    "https://vizier.china-vo.org",
    "https://vizier.idia.ac.za",
    "https://vizier.nao.ac.jp",
)
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


@dataclass(frozen=True)
class FirstTable:
    """What a VOTable says about its first table before any row is read: the names it goes by (its
    ``name`` and ``ID`` attributes) and each FIELD as a ``TAP_SCHEMA.columns``-shaped row."""

    names: tuple[str, ...]
    columns: list[dict[str, str]]


def first_table(body: bytes) -> FirstTable:
    """Read a VOTable only as far as its first table's rows, streaming, so a large document is not held
    as a tree: a QUERY_STATUS of ERROR raises TapQueryError, as it does over TAP."""
    validate_votable(body)
    names: tuple[str, ...] = ()
    columns: list[dict[str, str]] = []
    inside = False
    for event, element in ET.iterparse(io.BytesIO(body), events=("start", "end")):
        tag = element.tag.rsplit("}", 1)[-1]
        if event == "start":
            if tag == "TABLE" and not inside:
                inside = True
                names = tuple(n for n in (element.get("name"), element.get("ID")) if n)
            elif tag == "FIELD" and inside:
                columns.append(
                    {
                        "column_name": element.get("name") or element.get("ID") or "",
                        "ucd": element.get("ucd") or "",
                        "unit": element.get("unit") or "",
                    }
                )
            elif tag == "DATA" and inside:  # every field of the first table comes before its rows
                break
            continue
        if tag == "INFO" and element.get("name") == "QUERY_STATUS" and (element.get("value") or "").upper() == "ERROR":
            raise TapQueryError((element.text or "VizieR query failed").strip())
        if tag == "TABLE":
            break
        element.clear()
    return FirstTable(names, columns)


def votable_columns(body: bytes) -> list[dict[str, str]]:
    """Each FIELD of a VOTable's first table as a ``TAP_SCHEMA.columns`` row, the unit exactly as written."""
    return first_table(body).columns


def _names(catalogue: KnownClusterCatalogue) -> set[str]:
    """How VizieR names a table in a VOTable: ``name`` is the table itself, ``ID`` the same with every
    character an XML ID cannot hold written as ``_``."""
    return {catalogue.vizier_table, re.sub(r"[^A-Za-z0-9_.-]", "_", catalogue.vizier_table)}


@dataclass
class VizierKnownClusters:
    """Fetches one of ``CATALOGUES`` as a benchmark table: from TAPVizieR, else from the first VizieR
    mirror that answers, else from a VOTable file saved from any VizieR page."""

    transport: Transport
    tap_url: str = TAPVIZIER
    mirrors: tuple[str, ...] = ()

    def schema_request(self, catalogue: KnownClusterCatalogue) -> HttpRequest:
        adql = (
            "SELECT column_name, ucd, unit FROM TAP_SCHEMA.columns "
            f"WHERE table_name = {literal(catalogue.vizier_table)}"
        )
        return sync_request(self.tap_url, adql, 1000)

    def rows_request(self, catalogue: KnownClusterCatalogue, columns: Mapping[str, str]) -> HttpRequest:
        select = ", ".join(f"{_identifier(native)} AS {mine}" for mine, native in columns.items())
        return sync_request(self.tap_url, f"SELECT {select} FROM {_identifier(catalogue.vizier_table)}", MAXREC)

    def mirror_request(self, catalogue: KnownClusterCatalogue, base: str) -> HttpRequest:
        """VizieR's ASU query for the whole table as a VOTable, positions in decimal degrees."""
        query = urllib.parse.urlencode(
            {"-source": catalogue.vizier_table, "-out.max": str(MAXREC), "-oc.form": "dec", "-out.all": "1"}
        )
        return HttpRequest("GET", f"{base}/viz-bin/votable?{query}")

    def fetch(self, benchmark_id: str, overrides: Mapping[str, str] | None = None) -> pa.Table:
        catalogue = _catalogue(benchmark_id)
        try:
            return self._from_tap(catalogue, overrides)
        except TransportError as exc:
            if not (self.mirrors and _down(exc.status)):
                raise
            failures = [f"TAPVizieR: {exc}"]
        # Only a service that is down moves on: a query the mirror refuses in its VOTable, or a catalogue
        # it describes without a needed column, is refused, as it would be everywhere.
        for base in self.mirrors:
            try:
                response = self.transport.send(self.mirror_request(catalogue, base))
                if response.status != 200:
                    raise TransportError(f"HTTP {response.status}", response.status)
                table = from_votable(catalogue, response.body, overrides, f"VizieR {catalogue.vizier_table} via {base}")
            except TransportError as exc:
                if not _down(exc.status):
                    raise
                failures.append(f"{base}: {exc}")
                continue
            except (UntrustedInput, ET.ParseError) as exc:  # an error page instead of the table
                failures.append(f"{base}: {exc}")
                continue
            log.warning("TAPVizieR did not answer; %s read from %s", catalogue.vizier_table, base)
            return table
        raise TransportError(f"no VizieR service answered for {catalogue.vizier_table}: " + "; ".join(failures))

    def _from_tap(self, catalogue: KnownClusterCatalogue, overrides: Mapping[str, str] | None) -> pa.Table:
        described = run_sync(self.transport, self.schema_request(catalogue), f"VizieR {catalogue.vizier_table}")
        if described.num_rows == 0:
            raise CatalogueSchemaError(f"VizieR does not describe {catalogue.vizier_table}")
        columns = choose_columns(described.to_pylist(), overrides)
        raw = run_sync(self.transport, self.rows_request(catalogue, columns), f"VizieR {catalogue.vizier_table}")
        if raw.num_rows >= MAXREC:
            raise CatalogueSchemaError(f"{catalogue.vizier_table} filled the {MAXREC}-row limit; it was cut off")
        return benchmark_table(catalogue, raw, columns)


def _down(status: int | None) -> bool:
    """No answer, or a server error: the service is down, so another copy of it may serve."""
    return status is None or status >= 500


def _catalogue(benchmark_id: str) -> KnownClusterCatalogue:
    if benchmark_id not in CATALOGUES:
        raise KeyError(f"unknown catalogue {benchmark_id!r}; known: {sorted(CATALOGUES)}")
    return CATALOGUES[benchmark_id]


def from_votable(
    catalogue: KnownClusterCatalogue | str,
    body: bytes,
    overrides: Mapping[str, str] | None = None,
    source: str | None = None,
) -> pa.Table:
    """A benchmark table from a whole-table VOTable (a mirror's answer, or a file saved from a VizieR
    page), columns chosen by the UCDs and units its FIELDs carry, exactly as over TAP (DSI-015)."""
    if isinstance(catalogue, str):
        catalogue = _catalogue(catalogue)
    table = first_table(body)
    if not _names(catalogue) & set(table.names):
        called = " or ".join(repr(n) for n in table.names) if table.names else "nothing"
        raise CatalogueSchemaError(
            f"the VOTable's first table is called {called}, not {catalogue.vizier_table}: "
            "save the catalogue's own table from VizieR as a VOTable"
        )
    if not table.columns:
        raise CatalogueSchemaError(f"the VOTable for {catalogue.vizier_table} has no columns")
    columns = choose_columns(table.columns, overrides)
    native = votable.parse(body)
    if native.num_rows >= MAXREC:
        raise CatalogueSchemaError(f"{catalogue.vizier_table} filled the {MAXREC}-row limit; it was cut off")
    raw = pa.table({mine: native.column(theirs) for mine, theirs in columns.items()})
    return benchmark_table(catalogue, raw, columns, source)


def benchmark_table(
    catalogue: KnownClusterCatalogue, raw: pa.Table, columns: Mapping[str, str], source: str | None = None
) -> pa.Table:
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
            "sdp.benchmark.source": source or f"VizieR {catalogue.vizier_table}",
            "sdp.benchmark.columns": json.dumps(dict(columns), sort_keys=True),
        }
    )
