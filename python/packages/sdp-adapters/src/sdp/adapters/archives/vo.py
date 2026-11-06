"""One synchronous TAP query through a Transport: the request the VO adapters send, and its table.

The catalogue gateway pages with ``TapArchiveAdapter``; the Investigate adapters (SIMBAD, the VO
registry, MAST ObsCore) ask small bounded questions and use this instead, so every one of them
replays from cassettes in CI exactly as the catalogue gateway does (ADR-015).
"""

from __future__ import annotations

import contextlib
import re
import time
import urllib.parse
from collections.abc import Callable

import pyarrow as pa

from sdp.adapters.archives import votable
from sdp.adapters.archives.transport import HttpRequest, HttpResponse, Transport, TransportError
from sdp.adapters.archives.untrusted import validate_votable


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
    validate_votable(response.body)
    return votable.parse(response.body)


def async_request(service_url: str, adql: str, maxrec: int) -> HttpRequest:
    """A TAP async job (UWS) for the query, created already running."""
    body = urllib.parse.urlencode(
        {
            "REQUEST": "doQuery",
            "LANG": "ADQL",
            "FORMAT": "votable",
            "MAXREC": str(maxrec),
            "QUERY": adql,
            "PHASE": "RUN",
        }
    ).encode()
    return HttpRequest("POST", f"{service_url}/async", body, {"Content-Type": "application/x-www-form-urlencoded"})


_JOB_ID = re.compile(rb"jobId>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)")
_RUN = urllib.parse.urlencode({"PHASE": "RUN"}).encode()
_ABORT = urllib.parse.urlencode({"PHASE": "ABORT"}).encode()
_DELETE = urllib.parse.urlencode({"ACTION": "DELETE"}).encode()
_FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def _job_url(service_url: str, response: HttpResponse, what: str) -> str:
    """Where the new job lives: the redirect's Location, or the job document's jobId when the transport
    followed the redirect (urllib does)."""
    location = next((v for k, v in response.headers.items() if k.lower() == "location"), None)
    if response.status in (301, 302, 303) and location:
        return urllib.parse.urljoin(f"{service_url}/async", location).rstrip("/")
    if response.status in (200, 201):
        match = _JOB_ID.search(response.body)
        if match:
            return f"{service_url}/async/{match.group(1).decode()}"
    raise TransportError(f"{what}: the async job was not created (HTTP {response.status})", response.status)


def run_async(
    transport: Transport,
    service_url: str,
    adql: str,
    maxrec: int,
    what: str,
    *,
    poll_s: float = 5.0,
    max_wait_s: float = 3600.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> pa.Table:
    """Run the query as a TAP async job and parse its VOTable (#199). Nothing waits on one open request
    while the server works: the job is polled, at most every minute, until it completes, fails or
    ``max_wait_s`` passes (then it is aborted and a 408 raised, which callers treat as a timeout)."""
    job = _job_url(service_url, transport.send(async_request(service_url, adql, maxrec)), what)
    started = monotonic()
    wait = poll_s
    started_run = False
    while True:
        response = transport.send(HttpRequest("GET", f"{job}/phase"))
        if response.status != 200:
            raise TransportError(f"{what}: job phase HTTP {response.status}", response.status)
        phase = response.body.decode("utf-8", "replace").strip().upper()
        if phase == "COMPLETED":
            break
        if phase in ("ERROR", "ABORTED"):
            detail = transport.send(HttpRequest("GET", f"{job}/error"))
            text = detail.body.decode("utf-8", "replace").strip() if detail.status == 200 else ""
            # an ERROR is the service refusing the query (as a 400 would); an abort may be the service shedding load
            _delete(transport, job)
            raise TransportError(
                f"{what}: the async job ended {phase}: {text[:500]}", 400 if phase == "ERROR" else None
            )
        if phase == "PENDING" and not started_run:  # a service that ignored PHASE=RUN at creation
            transport.send(HttpRequest("POST", f"{job}/phase", _RUN, _FORM))
            started_run = True
        if monotonic() - started > max_wait_s:
            with contextlib.suppress(TransportError):
                transport.send(HttpRequest("POST", f"{job}/phase", _ABORT, _FORM))
            _delete(transport, job)
            raise TransportError(f"{what}: no result after {max_wait_s:.0f} s (job {job})", 408)
        sleep(wait)
        wait = min(wait * 1.5, 60.0)
    try:
        response = transport.send(HttpRequest("GET", f"{job}/results/result"))
        if response.status != 200:
            raise TransportError(f"{what}: result HTTP {response.status}", response.status)
        validate_votable(response.body)
        return votable.parse(response.body)
    finally:
        _delete(transport, job)


def _delete(transport: Transport, job: str) -> None:
    """Remove the job and its stored result from the service (UWS ACTION=DELETE), so repeated runs leave
    nothing behind under the archive's job quota. Best effort: a job left there expires by itself."""
    with contextlib.suppress(TransportError):
        transport.send(HttpRequest("POST", job, _DELETE, _FORM))


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
