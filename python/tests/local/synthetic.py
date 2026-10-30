"""A synthetic Vespera-like frame: a raw Bayer image of planted Gaia stars at a known WCS, so the plate
solver can be checked against the truth. Invented values only; nothing is read from an archive."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
from astropy.wcs import WCS

from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.solver import Reference, deproject
from sdp.adapters.local.solving import SOLVED
from sdp.adapters.local.sources import LocalSource, LocalSources

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
    distortion: dict[str, float] | None = None,
) -> Truth:
    """A raw GBRG frame centred ``centre_offset`` degrees (RA, Dec) off M31 at the given rotation, with
    the reference Gaia sees (G < 15) in a 1.5 degree cone about M31. ``only`` keeps that many stars."""
    centre = (M31[0] + centre_offset[0] / math.cos(math.radians(M31[1])), M31[1] + centre_offset[1])
    ra, dec, mag = gaia_field(M31, 1.5, per_deg2, seed)
    wcs_doc = truth_wcs(centre, width, height, scale_arcsec, rotation_deg, mirrored)
    if distortion:
        # a lens that bends the field: SIP terms, so the frame is not a pure TAN projection
        order = max(int(k.split("_")[1]) + int(k.split("_")[2]) for k in distortion)
        wcs_doc |= {"CTYPE1": "RA---TAN-SIP", "CTYPE2": "DEC--TAN-SIP", "A_ORDER": order, "B_ORDER": order}
        wcs_doc |= distortion
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


def write_solution(data: Path, source: LocalSource, relative: str, truth: Truth, width: int, height: int) -> Path:
    """The truth's WCS written where ``sdp local solve`` writes a solution, as if the frame had solved."""
    s = abs(truth.header["CD1_1"] * truth.header["CD2_2"] - truth.header["CD1_2"] * truth.header["CD2_1"]) ** 0.5
    wcs = {**truth.header, "CUNIT1": "deg", "CUNIT2": "deg", "RADESYS": "ICRS", "EQUINOX": 2000.0}
    centre = truth.wcs.all_pix2world([(width + 1) / 2], [(height + 1) / 2], 1)
    stat = (source.path / relative).stat()
    doc = {
        "frame": relative,
        "source": source.source_id,
        "file": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
        "wcs": {**wcs, "NAXIS1": width, "NAXIS2": height},
        "scale_arcsec": s * 3600.0,
        "centre": {"ra_deg": float(centre[0][0]), "dec_deg": float(centre[1][0])},
        "matched": 40,
        "rms_arcsec": 0.2,
    }
    path = source.output_dir(data, "solutions") / (relative + SOLVED)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), "utf-8")
    return path


@dataclass(frozen=True)
class LocalWorld:
    """A local source of two solved frames of the same sky (a raw Bayer sub and a stacked master), a
    dark that is never offered, and the bright, isolated Gaia stars of a field inside them."""

    source: LocalSource
    data: Path
    light: Truth
    stack: Truth
    catalogue: pa.Table
    centre: tuple[float, float]
    vanished: str


LOCAL_DATE = "2025-09-14T22:01:03"
VANISHED = "gaia:DR3:vanished"


def local_world(tmp: Path, *, field_radius_deg: float = 0.12, size: int = 1800, seed: int = 7) -> LocalWorld:
    root, data = tmp / "astrophotography", tmp / "SDP"
    (root / "M31" / "lights").mkdir(parents=True)
    (root / "M31" / "darks").mkdir()
    common: dict[str, Any] = {"centre_offset": (0.45, 0.4), "rotation_deg": 37.0, "width": size, "height": size}
    common |= {"per_deg2": 3000.0, "seed": seed}
    light = bayer_frame(root / "M31" / "lights" / "sub-001.fits", **common)
    stack = bayer_frame(root / "M31" / "M31_stacked.fits", stack=True, header={"DATE-OBS": LOCAL_DATE}, **common)
    write(root / "M31" / "darks" / "dark-001.fits", {
        "SIMPLE": True, "BITPIX": 16, "NAXIS": 2, "NAXIS1": 8, "NAXIS2": 8, "IMAGETYP": "Dark",
    }, bytes(128))  # fmt: skip
    source = LocalSources(data).add("astro", root, 1.65)
    FrameIndex(source, data).update()
    for truth, relative in ((light, "M31/lights/sub-001.fits"), (stack, "M31/M31_stacked.fits")):
        write_solution(data, source, relative, truth, size, size)
    # the field sits at the frames' centre; its catalogue is the bright stars with no near neighbour
    ra_c, dec_c = (float(v[0]) for v in light.wcs.all_pix2world([(size + 1) / 2], [(size + 1) / 2], 1))
    ra, dec, mag = gaia_field(M31, 1.5, 3000.0, seed)
    cosd = math.cos(math.radians(dec_c))
    near = np.hypot((ra - ra_c) * cosd, dec - dec_c) < field_radius_deg
    keep = []
    for i in np.flatnonzero(near & (mag < 13.0) & (mag > 9.0)):
        gap = np.hypot((ra - ra[i]) * cosd, dec - dec[i]) * 3600
        if not np.any((gap > 0) & (gap < 25.0) & (mag < mag[i] + 3.0)):
            keep.append(i)
    # one Gaia star where the frames show nothing: the run should flag it and only it
    angle = np.linspace(0, 2 * math.pi, 64, endpoint=False)
    spots = [(ra_c + 0.06 * math.cos(a) / cosd, dec_c + 0.06 * math.sin(a)) for a in angle]
    lonely = max(spots, key=lambda p: float(np.min(np.hypot((ra - p[0]) * cosd, dec - p[1]))))
    rows = [(f"gaia:DR3:{i}", float(ra[i]), float(dec[i]), float(mag[i])) for i in keep]
    rows.append((VANISHED, lonely[0], lonely[1], 11.0))
    n = len(rows)
    catalogue = pa.table({
        "source_key": [r[0] for r in rows], "ra_deg": [r[1] for r in rows], "dec_deg": [r[2] for r in rows],
        "epoch_jyear": [2016.0] * n, "parallax_mas": [1.0] * n, "parallax_mas_err": [0.05] * n,
        "pm_ra_cosdec_masyr": [0.0] * n, "pm_dec_masyr": [0.0] * n, "g_mag": [r[3] for r in rows],
        "bp_rp": [0.8] * n,
    })  # fmt: skip
    return LocalWorld(source, data, light, stack, catalogue, (ra_c, dec_c), VANISHED)
