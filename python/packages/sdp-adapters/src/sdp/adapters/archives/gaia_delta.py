"""What the DR4 delta engine (Discovery Roadmap C5) reads: the seed clusters, their stars in each Gaia
release, and how stars of one release map to the next.

* Hunt & Reffert 2023's cluster rows and DR3 member lists come from VizieR's classic interface (ASU),
  at CDS or the first official mirror that answers (``MirrorTransport``): TAPVizieR is often refused.
* Each cluster's stars in a release come from the Gaia archive's TAP service: one cone of the cluster's
  tidal radius, cut to a parallax window and a magnitude limit on the server.
* DR2 sources are matched to DR3 through Gaia's ``dr2_neighbourhood`` table, nearest neighbour first.

Everything goes through a Transport, so tests replay without the network (ADR-015).
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pyarrow as pa

from sdp.adapters.archives import votable
from sdp.adapters.archives.benchmarks import VIZIER_MIRRORS
from sdp.adapters.archives.transport import HttpRequest, MirrorTransport, Transport, TransportError
from sdp.adapters.archives.untrusted import UntrustedInput, validate_votable
from sdp.adapters.archives.vo import run_sync, sync_request

GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap"
HR23_CLUSTERS = "J/A+A/673/A114/clusters"
HR23_MEMBERS = "J/A+A/673/A114/members"
CLUSTER_COLUMNS = ("Name", "ID", "N", "RA_ICRS", "DE_ICRS", "rt", "pmRA", "s_pmRA", "pmDE", "s_pmDE", "Plx", "s_Plx")
#: rows allowed per Gaia cone; an answer this long was cut off and is refused
MAXREC = 500_000
MAX_MEMBERS = 100_000
#: source_ids per neighbourhood query
CHUNK = 500


def vizier(inner: Transport) -> Transport:
    """VizieR at CDS, or the first mirror that answers."""
    return MirrorTransport(inner, {VIZIER_MIRRORS[0]: VIZIER_MIRRORS[1:]})


def _asu(source: str, name: str, columns: Sequence[str], max_rows: int) -> HttpRequest:
    query = urllib.parse.urlencode(
        {"-source": source, "Name": name, "-out": ",".join(columns), "-out.max": str(max_rows)}
    )
    return HttpRequest("GET", f"{VIZIER_MIRRORS[0]}/viz-bin/votable?{query}")


def _votable(transport: Transport, request: HttpRequest, what: str) -> pa.Table:
    response = transport.send(request)
    if response.status != 200:
        raise TransportError(f"{what}: HTTP {response.status}", response.status)
    validate_votable(response.body)
    return votable.parse(response.body)


def _safe_name(name: str) -> str:
    if not name or not all(c.isalnum() or c in "_-+." for c in name):
        raise ValueError(f"not a catalogue name: {name!r}")
    return name


def hr23_cluster(transport: Transport, name: str) -> dict[str, Any]:
    """One cluster's HR23 row, matched exactly by name."""
    rows = [
        r
        for r in _votable(
            transport, _asu(HR23_CLUSTERS, f"={_safe_name(name)}", CLUSTER_COLUMNS, 5), f"HR23 {name}"
        ).to_pylist()
        if str(r.get("Name", "")).strip() == name
    ]
    if len(rows) != 1:
        raise LookupError(f"HR23 lists {len(rows)} clusters named {name!r}, not one")
    found: dict[str, Any] = rows[0]
    return found


def hr23_members(transport: Transport, name: str) -> list[int]:
    """The Gaia DR3 source_ids HR23 gives as the cluster's members."""
    table = _votable(
        transport, _asu(HR23_MEMBERS, f"={_safe_name(name)}", ("Name", "GaiaDR3"), MAX_MEMBERS), f"HR23 members {name}"
    )
    if table.num_rows >= MAX_MEMBERS:
        raise UntrustedInput(f"HR23 members of {name}: {table.num_rows} rows reached the cap")
    return [int(r["GaiaDR3"]) for r in table.to_pylist() if str(r.get("Name", "")).strip() == name]


def cone_query(
    table: str, columns: Sequence[str], ra: float, dec: float, radius: float, plx_lo: float, plx_hi: float, g_max: float
) -> str:
    if not all(part.isidentifier() for part in table.split(".")) or table.count(".") != 1:
        raise ValueError(f"not a Gaia table name: {table!r}")
    return (
        f"SELECT {', '.join(columns)} FROM {table} "
        f"WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.8f}, {dec:.8f}, {radius:.8f})) "
        f"AND parallax BETWEEN {plx_lo:.6f} AND {plx_hi:.6f} AND phot_g_mean_mag <= {g_max:.3f}"
    )


def cone(transport: Transport, query: str, what: str) -> pa.Table:
    table = run_sync(transport, sync_request(GAIA_TAP, query, MAXREC), what)
    if table.num_rows >= MAXREC:
        raise UntrustedInput(f"{what}: {table.num_rows} rows reached the {MAXREC} cap, so the cone was cut off")
    return table


def neighbourhood(transport: Transport, match: Mapping[str, Any], source_ids: Iterable[int]) -> dict[int, int]:
    """Earlier source_id -> later source_id, the nearest neighbour Gaia's table gives for each."""
    for key in ("table", "from_column", "to_column", "order_by"):
        if not str(match[key]).replace(".", "_").isidentifier():
            raise ValueError(f"not an identifier: {match[key]!r}")
    ids = sorted({int(i) for i in source_ids})
    best: dict[int, tuple[float, int]] = {}
    for start in range(0, len(ids), CHUNK):
        part = ids[start : start + CHUNK]
        query = (
            f"SELECT {match['from_column']}, {match['to_column']}, {match['order_by']} FROM {match['table']} "
            f"WHERE {match['from_column']} IN ({', '.join(str(i) for i in part)})"
        )
        table = run_sync(transport, sync_request(GAIA_TAP, query, CHUNK * 20), "Gaia neighbourhood")
        if table.num_rows >= CHUNK * 20:
            # a cut-off answer could lack a source's nearest neighbour, and the match is kept
            raise UntrustedInput(f"Gaia neighbourhood: {table.num_rows} rows reached the {CHUNK * 20} cap")
        for r in table.to_pylist():
            a, b, d = int(r[match["from_column"]]), int(r[match["to_column"]]), float(r[match["order_by"]])
            if a not in best or d < best[a][0]:
                best[a] = (d, b)
    return {a: b for a, (_, b) in best.items()}
