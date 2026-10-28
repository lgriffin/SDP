"""Schema drift: what the archive's TAP_SCHEMA says now against the last recording (DSI-008, T11).

The weekly live job asks each service for its table's column list, compares it with the committed
cassette of the same request (the previous recording) and with the source's declared columns, and
fails naming every column that was added, removed or retyped.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping

from sdp.adapters.archives import adql, votable
from sdp.adapters.archives.tap import TapSource
from sdp.adapters.archives.transport import HttpRequest


def tap_schema_request(source: TapSource) -> HttpRequest:
    text = (
        "SELECT column_name, datatype FROM TAP_SCHEMA.columns "
        f"WHERE table_name = {adql.literal(source.descriptor.table)} ORDER BY column_name"
    )
    body = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "votable", "QUERY": text}).encode()
    return HttpRequest(
        "POST", f"{source.service_url}/sync", body, {"Content-Type": "application/x-www-form-urlencoded"}
    )


def columns_from(body: bytes) -> dict[str, str]:
    table = votable.parse(body)
    names = table.column("column_name").to_pylist()
    types = table.column("datatype").to_pylist()
    return {str(n).strip(): str(t).strip() for n, t in zip(names, types, strict=True)}


def schema_drift(source: TapSource, previous: Mapping[str, str] | None, current: Mapping[str, str]) -> list[str]:
    """One line per drifted column, naming it; empty when nothing changed."""
    sid = source.descriptor.source_id
    problems: list[str] = []
    if previous is not None:
        for name in sorted(set(current) - set(previous)):
            problems.append(f"{sid}: column {name} added ({current[name]})")
        for name in sorted(set(previous) - set(current)):
            problems.append(f"{sid}: column {name} removed or renamed")
        for name in sorted(set(previous) & set(current)):
            if previous[name] != current[name]:
                problems.append(f"{sid}: column {name} retyped from {previous[name]} to {current[name]}")
    for column in source.descriptor.columns:
        if column.name not in current:
            problems.append(f"{sid}: declared column {column.name} is not in the archive's table")
    return sorted(set(problems))
