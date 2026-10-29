"""Cutouts of FITS images read by byte range (Integration v0.4.0 §3, INT-004).

An archive that publishes no SODA service can still serve a cutout when its host answers HTTP range
requests: a FITS file is headers in 2880-byte blocks followed by its pixels row by row, so SDP reads
the headers, finds the image and its WCS, and fetches only the rows around the position. The columns
outside the square are dropped before anything is stored. The pixels are copied as the archive wrote
them (no rescaling), and the header records where they were cut from.

Only uncompressed 2-D images are cut; a tile-compressed image or a table raises ``RangeUnsupported``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError

BLOCK = 2880
CARD = 80
#: Headers are read this many blocks at a time.
HEADER_CHUNK = 10 * BLOCK
#: Rows are fetched in requests of at most this many bytes.
ROW_CHUNK = 16 * 1024**2
#: The most a cutout may read across the wire, rows included; a wider image needs a smaller square.
MAX_READ_BYTES = 512 * 1024**2
MAX_HDUS = 16


class RangeUnsupported(ValueError):
    """The host or the file cannot be cut by range."""


@dataclass(frozen=True)
class RangeCutout:
    data: bytes
    """A FITS file: the source's primary header, then the cut image."""
    extension: str
    x0: int
    y0: int
    width: int
    height: int
    pixel_arcsec: float
    bytes_read: int


def range_request(url: str, start: int, end: int) -> HttpRequest:
    return HttpRequest("GET", url, headers={"Range": f"bytes={start}-{end}"})


def read_range(transport: Transport, url: str, start: int, end: int) -> bytes:
    """Bytes ``start`` to ``end`` inclusive; a host that ignores the range is refused, not trusted."""
    response = transport.send(range_request(url, start, end))
    if response.status != 206:
        raise RangeUnsupported(f"{url} answered a byte-range request with HTTP {response.status}, not 206")
    want = end - start + 1
    if len(response.body) > want:
        raise RangeUnsupported(f"{url} sent {len(response.body)} bytes for a {want}-byte range")
    return response.body


def range_status(transport: Transport, url: str) -> int | None:
    """The status a one-block range request gets: 206 means the product can be cut by range."""
    try:
        return transport.send(range_request(url, 0, BLOCK - 1)).status
    except TransportError as exc:
        return exc.status


def _header(transport: Transport, url: str, offset: int) -> tuple[fits.Header, bytes]:
    """The header starting at ``offset`` and its raw blocks (END card included)."""
    raw = b""
    while True:
        chunk = read_range(transport, url, offset + len(raw), offset + len(raw) + HEADER_CHUNK - 1)
        if not chunk:
            raise RangeUnsupported(f"{url} ended inside a header at byte {offset + len(raw)}")
        raw += chunk
        for i in range(0, len(raw) - CARD + 1, CARD):
            if raw[i : i + 8] == b"END     ":
                blocks = raw[: math.ceil((i + CARD) / BLOCK) * BLOCK]
                return fits.Header.fromstring(blocks.decode("ascii")), blocks
        if len(chunk) < HEADER_CHUNK:
            raise RangeUnsupported(f"{url} has a header with no END card")


def _data_bytes(header: fits.Header) -> int:
    naxis = int(header.get("NAXIS", 0))
    if naxis == 0:
        return 0
    count = math.prod(int(header[f"NAXIS{i}"]) for i in range(1, naxis + 1))
    size = abs(int(header["BITPIX"])) // 8 * int(header.get("GCOUNT", 1)) * (int(header.get("PCOUNT", 0)) + count)
    return math.ceil(size / BLOCK) * BLOCK


def _is_image(header: fits.Header, primary: bool) -> bool:
    kind = "IMAGE" if primary else str(header.get("XTENSION", "")).strip()
    return kind == "IMAGE" and int(header.get("NAXIS", 0)) == 2 and not header.get("ZIMAGE")


