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

    def table(self, request: HttpRequest, rows: dict[str, object]) -> None:
        table = pa.table(rows)
        body = to_votable(table) if table.num_rows else empty_votable(table.column_names)
        self.responses[request.key()] = HttpResponse(200, body, VOTABLE)

    def raw(self, request: HttpRequest, body: bytes, status: int = 200, content_type: str = "text/xml") -> None:
        self.responses[request.key()] = HttpResponse(status, body, {"Content-Type": content_type})

    def send(self, request: HttpRequest) -> HttpResponse:
        self.calls += 1
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
