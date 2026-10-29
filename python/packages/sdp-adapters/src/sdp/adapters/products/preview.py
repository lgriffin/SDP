"""A picture of a FITS image for the evidence UI: its science pixels, stretched for the eye (INV-020).

The first image HDU named SCI (else the first with 2-D data) is scaled with an asinh stretch between
its 0.5 and 99.8 percentiles, pixel y upward as FITS viewers draw it (not rotated to north up: the
WCS is not read): the PNG's top row is the image's last row, so pixel (x, y)
of the FITS file is drawn at (x, height - 1 - y). Pixels without a value (NaN, as at a mosaic's edge)
are drawn black. An image larger than ``max_side`` is binned by a whole factor, and the picture keeps
the image's aspect, so a viewer stretching it over the full pixel grid draws overlays in place.
The PNG is written with zlib alone, so no imaging library is needed. Nothing here is analysis: the
tests never read it.
"""

from __future__ import annotations

import io
import struct
import warnings
import zlib

import numpy as np
from astropy.io import fits

MAX_SIDE = 2048


def science_pixels(data: bytes) -> np.ndarray:
    """The 2-D science array of a FITS image; ``ValueError`` when it has none."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            hdul = fits.open(io.BytesIO(data), memmap=False)
        except (OSError, TypeError) as exc:
            raise ValueError(f"not a readable FITS file: {exc}") from exc
        with hdul:
            images = [h for h in hdul if h.data is not None and getattr(h.data, "ndim", 0) == 2]
            chosen = next((h for h in images if h.name == "SCI"), images[0] if images else None)
            if chosen is None:
                raise ValueError("the file has no 2-D image to draw")
            return np.asarray(chosen.data, dtype=float)


def stretch(pixels: np.ndarray) -> np.ndarray:
    """0-255 grey levels: asinh between the 0.5 and 99.8 percentiles; no value is black."""
    finite = np.isfinite(pixels)
    if not finite.any():
        return np.zeros(pixels.shape, dtype=np.uint8)
    low, high = np.percentile(pixels[finite], [0.5, 99.8])
    span = high - low if high > low else 1.0
    scaled = np.clip((np.where(finite, pixels, low) - low) / span, 0.0, 1.0)
    soft = 0.1  # asinh softening: faint stars stay visible without burning out the bright ones
    grey = np.arcsinh(scaled / soft) / np.arcsinh(1.0 / soft)
    return np.where(finite, np.round(grey * 255.0), 0).astype(np.uint8)


def binned(pixels: np.ndarray, max_side: int = MAX_SIDE) -> np.ndarray:
    """Pixels averaged in blocks so neither side exceeds ``max_side``. Block edges are spread evenly
    over the whole grid (each block within one pixel of the same size), so every picture pixel covers
    the same share of the image and an overlay stretched over the picture stays on its stars."""
    factor = max(1, -(-max(pixels.shape) // max_side))
    if factor == 1:
        return pixels
    height, width = pixels.shape
    rows = np.round(np.linspace(0, height, -(-height // factor) + 1)).astype(int)[:-1]
    cols = np.round(np.linspace(0, width, -(-width // factor) + 1)).astype(int)[:-1]
    finite = np.isfinite(pixels)
    sums = np.add.reduceat(np.add.reduceat(np.where(finite, pixels, 0.0), rows, axis=0), cols, axis=1)
    counts = np.add.reduceat(np.add.reduceat(finite.astype(float), rows, axis=0), cols, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(counts > 0, sums / np.where(counts > 0, counts, 1.0), np.nan)


def png(grey: np.ndarray) -> bytes:
    """An 8-bit greyscale PNG of ``grey``, top row first."""
    height, width = grey.shape
    raw = b"".join(b"\x00" + grey[row].tobytes() for row in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def preview_png(data: bytes, max_side: int = MAX_SIDE) -> bytes:
    """The picture of a FITS image, pixel y upward."""
    return png(stretch(binned(science_pixels(data), max_side))[::-1])
