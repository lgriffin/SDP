"""A synthetic Vespera-like frame: a raw Bayer image of planted Gaia stars at a known WCS, so the plate
solver can be checked against the truth. Invented values only; nothing is read from an archive."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from astropy.wcs import WCS

from sdp.adapters.local.solver import Reference, deproject

from .fits_files import write

#: M31's catalogue position, the OBJECT the frames name
M31 = (10.684708, 41.268750)


@dataclass(frozen=True)
class Truth:
    wcs: WCS
    header: dict[str, Any]
    reference: Reference
    path: Path


def gaia_field(centre: tuple[float, float], radius_deg: float, per_deg2: float, seed: int) -> tuple[np.ndarray, ...]:
    """Stars spread evenly on the sky in a cone, G from 7 to 16.5 with counts growing as stars get fainter."""
    rng = np.random.default_rng(seed)
    count = int(per_deg2 * math.pi * radius_deg**2)
    r = radius_deg * np.sqrt(rng.random(count))
    angle = rng.random(count) * 2 * math.pi
    ra, dec = deproject(r * np.cos(angle), r * np.sin(angle), *centre)
    u = rng.random(count)
    lo, hi, slope = 7.0, 16.5, 0.35  # N(<m) grows as 10^(0.35 m)
    mag = np.log10(10 ** (slope * lo) + u * (10 ** (slope * hi) - 10 ** (slope * lo))) / slope
    return ra, dec, mag


def truth_wcs(
    centre: tuple[float, float], width: int, height: int, scale_arcsec: float, rotation_deg: float, mirrored: bool
) -> dict[str, Any]:
    s = scale_arcsec / 3600.0
    c, n = math.cos(math.radians(rotation_deg)), math.sin(math.radians(rotation_deg))
    cd = np.array([[-c, n], [n, c]]) * s  # east left, north up, then rotated
    if mirrored:
        cd = cd @ np.array([[-1.0, 0.0], [0.0, 1.0]])
    return {
        "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": centre[0], "CRVAL2": centre[1],
        "CRPIX1": (width + 1) / 2, "CRPIX2": (height + 1) / 2,
        "CD1_1": cd[0, 0], "CD1_2": cd[0, 1], "CD2_1": cd[1, 0], "CD2_2": cd[1, 1],
    }  # fmt: skip


def bayer_frame(
    path: Path,
    *,
    centre_offset: tuple[float, float] = (0.45, 0.4),
    rotation_deg: float = 37.0,
    mirrored: bool = False,
    width: int = 1800,
    height: int = 1800,
    scale_arcsec: float = 1.65,
    per_deg2: float = 450.0,
    only: int | None = None,
    seed: int = 7,
    header: dict[str, Any] | None = None,
    stack: bool = False,
) -> Truth:
    """A raw GBRG frame centred ``centre_offset`` degrees (RA, Dec) off M31 at the given rotation, with
    the reference Gaia sees (G < 15) in a 1.5 degree cone about M31. ``only`` keeps that many stars."""
    centre = (M31[0] + centre_offset[0] / math.cos(math.radians(M31[1])), M31[1] + centre_offset[1])
    ra, dec, mag = gaia_field(M31, 1.5, per_deg2, seed)
    wcs_doc = truth_wcs(centre, width, height, scale_arcsec, rotation_deg, mirrored)
    wcs = WCS(wcs_doc)
    x, y = wcs.all_world2pix(ra, dec, 0)
    inside = (x > -10) & (x < width + 10) & (y > -10) & (y < height + 10)
    if only is not None:
        keep = np.flatnonzero(inside)[np.argsort(mag[inside])][:only]
        inside = np.zeros_like(inside)
        inside[keep] = True
    rng = np.random.default_rng(seed + 1)
    image = np.full((height, width), 600.0)
    sigma = 1.7  # raw pixels: 2.8 arcsec seeing at 1.65 arcsec a pixel
    yy, xx = np.mgrid[-8:9, -8:9]
    for xs, ys, m in zip(x[inside], y[inside], mag[inside], strict=True):
        flux = 4.0e5 * 10 ** (-0.4 * (m - 10.0))
        ix, iy = round(xs), round(ys)
        stamp = flux / (2 * math.pi * sigma**2) * np.exp(-((xx + ix - xs) ** 2 + (yy + iy - ys) ** 2) / (2 * sigma**2))
        x0, y0 = ix - 8, iy - 8
        sx = slice(max(0, x0), min(width, x0 + 17))
        sy = slice(max(0, y0), min(height, y0 + 17))
        if sx.start < sx.stop and sy.start < sy.stop:
            image[sy, sx] += stamp[sy.start - y0 : sy.stop - y0, sx.start - x0 : sx.stop - x0]
    if stack:
        # a Siril master: three float colour planes of the same sky
        cube = np.stack([image * g + rng.normal(0.0, 3.0, image.shape) for g in (0.6, 1.0, 0.5)]) / 60.0
        fits_header = {
            "SIMPLE": True, "BITPIX": -32, "NAXIS": 3, "NAXIS1": width, "NAXIS2": height, "NAXIS3": 3,
            "OBJECT": "M31", "STACKCNT": 120, "EXPTIME": 1200.0, **(header or {}),
        }  # fmt: skip
        write(path, fits_header, cube.astype(">f4").tobytes())
    else:
        # GBRG colour cells answer differently; binning sums each cell, so the solver sees their total
        gain = np.ones((height, width))
        gain[0::2, 0::2], gain[0::2, 1::2], gain[1::2, 0::2], gain[1::2, 1::2] = 1.0, 0.55, 0.45, 1.0
        image = image * gain + rng.normal(0.0, 12.0, image.shape)
        raw = np.clip(np.round(image), 0, 65535).astype(np.int64) - 32768
        fits_header = {
            "SIMPLE": True, "BITPIX": 16, "NAXIS": 2, "NAXIS1": width, "NAXIS2": height, "BZERO": 32768,
            "OBJECT": "M31", "EXPOSURE": 10.0, "BAYERPAT": "GBRG", "DATE-OBS": "2025-09-14T22:01:03",
            **(header or {}),
        }  # fmt: skip
        write(path, fits_header, raw.astype(">i2").tobytes())
    gaia = mag < 15.0
    reference = Reference(ra[gaia], dec[gaia], mag[gaia], "sha256:synthetic-gaia")
    return Truth(wcs, wcs_doc, reference, path)
