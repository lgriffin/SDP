"""CdsXMatchAdapter: CrossMatchService through the CDS X-Match service (§8, DSI-009).

Both canonical frames are uploaded as CSV with their row index, after the same epoch propagation the
CPU reference applies, so the service only does the angular join. X-Match returns every pair within
the radius; the adapter keeps the nearest right row per left row, as AstropyCrossMatch does. It must
agree with AstropyCrossMatch on the fixture before any gate uses it.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass

import astropy.units as u
import numpy as np
import pyarrow as pa

from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.kernel.ports.archives import CrossMatchResult

SERVICE_URL = "https://cdsxmatch.u-strasbg.fr/xmatch/api/v1/sync"
_MAS_TO_DEG = 1.0 / 3.6e6


def positions(table: pa.Table, epoch: u.Quantity | None) -> tuple[np.ndarray, np.ndarray]:
    ra = table.column("ra_deg").to_numpy(zero_copy_only=False).astype(float)
    dec = table.column("dec_deg").to_numpy(zero_copy_only=False).astype(float)
    if epoch is None or "pm_ra_cosdec_masyr" not in table.column_names:
        return ra, dec
    t0 = table.column("epoch_jyear").to_numpy(zero_copy_only=False).astype(float)
    pmra = np.nan_to_num(table.column("pm_ra_cosdec_masyr").to_numpy(zero_copy_only=False).astype(float))
    pmdec = np.nan_to_num(table.column("pm_dec_masyr").to_numpy(zero_copy_only=False).astype(float))
    dt = epoch.to_value(u.yr) - t0
    dec_new = dec + pmdec * dt * _MAS_TO_DEG
    return (ra + pmra * dt * _MAS_TO_DEG / np.cos(np.radians(dec))) % 360.0, dec_new


def _csv(prefix: str, ra: np.ndarray, dec: np.ndarray) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([f"i{prefix}", f"ra{prefix}", f"dec{prefix}"])
    for i, (r, d) in enumerate(zip(ra.tolist(), dec.tolist(), strict=True)):
        writer.writerow([i, repr(r), repr(d)])
    return out.getvalue().encode()


def multipart(fields: dict[str, str], uploads: dict[str, bytes], boundary: str) -> bytes:
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, content in uploads.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{name}.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n".encode()
            + content
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


@dataclass
class CdsXMatchAdapter:
    transport: Transport
    service_url: str = SERVICE_URL
    #: Fixed so the same inputs give the same request body, and so the same cassette.
    boundary: str = "sdp-xmatch-" + uuid.UUID(int=0).hex

    def request(self, left: pa.Table, right: pa.Table, radius: u.Quantity, epoch: u.Quantity | None) -> HttpRequest:
        lra, ldec = positions(left, epoch)
        rra, rdec = positions(right, epoch)
        fields = {
            "request": "xmatch",
            "distMaxArcsec": repr(float(radius.to_value(u.arcsec))),
            "RESPONSEFORMAT": "csv",
            "colRA1": "ra1",
            "colDec1": "dec1",
            "colRA2": "ra2",
            "colDec2": "dec2",
        }
        body = multipart(fields, {"cat1": _csv("1", lra, ldec), "cat2": _csv("2", rra, rdec)}, self.boundary)
        return HttpRequest(
            "POST", self.service_url, body, {"Content-Type": f"multipart/form-data; boundary={self.boundary}"}
        )

    def match(
        self, left: pa.Table, right: pa.Table, radius: u.Quantity, *, epoch: u.Quantity | None = None
    ) -> CrossMatchResult:
        if left.num_rows == 0 or right.num_rows == 0:
            return CrossMatchResult(np.array([], dtype=np.int64), np.array([], dtype=np.int64), np.array([]) * u.arcsec)
        response = self.transport.send(self.request(left, right, radius, epoch))
        if response.status != 200:
            raise TransportError(f"X-Match: HTTP {response.status}: {response.body[:200]!r}", response.status)
        best: dict[int, tuple[float, int]] = {}
        for row in csv.DictReader(io.StringIO(response.body.decode("utf-8"))):
            i, j, sep = int(row["i1"]), int(row["i2"]), float(row["angDist"])
            if i not in best or (sep, j) < best[i]:
                best[i] = (sep, j)
        order = sorted(best)
        return CrossMatchResult(
            np.asarray(order, dtype=np.int64),
            np.asarray([best[i][1] for i in order], dtype=np.int64),
            np.asarray([best[i][0] for i in order]) * u.arcsec,
        )
