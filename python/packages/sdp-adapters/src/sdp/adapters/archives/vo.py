"""One synchronous TAP query through a Transport: the request the VO adapters send, and its table.

The catalogue gateway pages with ``TapArchiveAdapter``; the Investigate adapters (SIMBAD, the VO
registry, MAST ObsCore) ask small bounded questions and use this instead, so every one of them
replays from cassettes in CI exactly as the catalogue gateway does (ADR-015).
"""

from __future__ import annotations

import urllib.parse

import pyarrow as pa

from sdp.adapters.archives import votable
from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError


def sync_request(service_url: str, adql: str, maxrec: int) -> HttpRequest:
    body = urllib.parse.urlencode(
        {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "votable", "MAXREC": str(maxrec), "QUERY": adql}
    ).encode()
    return HttpRequest("POST", f"{service_url}/sync", body, {"Content-Type": "application/x-www-form-urlencoded"})


def run_sync(transport: Transport, request: HttpRequest, what: str) -> pa.Table:
    """Send the query and parse its VOTable; an HTTP error or a TAP error document raises."""
    response = transport.send(request)
    if response.status != 200:
        raise TransportError(f"{what}: HTTP {response.status}", response.status)
    return votable.parse(response.body)


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
