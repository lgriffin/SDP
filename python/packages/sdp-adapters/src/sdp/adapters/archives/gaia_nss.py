"""Gaia astrometric orbits with the masses Gaia fitted, from the Gaia TAP service (DSI-023).

The release (DR3 by default, DR4 from 2 December 2026) comes from ``gaia_releases.yaml``: its table and
column names, its service and its citation. The text below describes DR3.

``gaiadr3.nss_two_body_orbit`` holds the orbits Gaia fitted to stars that wobble; the rows with
Thiele-Innes elements (about 169,000) are orbits on the sky. ``gaiadr3.binary_masses`` gives the visible
star's mass range for most of them, and ``gaiadr3.gaia_source`` its magnitude and RUWE. The three are
joined on the server and fetched in source_id ranges, each as a TAP async job (#199): the synchronous
service timed out on the join while the archive was busy. Every request travels through a Transport,
so CI replays recordings (ADR-015).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pyarrow as pa

from sdp.adapters.archives.transport import Transport
from sdp.adapters.archives.vo import run_async, run_sync, sync_request

if TYPE_CHECKING:
    from sdp.adapters.archives.gaia_releases import GaiaRelease

log = logging.getLogger("sdp.adapters.gaia_nss")

GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap"
RELEASE = "gaia:DR3"
CITATION = "Gaia Collaboration, Arenou et al. 2023, A&A 674, A34 (Gaia DR3 stellar multiplicity); https://www.cosmos.esa.int/web/gaia-users/credits"
LICENCE = "CC BY-SA 3.0 IGO (ESA Gaia, https://www.cosmos.esa.int/web/gaia-users/license)"
#: Gaia source_ids run up to 2^63; the 35 low bits are not position, so equal ranges hold similar sky areas
SOURCE_ID_MAX = 2**63 - 1
#: ranges asked for in turn; about 14,000 orbits each, which the synchronous service returns in seconds
PARTS = 36
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


def _dr3() -> GaiaRelease:
    from sdp.adapters.archives.gaia_releases import release

    return release("dr3")


def _select(r: GaiaRelease | None = None) -> str:
    r = r or _dr3()
    sid = r.column("source_id")
    cols = (
        [r.select("n", c) for c in ORBIT_COLUMNS]
        + [r.select("g", c) for c in SOURCE_COLUMNS]
        + [r.select("b", c) for c in MASS_COLUMNS]
    )
    return (
        f"SELECT {', '.join(cols)} FROM {r.table('nss_two_body_orbit')} AS n "
        f"JOIN {r.table('gaia_source')} AS g ON g.{sid} = n.{sid} "
        f"LEFT OUTER JOIN {r.table('binary_masses')} AS b ON b.{sid} = n.{sid} "
        f"WHERE n.{r.column('a_thiele_innes')} IS NOT NULL"
    )


def orbit_query(lo: int, hi: int, r: GaiaRelease | None = None) -> str:
    r = r or _dr3()
    return f"{_select(r)} AND n.{r.column('source_id')} BETWEEN {int(lo)} AND {int(hi)}"


def targets_query(source_ids: Sequence[int], r: GaiaRelease | None = None) -> str:
    """The same columns for named stars only: a refinement step's targets."""
    ids = sorted({int(s) for s in source_ids})
    if not ids:
        raise ValueError("no source_ids to ask for")
    r = r or _dr3()
    return f"{_select(r)} AND n.{r.column('source_id')} IN ({', '.join(str(s) for s in ids)})"


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


def _with_metadata(table: pa.Table, query: str, parts: int, r: GaiaRelease | None = None) -> pa.Table:
    r = r or _dr3()
    meta = {
        b"sdp.release": r.label.encode(),
        b"sdp.citation": (r.orbits_citation or CITATION).encode(),
        b"sdp.licence": LICENCE.encode(),
        b"sdp.query": query.encode(),
        b"sdp.parts": str(parts).encode(),
    }
    return table.replace_schema_metadata({**(table.schema.metadata or {}), **meta})


def fetch_targets(
    transport: Transport,
    source_ids: Sequence[int],
    service_url: str | None = None,
    release: GaiaRelease | None = None,
) -> pa.Table:
    """The orbits, if any, of named stars (at most a few thousand per call)."""
    r = release or _dr3()
    what = f"Gaia {r.name.upper()} orbits of targets"
    query = targets_query(source_ids, r)
    table = run_sync(transport, sync_request(service_url or r.tap, query, MAXREC), what)
    return _with_metadata(one_mass_per_orbit(_checked(table, what)), query, 1, r)


def fetch_orbits(
    transport: Transport,
    parts: int = PARTS,
    service_url: str | None = None,
    progress: Callable[[int, int], None] | None = None,
    parts_dir: Path | None = None,
    mode: Literal["async", "sync"] = "async",
    sleep: Callable[[float], None] = time.sleep,
    release: GaiaRelease | None = None,
) -> pa.Table:
    """Every Gaia orbit of the release (DR3 by default) on the sky with its star's G, RUWE and Gaia's mass range, one table.

    With ``parts_dir`` each source_id range is saved there as it arrives, and a range already saved is
    read back instead of asked for again, so a download that stops part way resumes where it stopped.
    Each range is an async job unless ``mode`` is ``"sync"``."""
    import pyarrow.parquet as pq

    r = release or _dr3()
    url = service_url or r.tap
    if parts_dir is not None:
        # saved parts are one release's: a folder left by another release is never mixed in
        marker = parts_dir / "release.txt"
        if marker.exists() and marker.read_text("utf-8").strip() != r.label:
            raise ValueError(
                f"{parts_dir} holds parts of {marker.read_text('utf-8').strip()}, not {r.label}; "
                "move it away or give another --out"
            )
        unrecorded = parts_dir.exists() and not marker.exists() and any(parts_dir.glob("part-*.parquet"))
        if unrecorded and r.label != "gaia:DR3":  # parts saved before releases were recorded are DR3's
            raise ValueError(f"{parts_dir} holds parts of an unrecorded release (DR3), not {r.label}")
        parts_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(r.label + "\n", "utf-8")
    tables = []
    spans = ranges(parts)
    for i, (lo, hi) in enumerate(spans):
        saved = parts_dir / f"part-{i + 1:03d}-of-{parts:03d}.parquet" if parts_dir is not None else None
        if saved is not None and saved.exists():
            table = pq.read_table(saved)
        else:
            what = f"Gaia {r.name.upper()} orbits part {i + 1}/{len(spans)}"
            if mode == "async":
                table = run_async(transport, url, orbit_query(lo, hi, r), MAXREC, what, sleep=sleep)
            else:
                table = run_sync(transport, sync_request(url, orbit_query(lo, hi, r), MAXREC), what)
            table = _checked(table, f"Gaia {r.name.upper()} orbits {lo}..{hi}")
            if saved is not None:
                saved.parent.mkdir(parents=True, exist_ok=True)
                tmp = saved.with_suffix(".tmp")
                pq.write_table(table, tmp)
                tmp.replace(saved)
        tables.append(table)
        if progress is not None:
            progress(i + 1, len(spans))
    whole = one_mass_per_orbit(pa.concat_tables(tables, promote_options="permissive").combine_chunks())
    return _with_metadata(whole, f"{_select(r)} (in {parts} source_id ranges)", parts, r)
