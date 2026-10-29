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
    """Pixels averaged in square blocks so neither side exceeds ``max_side``. The last row and column
    of blocks are partial rather than dropped, so the picture still spans the whole pixel grid and an
    overlay stretched over it stays on its stars."""
    factor = max(1, -(-max(pixels.shape) // max_side))
    if factor == 1:
        return pixels
    h, w = -(-pixels.shape[0] // factor) * factor, -(-pixels.shape[1] // factor) * factor
    padded = np.full((h, w), np.nan)
    padded[: pixels.shape[0], : pixels.shape[1]] = pixels
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # an all-NaN block stays NaN
        return np.nanmean(padded.reshape(h // factor, factor, w // factor, factor), axis=(1, 3))


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
