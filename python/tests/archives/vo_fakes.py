"""Fakes of the VO services the Investigate adapters call: they answer exactly the requests they were
primed with, from invented values. They are fakes of the remote, not recordings of it; recordings come
from the weekly live job (ADR-015)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pyarrow as pa

from sdp.adapters.archives.transport import CassetteMissing, HttpRequest, HttpResponse
from sdp.adapters.archives.votable import to_votable

VOTABLE = {"Content-Type": "application/x-votable+xml"}


@dataclass
class PrimedService:
    responses: dict[str, HttpResponse] = field(default_factory=dict)
    calls: int = 0
    #: the key of every request sent, in order
    sent: list[str] = field(default_factory=list)

    def table(self, request: HttpRequest, rows: dict[str, object]) -> None:
        table = pa.table(rows)
        body = to_votable(table) if table.num_rows else empty_votable(table.column_names)
        self.responses[request.key()] = HttpResponse(200, body, VOTABLE)

    def raw(self, request: HttpRequest, body: bytes, status: int = 200, content_type: str = "text/xml") -> None:
        self.responses[request.key()] = HttpResponse(status, body, {"Content-Type": content_type})

    def send(self, request: HttpRequest) -> HttpResponse:
        self.calls += 1
        self.sent.append(request.key())
        if request.key() not in self.responses:
            raise CassetteMissing(f"fake service was not primed for {request.method} {request.url}")
        return self.responses[request.key()]


def empty_votable(columns: list[str]) -> bytes:
    """A result with fields and no rows, as a TAP service answers a query that matched nothing."""
    fields = "".join(f'<FIELD name="{c}" datatype="char" arraysize="*"/>' for c in columns)
    return (
        '<?xml version="1.0"?><VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">'
        '<RESOURCE type="results"><INFO name="QUERY_STATUS" value="OK"/>'
        f"<TABLE>{fields}<DATA><TABLEDATA></TABLEDATA></DATA></TABLE></RESOURCE></VOTABLE>"
    ).encode()


def sesame(oname: str | None, otype: str = "", ra: float | None = None, dec: float | None = None) -> bytes:
    """A Sesame XML answer shaped like CDS's: a Resolver element, with oname only when SIMBAD matched."""
    inner = (
        f"<oname>{oname}</oname><otype>{otype}</otype><jradeg>{ra}</jradeg><jdedeg>{dec}</jdedeg>"
        if oname
        else ("<INFO>*** Nothing found ***</INFO>")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Sesame><Target option="S"><name>q</name>'
        f'<Resolver name="S=Simbad (via url)">{inner}</Resolver></Target></Sesame>'
    ).encode()


@dataclass
class AsyncTapService:
    """A TAP service's async (UWS) side: each primed query becomes a job that reports the given phases
    in turn (the last one repeats) and then serves its table. ``redirect`` answers the creation with a
    303 and a Location, as a server does; otherwise with the job document, as urllib shows it after
    following that redirect."""

    service_url: str
    jobs: dict[str, dict[str, object]] = field(default_factory=dict)
    #: method and URL of every request, in order
    sent: list[str] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)

    def prime(
        self,
        adql: str,
        maxrec: int,
        rows: dict[str, object],
        phases: tuple[str, ...] = ("EXECUTING", "COMPLETED"),
        error: str = "",
        redirect: bool = False,
    ) -> None:
        from sdp.adapters.archives.vo import async_request

        table = pa.table(rows)
        job_id = f"job{len(self.jobs) + 1}"
        self.jobs[async_request(self.service_url, adql, maxrec).key()] = {
            "id": job_id,
            "phases": list(phases),
            "body": to_votable(table) if table.num_rows else empty_votable(table.column_names),
            "error": error,
            "redirect": redirect,
        }

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def _job(self, job_id: str) -> dict[str, object]:
        return next(j for j in self.jobs.values() if j["id"] == job_id)

    def send(self, request: HttpRequest) -> HttpResponse:
        self.sent.append(f"{request.method} {request.url}")
        base = f"{self.service_url}/async"
        if request.method == "POST" and request.url == base:
            job = self.jobs.get(request.key())
            if job is None:
                raise CassetteMissing(f"no async job primed for {request.body[:120]!r}")
            if job["redirect"]:
                return HttpResponse(303, b"", {"Location": f"{base}/{job['id']}"})
            doc = f'<uws:job xmlns:uws="http://www.ivoa.net/xml/UWS/v1.0"><uws:jobId><![CDATA[{job["id"]}]]></uws:jobId></uws:job>'
            return HttpResponse(200, doc.encode(), {"Content-Type": "text/xml"})
        if not request.url.startswith(base + "/"):
            raise CassetteMissing(f"fake async service was not primed for {request.method} {request.url}")
        job_id, _, rest = request.url[len(base) + 1 :].partition("/")
        job = self._job(job_id)
        phases = job["phases"]
        assert isinstance(phases, list)
        if rest == "" and request.method == "POST" and b"ACTION=DELETE" in request.body:
            job["deleted"] = True
            return HttpResponse(303, b"", {"Location": base})
        if rest == "phase" and request.method == "GET":
            phase = phases.pop(0) if len(phases) > 1 else phases[0]
            return HttpResponse(200, str(phase).encode(), {"Content-Type": "text/plain"})
        if rest == "phase" and request.method == "POST":
            if b"ABORT" in request.body:
                job["phases"] = ["ABORTED"]
            return HttpResponse(200, b"")
        if rest == "error":
            return HttpResponse(200, str(job["error"]).encode(), {"Content-Type": "text/plain"})
        if rest == "results/result":
            body = job["body"]
            assert isinstance(body, bytes)
            return HttpResponse(200, body, VOTABLE)
        raise CassetteMissing(f"fake async service has no {request.method} {request.url}")
