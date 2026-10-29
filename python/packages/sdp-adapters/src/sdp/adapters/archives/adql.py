"""ADQL rendering of a CatalogueQuery (§8). The query is a value object; this is the only place it
becomes text, and the text is what the acquisition records and keys on (DSI-002)."""

from __future__ import annotations

import math

from sdp.kernel.domain.archive import Box, CatalogueQuery, Cone, Filter

_ADQL_OPS = {"<": "<", "<=": "<=", ">": ">", ">=": ">=", "=": "=", "!=": "<>"}


def literal(value: float | int | str | None) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("ADQL literals must be finite")
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise ValueError(f"no ADQL literal for {value!r}")


#: From this radius up, a cone also gets a BETWEEN bounding box. The Gaia archive answers CONTAINS alone
#: in about 2 s at 1 degree but times out (60 s) at 1.5 degrees, while the same circle behind a bounding box
#: answers in about 6 s (measured 2026-10-29). Smaller cones keep their text, and so their recorded cassettes.
PREFILTER_FROM_DEG = 0.5


def cone_bounds(cone: Cone) -> tuple[tuple[float, float] | None, float, float]:
    """The RA interval (None when the cone covers every RA) and Dec limits of a box holding the cone.
    The RA interval may run past 0 or 360; ``ra_clause`` folds it back."""
    dec_min = max(-90.0, _down(cone.dec_deg - cone.radius_deg))
    dec_max = min(90.0, _up(cone.dec_deg + cone.radius_deg))
    if dec_min <= -90.0 or dec_max >= 90.0:
        return None, dec_min, dec_max
    # the widest RA a circle reaches, exactly: sin(half_width) = sin(r) / cos(dec)
    ratio = math.sin(math.radians(cone.radius_deg)) / math.cos(math.radians(cone.dec_deg))
    if ratio >= 1.0:
        return None, dec_min, dec_max
    half = math.degrees(math.asin(ratio))
    return (_down(cone.ra_deg - half), _up(cone.ra_deg + half)), dec_min, dec_max


#: Bounds are rounded outwards to a microdegree (3.6 mas): short query text, and never inside the circle.
def _down(value: float) -> float:
    return math.floor(value * 1e6 - 1e-6) / 1e6


def _up(value: float) -> float:
    return math.ceil(value * 1e6 + 1e-6) / 1e6


def ra_clause(lo: float, hi: float, ra: str) -> str:
    if lo < 0.0:
        return f"({ra} >= {literal(lo + 360.0)} OR {ra} <= {literal(hi)})"
    if hi >= 360.0:
        return f"({ra} >= {literal(lo)} OR {ra} <= {literal(hi - 360.0)})"
    return f"{ra} BETWEEN {literal(lo)} AND {literal(hi)}"


def region_clause(region: Cone | Box, ra: str, dec: str) -> str:
    if isinstance(region, Cone):
        contains = (
            f"1 = CONTAINS(POINT('ICRS', {ra}, {dec}), "
            f"CIRCLE('ICRS', {literal(region.ra_deg)}, {literal(region.dec_deg)}, {literal(region.radius_deg)}))"
        )
        if region.radius_deg < PREFILTER_FROM_DEG:
            return contains
        ra_range, dec_min, dec_max = cone_bounds(region)
        prefilter = [f"{dec} BETWEEN {literal(dec_min)} AND {literal(dec_max)}"]
        if ra_range is not None:
            prefilter.insert(0, ra_clause(*ra_range, ra))
        return " AND ".join([*prefilter, contains])
    return (
        f"{ra} BETWEEN {literal(region.ra_min_deg)} AND {literal(region.ra_max_deg)} "
        f"AND {dec} BETWEEN {literal(region.dec_min_deg)} AND {literal(region.dec_max_deg)}"
    )


def filter_clause(f: Filter) -> str:
    if f.op == "is_not_null":
        return f"{f.column} IS NOT NULL"
    return f"{f.column} {_ADQL_OPS[f.op]} {literal(f.value)}"


def render(
    q: CatalogueQuery, *, table: str, ra: str, dec: str, order_by: str, top: int, offset: int | None = None
) -> str:
    where = " AND ".join([region_clause(q.region, ra, dec), *(filter_clause(f) for f in q.filters)])
    text = f"SELECT TOP {top} {', '.join(q.columns)} FROM {table} WHERE {where} ORDER BY {order_by}"
    if offset:
        text += f" OFFSET {offset}"
    return text