def range_cutout(
    transport: Transport,
    url: str,
    ra_deg: float,
    dec_deg: float,
    side_arcsec: float,
    *,
    extension: str = "SCI",
    max_read_bytes: int = MAX_READ_BYTES,
) -> RangeCutout:
    """The square of ``side_arcsec`` around the position, from the image extension named
    ``extension`` (else the first 2-D image), reading only headers and the rows it covers."""
    primary, primary_blocks = _header(transport, url, 0)
    offset = len(primary_blocks)
    candidates: list[tuple[fits.Header, int, bool]] = []
    if _is_image(primary, True):
        candidates.append((primary, offset, True))
    offset += _data_bytes(primary)
    for _ in range(MAX_HDUS):
        try:
            header, blocks = _header(transport, url, offset)
        except RangeUnsupported:
            break  # past the last HDU
        if header.get("ZIMAGE") and str(header.get("EXTNAME", "")).strip() == extension:
            raise RangeUnsupported(f"{extension} in {url} is tile-compressed; a range cannot cut it")
        if _is_image(header, False):
            candidates.append((header, offset + len(blocks), False))
            if str(header.get("EXTNAME", "")).strip() == extension:
                break
        offset += len(blocks) + _data_bytes(header)
    if not candidates:
        raise RangeUnsupported(f"{url} holds no uncompressed 2-D image")
    named = [c for c in candidates if str(c[0].get("EXTNAME", "")).strip() == extension]
    header, data_offset, in_primary = (named or candidates)[0]

    wcs = WCS(header).celestial
    if not wcs.has_celestial:
        raise RangeUnsupported(f"the image in {url} has no celestial WCS to cut by")
    scale = float(min(proj_plane_pixel_scales(wcs))) * 3600.0
    nx, ny = int(header["NAXIS1"]), int(header["NAXIS2"])
    x, y = (float(v) for v in wcs.world_to_pixel_values(ra_deg, dec_deg))
    half = side_arcsec / scale / 2.0
    x0, x1 = max(0, math.floor(x - half)), min(nx, math.ceil(x + half) + 1)
    y0, y1 = max(0, math.floor(y - half)), min(ny, math.ceil(y + half) + 1)
    if x0 >= x1 or y0 >= y1:
        raise LookupError(f"({ra_deg:.6f}, {dec_deg:.6f}) is off the image in {url}")

    pixel = abs(int(header["BITPIX"])) // 8
    row = nx * pixel
    if (y1 - y0) * row > max_read_bytes:
        raise RangeUnsupported(
            f"cutting {side_arcsec} arcsec from {url} reads {(y1 - y0) * row} bytes of rows, over {max_read_bytes}"
        )
    band = bytearray()
    rows_per_request = max(1, ROW_CHUNK // row)
    for start in range(y0, y1, rows_per_request):
        stop = min(y1, start + rows_per_request)
        part = read_range(transport, url, data_offset + start * row, data_offset + stop * row - 1)
        if len(part) != (stop - start) * row:
            raise RangeUnsupported(f"{url} returned {len(part)} bytes for rows {start} to {stop}")
        band += part
    width, height = x1 - x0, y1 - y0
    pixels = b"".join(band[r * row + x0 * pixel : r * row + x1 * pixel] for r in range(height))

    cut = header.copy()
    cut["NAXIS1"], cut["NAXIS2"] = width, height
    for key in ("CRPIX1", "CRPIX2"):
        if key in cut:
            cut[key] = float(cut[key]) - (x0 if key == "CRPIX1" else y0)
    for key in ("CHECKSUM", "DATASUM"):
        cut.remove(key, ignore_missing=True)
    cut["SDPCUT"] = ("range", "SDP cut this image by HTTP byte range (INT-004)")
    cut["SDPCUTX0"] = (x0, "first column of the source image (0-based)")
    cut["SDPCUTY0"] = (y0, "first row of the source image (0-based)")
    cut.add_history(f"SDP cutout of {side_arcsec:g} arcsec at ({ra_deg:.6f}, {dec_deg:.6f}) from {url}")
    body = pixels + bytes(-len(pixels) % BLOCK)
    if in_primary:
        data = cut.tostring(padding=True).encode("ascii") + body
    else:
        head = primary_blocks
        if int(primary.get("NAXIS", 0)):
            # The primary's own data is not copied, so its header must say it has none.
            bare = primary.copy()
            for i in range(1, int(primary["NAXIS"]) + 1):
                bare.remove(f"NAXIS{i}", ignore_missing=True)
            bare["NAXIS"] = 0
            head = bare.tostring(padding=True).encode("ascii")
        data = head + cut.tostring(padding=True).encode("ascii") + body
    name = str(header.get("EXTNAME", "PRIMARY" if in_primary else "")).strip()
    return RangeCutout(data, name, x0, y0, width, height, scale, len(primary_blocks) + len(band))
