"""Archive test doubles: a TAP service that answers the adapter's own chunk requests from a fixture table,
and an X-Match service that joins uploaded CSVs. Both are fakes of the remote, not recordings of it;
real recordings come from the weekly live job (ADR-015)."""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pyarrow as pa

from sdp.adapters.archives.tap import TapArchiveAdapter, TapSource, load_sources
from sdp.adapters.archives.transport import CassetteMissing, HttpRequest, HttpResponse
from sdp.adapters.archives.votable import to_votable
from sdp.kernel.domain.archive import AcquisitionHandle, CatalogueQuery, Cone
from sdp.testing.fakes.archives import apply_query

GAIA = load_sources()["gaia"]


def gaia_native(n: int = 120, seed: int = 3) -> pa.Table:
    """Native-shaped Gaia rows near (45, -12); invented values, for exercising the adapter only."""
    rng = np.random.default_rng(seed)
    cols: dict[str, pa.Array] = {}
    for c in GAIA.descriptor.columns:
        if c.name == "source_id":
            cols[c.name] = pa.array(np.arange(1, n + 1, dtype=np.int64) * 1000)
        elif c.name == "ra":
            cols[c.name] = pa.array(45 + rng.uniform(-0.3, 0.3, n))
        elif c.name == "dec":
            cols[c.name] = pa.array(-12 + rng.uniform(-0.3, 0.3, n))
        elif c.name == "ref_epoch":
            cols[c.name] = pa.array(np.full(n, 2016.0))
        elif c.datatype == "boolean":
            cols[c.name] = pa.array(rng.uniform(size=n) < 0.1)
        elif c.datatype == "short":
            cols[c.name] = pa.array(np.full(n, 31, dtype=np.int16))
        elif c.name.endswith("_corr"):
            cols[c.name] = pa.array(rng.uniform(-0.5, 0.5, n))
        else:
            cols[c.name] = pa.array(rng.uniform(0.01, 5.0, n))
    return pa.table(cols)


def gaia_query(**overrides: object) -> CatalogueQuery:
    params: dict[str, object] = {
        "source_id": "gaia",
        "release_label": "gaia:DR3",
        "region": Cone(45.0, -12.0, 0.25),
        "columns": GAIA.descriptor.column_names(),
        "row_cap": 200,
        "chunk_rows": 25,
    }
    params.update(overrides)
    return CatalogueQuery(**params)  # type: ignore[arg-type]


@dataclass
class FixtureTap:
    """Answers exactly the requests the adapter makes for the handles it was primed with."""

    responses: dict[str, HttpResponse] = field(default_factory=dict)
    calls: int = 0

    def prime(
        self, adapter: TapArchiveAdapter, handle: AcquisitionHandle, native: pa.Table, source: TapSource = GAIA
    ) -> None:
        q = handle.query
        result = apply_query(native, q, source.ra_column, source.dec_column)
        part = 0
        while True:
            chunk = result.slice(part * q.chunk_rows, q.chunk_rows)
            self.responses[adapter.chunk_request(handle, part).key()] = HttpResponse(
                200, to_votable(chunk), {"Content-Type": "application/x-votable+xml"}
            )
            if chunk.num_rows < q.chunk_rows or (part + 1) * q.chunk_rows >= q.row_cap:
                break
            part += 1

    def send(self, request: HttpRequest) -> HttpResponse:
        self.calls += 1
        if request.key() not in self.responses:
            raise CassetteMissing(f"fixture TAP was not primed for {request.url}")
        return self.responses[request.key()]


def _form(body: bytes, boundary: str) -> Mapping[str, bytes]:
    out: dict[str, bytes] = {}
    for part in body.split(f"--{boundary}".encode()):
        if b"\r\n\r\n" not in part:
            continue
        head, _, content = part.partition(b"\r\n\r\n")
        name = head.decode().split('name="')[1].split('"')[0]
        out[name] = content.removesuffix(b"\r\n")
    return out


@dataclass
class FakeXMatchService:
    """Every pair within distMaxArcsec, as the CDS service returns them (angDist first)."""

    def send(self, request: HttpRequest) -> HttpResponse:
        boundary = request.headers["Content-Type"].split("boundary=")[1]
        form = _form(request.body, boundary)
        radius = float(form["distMaxArcsec"])
        left = list(csv.DictReader(io.StringIO(form["cat1"].decode())))
        right = list(csv.DictReader(io.StringIO(form["cat2"].decode())))
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["angDist", "i1", "ra1", "dec1", "i2", "ra2", "dec2"])
        for a in left:
            for b in right:
                sep = _separation_arcsec(float(a["ra1"]), float(a["dec1"]), float(b["ra2"]), float(b["dec2"]))
                if sep <= radius:
                    writer.writerow([f"{sep:.6f}", a["i1"], a["ra1"], a["dec1"], b["i2"], b["ra2"], b["dec2"]])
        return HttpResponse(200, out.getvalue().encode(), {"Content-Type": "text/csv"})


def _separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    h = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(math.sqrt(h))) * 3600
