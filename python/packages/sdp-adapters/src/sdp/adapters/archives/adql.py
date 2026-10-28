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


def region_clause(region: Cone | Box, ra: str, dec: str) -> str:
    if isinstance(region, Cone):
        return (
            f"1 = CONTAINS(POINT('ICRS', {ra}, {dec}), "
            f"CIRCLE('ICRS', {literal(region.ra_deg)}, {literal(region.dec_deg)}, {literal(region.radius_deg)}))"
        )
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
