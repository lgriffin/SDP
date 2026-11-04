"""Gaia DR3 astrometric orbits with the masses Gaia fitted, from the Gaia TAP service (DSI-023).

``gaiadr3.nss_two_body_orbit`` holds the orbits Gaia fitted to stars that wobble; the rows with
Thiele-Innes elements (about 169,000) are orbits on the sky. ``gaiadr3.binary_masses`` gives the visible
star's mass range for most of them, and ``gaiadr3.gaia_source`` its magnitude and RUWE. The three are
joined on the server and fetched in source_id ranges, so no answer is large enough for the synchronous
service to cut off. Every request travels through a Transport, so CI replays recordings (ADR-015).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import pyarrow as pa

from sdp.adapters.archives.transport import Transport
from sdp.adapters.archives.vo import run_sync, sync_request

log = logging.getLogger("sdp.adapters.gaia_nss")

GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap"
RELEASE = "gaia:DR3"
CITATION = "Gaia Collaboration, Arenou et al. 2023, A&A 674, A34 (Gaia DR3 stellar multiplicity); https://www.cosmos.esa.int/web/gaia-users/credits"
LICENCE = "CC BY-SA 3.0 IGO (ESA Gaia, https://www.cosmos.esa.int/web/gaia-users/license)"
#: Gaia source_ids run up to 2^63; the 35 low bits are not position, so equal ranges hold similar sky areas
SOURCE_ID_MAX = 2**63 - 1
#: ranges asked for in turn; about 14,000 orbits each, which the synchronous service returns in seconds
PARTS = 12
#: rows allowed per answer, well above any one range's count, so a cut-off would show as a short answer
MAXREC = 200_000

ORBIT_COLUMNS = (
    "source_id",
    "nss_solution_type",
    "period",
    "period_error",
    "a_thiele_innes",
    "a_thiele_innes_error",
    "b_thiele_innes",
    "b_thiele_innes_error",
    "f_thiele_innes",
    "f_thiele_innes_error",
    "g_thiele_innes",
    "g_thiele_innes_error",
    "parallax",
    "parallax_error",
    "eccentricity",
    "significance",
    "goodness_of_fit",
)
SOURCE_COLUMNS = ("ra", "dec", "pmra", "pmdec", "phot_g_mean_mag", "bp_rp", "ruwe")
MASS_COLUMNS = (
    "m1",
    "m1_lower",
    "m1_upper",
    "m2",
    "m2_lower",
    "m2_upper",
    "fluxratio_lower",
    "fluxratio_upper",
    "combination_method",
    "flag",
)


def ranges(parts: int = PARTS) -> list[tuple[int, int]]:
    """Closed source_id ranges that cover every id exactly once."""
    step = (SOURCE_ID_MAX + 1) // parts
    edges = [i * step for i in range(parts)] + [SOURCE_ID_MAX + 1]
    return [(edges[i], edges[i + 1] - 1) for i in range(parts)]


def _select() -> str:
    cols = [f"n.{c}" for c in ORBIT_COLUMNS] + [f"g.{c}" for c in SOURCE_COLUMNS] + [f"b.{c}" for c in MASS_COLUMNS]
    return (
        f"SELECT {', '.join(cols)} FROM gaiadr3.nss_two_body_orbit AS n "
        "JOIN gaiadr3.gaia_source AS g ON g.source_id = n.source_id "
        "LEFT OUTER JOIN gaiadr3.binary_masses AS b ON b.source_id = n.source_id "
        "WHERE n.a_thiele_innes IS NOT NULL"
    )


def orbit_query(lo: int, hi: int) -> str:
    return f"{_select()} AND n.source_id BETWEEN {int(lo)} AND {int(hi)}"


def targets_query(source_ids: Sequence[int]) -> str:
    """The same columns for named stars only: a refinement step's targets."""
    ids = sorted({int(s) for s in source_ids})
    if not ids:
        raise ValueError("no source_ids to ask for")
    return f"{_select()} AND n.source_id IN ({', '.join(str(s) for s in ids)})"


def one_mass_per_orbit(table: pa.Table) -> pa.Table:
    """One row per (source_id, solution type). ``binary_masses`` holds a row per way of combining a
    star's solutions (``Orbital+M1``, ``SB2+M1``, ...), so the join repeats an orbit; the row kept is the
    one combining this orbit's own type with M1, else the first with a mass, else the first."""
    ids = table.column("source_id").to_pylist()
    kinds = table.column("nss_solution_type").to_pylist()
    methods = table.column("combination_method").to_pylist()
    m1 = table.column("m1").to_pylist()
    best: dict[tuple[int, str], tuple[int, int]] = {}
    for i, (sid, kind, method, mass) in enumerate(zip(ids, kinds, methods, m1, strict=True)):
        rank = 0 if (method or "").startswith(f"{kind}+") and mass is not None else 1 if mass is not None else 2
        key = (sid, kind)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, i)
    keep = sorted(i for _, i in best.values())
    return table.take(pa.array(keep, type=pa.int64()))


def _checked(table: pa.Table, what: str) -> pa.Table:
    if table.num_rows >= MAXREC:
        raise ValueError(f"{what}: {table.num_rows} rows reached MAXREC; ask in more parts")
    missing = [c for c in (*ORBIT_COLUMNS, *SOURCE_COLUMNS, *MASS_COLUMNS) if c not in table.column_names]
    if missing:
        raise ValueError(f"{what}: the answer lacks columns {missing}")
    return table.select([*ORBIT_COLUMNS, *SOURCE_COLUMNS, *MASS_COLUMNS])


def _with_metadata(table: pa.Table, query: str, parts: int) -> pa.Table:
    meta = {
        b"sdp.release": RELEASE.encode(),
        b"sdp.citation": CITATION.encode(),
        b"sdp.licence": LICENCE.encode(),
        b"sdp.query": query.encode(),
        b"sdp.parts": str(parts).encode(),
    }
    return table.replace_schema_metadata({**(table.schema.metadata or {}), **meta})


def fetch_targets(transport: Transport, source_ids: Sequence[int], service_url: str = GAIA_TAP) -> pa.Table:
    """The orbits, if any, of named stars (at most a few thousand per call)."""
    query = targets_query(source_ids)
    table = run_sync(transport, sync_request(service_url, query, MAXREC), "Gaia DR3 orbits of targets")
    return _with_metadata(one_mass_per_orbit(_checked(table, "Gaia DR3 orbits of targets")), query, 1)


def fetch_orbits(
    transport: Transport,
    parts: int = PARTS,
    service_url: str = GAIA_TAP,
    progress: Callable[[int, int], None] | None = None,
) -> pa.Table:
    """Every Gaia DR3 orbit on the sky with its star's G, RUWE and Gaia's mass range, one table."""
    tables = []
    spans = ranges(parts)
    for i, (lo, hi) in enumerate(spans):
        table = run_sync(transport, sync_request(service_url, orbit_query(lo, hi), MAXREC), "Gaia DR3 orbits")
        tables.append(_checked(table, f"Gaia DR3 orbits {lo}..{hi}"))
        if progress is not None:
            progress(i + 1, len(spans))
    whole = one_mass_per_orbit(pa.concat_tables(tables, promote_options="permissive").combine_chunks())
    return _with_metadata(whole, f"{_select()} (in {parts} source_id ranges)", parts)
