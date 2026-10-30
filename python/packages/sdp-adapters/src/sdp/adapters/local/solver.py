"""SDP's own plate solver for a person's frames (#71).

Given a frame, a rough centre (from its OBJECT, off by up to about a degree), its pixel scale and the
Gaia stars around that centre, it finds the frame's sky solution:

1. Stars: raw Bayer frames are binned 2 x 2 (one pixel per colour cell), stacks are reduced to their
   luminance; photutils finds stars on the background-subtracted image, brightest first.
2. Triangles: the brightest stars of the frame and of Gaia form triangles described by their three
   side lengths in arcseconds (the scale is known), so a triangle matches whatever the rotation,
   mirror image or offset of the frame.
3. Votes: each matching pair of triangles proposes a rotation, scale and offset; the most voted
   proposals are each checked against every detected star, and the best is kept.
4. Fit: a TAN projection (reference point and CD matrix) is fitted to the matched stars by least
   squares, rematched and refitted with outliers clipped until it settles.
5. Verdict: accepted only with at least ``min_matches`` stars and an RMS under ``max_rms_arcsec``;
   otherwise the frame is refused with the reason, and no doubtful solution is written.

The result is deterministic for a given frame and Gaia table. The WCS is in the frame's own (unbinned)
pixels, FITS 1-based.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
from scipy.spatial import cKDTree

SOLVER_VERSION = "sdp-solve 2"
ARCSEC = 3600.0


@dataclass(frozen=True)
class SolverConfig:
    max_stars: int = 150
    #: stars of the frame that form triangles; Gaia gets proportionally more, as its cone is wider
    triangle_stars: int = 30
    min_triangle_side_arcsec: float = 120.0
    #: the tolerance on a triangle's side ratios: this, plus twice side_tolerance_arcsec over its longest side
    side_tolerance: float = 0.005
    side_tolerance_arcsec: float = 8.0
    max_scale_error: float = 0.03
    min_matches: int = 12
    max_rms_arcsec: float = 1.5
    fwhm_px: float = 3.0
    nsigma: float = 5.0
    background_box: int = 128

    def to_document(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(frozen=True)
class Stars:
    """Detected stars in the solving image's pixels (0-based), brightest first."""

    x: np.ndarray
    y: np.ndarray
    flux: np.ndarray
    width: int
    height: int
    #: raw pixels per solving pixel (2 for a binned Bayer frame)
    binning: int = 1

    def __len__(self) -> int:
        return int(self.x.size)


@dataclass(frozen=True)
class Reference:
    """Gaia stars around the rough centre: degrees and G magnitude."""

    ra: np.ndarray
    dec: np.ndarray
    mag: np.ndarray
    artifact_id: str = ""


@dataclass(frozen=True)
class Solution:
    wcs: dict[str, Any]
    matched: int
    rms_arcsec: float
    scale_arcsec: float
    rotation_deg: float
    mirrored: bool
    centre: tuple[float, float]
    detected: int

    def to_document(self) -> dict[str, Any]:
        return {
            "wcs": self.wcs,
            "matched": self.matched,
            "rms_arcsec": round(self.rms_arcsec, 4),
            "scale_arcsec": round(self.scale_arcsec, 5),
            "rotation_deg": round(self.rotation_deg, 4),
            "mirrored": self.mirrored,
            "centre": {"ra_deg": round(self.centre[0], 7), "dec_deg": round(self.centre[1], 7)},
            "detected": self.detected,
        }


class Unsolved(Exception):
    """The frame was not solved; ``reason`` says why in plain words."""

    def __init__(self, reason: str, **details: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details


# -- pixels and stars -----------------------------------------------------------------------------


def solving_image(data: np.ndarray, bayer: str | None) -> tuple[np.ndarray, int]:
    """The image stars are found on: a colour cube's mean, or a raw Bayer frame binned 2 x 2."""
    image = np.asarray(data, dtype=np.float32)
    if image.ndim == 3:
        return image.mean(axis=0), 1
    if image.ndim != 2:
        raise Unsolved(f"the frame has {image.ndim} axes; a 2-D image or a colour cube is solved")
    if bayer:
        h, w = (image.shape[0] // 2) * 2, (image.shape[1] // 2) * 2
        cell = image[:h, :w]
        return cell[0::2, 0::2] + cell[0::2, 1::2] + cell[1::2, 0::2] + cell[1::2, 1::2], 2
    return image, 1


def read_frame(path: Path | BinaryIO) -> tuple[np.ndarray, dict[str, Any]]:
    """The primary image and header, read-only (the file is never opened for writing)."""
    from astropy.io import fits

    with fits.open(path, mode="readonly", memmap=False) as hdus:
        hdu = next((h for h in hdus if h.data is not None and h.data.ndim in (2, 3)), None)
        if hdu is None:
            raise Unsolved("the file holds no image")
        return np.array(hdu.data, dtype=np.float32), dict(hdu.header)


def detect(image: np.ndarray, config: SolverConfig, binning: int = 1) -> Stars:
    """photutils on the background-subtracted image, as the image tests' detector does it."""
    from astropy.stats import SigmaClip
    from photutils.background import Background2D, MedianBackground
    from photutils.detection import DAOStarFinder

    finite = np.isfinite(image)
    box = int(min(config.background_box, *image.shape))
    background = Background2D(
        image,
        box,
        filter_size=3,
        sigma_clip=SigmaClip(sigma=3.0),
        bkg_estimator=MedianBackground(),
        mask=~finite,
        exclude_percentile=25.0,
    )
    subtracted = np.where(finite, image - background.background, 0.0)
    finder = DAOStarFinder(fwhm=config.fwhm_px, threshold=config.nsigma * float(background.background_rms_median))
    with np.errstate(divide="ignore", invalid="ignore"):
        table = finder(subtracted, mask=~finite)
    if table is None or len(table) == 0:
        empty = np.zeros(0)
        return Stars(empty, empty, empty, image.shape[1], image.shape[0], binning)
    x = np.asarray(table["x_centroid"], dtype=float)
    y = np.asarray(table["y_centroid"], dtype=float)
    flux = np.asarray(table["flux"], dtype=float)
    order = np.lexsort((y, x, -flux))[: config.max_stars]
    return Stars(x[order], y[order], flux[order], image.shape[1], image.shape[0], binning)


# -- the tangent plane ----------------------------------------------------------------------------


def project(ra: np.ndarray, dec: np.ndarray, ra0: float, dec0: float) -> tuple[np.ndarray, np.ndarray]:
    """Gnomonic (TAN) projection about (ra0, dec0): standard coordinates in degrees, xi towards east."""
    a, d = np.radians(ra - ra0), np.radians(dec)
    d0 = math.radians(dec0)
    cos_c = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(a)
    xi = np.cos(d) * np.sin(a) / cos_c
    eta = (math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(a)) / cos_c
    return np.degrees(xi), np.degrees(eta)


def deproject(xi: np.ndarray, eta: np.ndarray, ra0: float, dec0: float) -> tuple[np.ndarray, np.ndarray]:
    x, y = np.radians(xi), np.radians(eta)
    d0 = math.radians(dec0)
    denom = math.cos(d0) - y * math.sin(d0)
    ra = ra0 + np.degrees(np.arctan2(x, denom))
    dec = np.degrees(np.arctan2(math.sin(d0) + y * math.cos(d0), np.hypot(x, denom)))
    return ra % 360.0, dec


# -- triangles ------------------------------------------------------------------------------------


def triangles(points: np.ndarray, max_side: float, min_side: float) -> tuple[np.ndarray, np.ndarray]:
    """Every triangle of ``points`` whose sides lie within [min_side, max_side]: its sorted side lengths
    (longest first) and its vertices ordered by the side they face (longest, middle, shortest)."""
    n = len(points)
    if n < 3:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=int)
    distance = np.hypot(*(points[:, None, :] - points[None, :, :]).transpose(2, 0, 1))
    near = (distance <= max_side) & (distance >= min_side)
    found: list[np.ndarray] = []
    for i, j in np.argwhere(np.triu(near, 1)):
        ks = np.flatnonzero(near[i, j + 1 :] & near[j, j + 1 :]) + j + 1
        if ks.size:
            found.append(np.column_stack([np.full(ks.size, i), np.full(ks.size, j), ks]))
    if not found:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=int)
    tri = np.concatenate(found)
    i, j, k = tri.T
    # the side facing each vertex
    sides = np.column_stack([distance[j, k], distance[i, k], distance[i, j]])
    order = np.argsort(-sides, axis=1, kind="stable")
    return np.take_along_axis(sides, order, axis=1), np.take_along_axis(tri, order, axis=1)


def _shape(descriptors: np.ndarray) -> np.ndarray:
    return np.asarray(descriptors[:, 1:] / descriptors[:, :1])
    return descriptors[:, 1:] / descriptors[:, :1]


def _similarities(p: np.ndarray, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The similarity (complex factor ``a``, offset ``t``) best taking each row of points ``p`` onto
    ``q``, as complex numbers: q ~ a * p + t."""
    pc = p - p.mean(axis=1, keepdims=True)
    qc = q - q.mean(axis=1, keepdims=True)
    a = (np.conj(pc) * qc).sum(axis=1) / (np.abs(pc) ** 2).sum(axis=1)
    return a, q.mean(axis=1) - a * p.mean(axis=1)


# -- solving --------------------------------------------------------------------------------------


@dataclass
class _Candidate:
    a: complex
    t: complex
    mirrored: bool
    votes: int = 0
    matched: int = 0
    pairs: list[tuple[int, int]] = field(default_factory=list)


def _match(sky: np.ndarray, tree: cKDTree, radius: float) -> list[tuple[int, int]]:
    """Each frame star's nearest reference within ``radius``, each reference used once (nearest wins)."""
    distance, index = tree.query(sky, distance_upper_bound=radius)
    best: dict[int, tuple[float, int]] = {}
    for star, (d, ref) in enumerate(zip(distance, index, strict=True)):
        if np.isfinite(d) and (ref not in best or d < best[ref][0]):
            best[int(ref)] = (float(d), star)
    return sorted((star, ref) for ref, (_, star) in best.items())


def solve(
    stars: Stars,
    reference: Reference,
    centre: tuple[float, float],
    scale_arcsec: float,
    config: SolverConfig | None = None,
    cache: dict[Any, Any] | None = None,
) -> Solution:
    """The frame's TAN solution, or ``Unsolved`` with the reason. ``scale_arcsec`` is per raw pixel.
    ``cache`` keeps Gaia's triangles between calls: every frame of a target shares one reference."""
    config = config or SolverConfig()
    if len(stars) < config.min_matches:
        raise Unsolved(f"only {len(stars)} stars were found; at least {config.min_matches} are needed")
    if reference.ra.size < config.min_matches:
        raise Unsolved(f"Gaia has only {reference.ra.size} stars around the centre")
    scale = scale_arcsec * stars.binning / ARCSEC  # degrees per solving pixel
    cx, cy = (stars.width - 1) / 2.0, (stars.height - 1) / 2.0
    # the frame's stars in degrees about its centre, unrotated; the reference on the tangent plane
    frame = ((stars.x - cx) + 1j * (stars.y - cy)) * scale
    xi, eta = project(reference.ra, reference.dec, *centre)
    ref_plane = xi + 1j * eta
    ref_tree = cKDTree(np.column_stack([xi, eta]))

    field_deg = min(stars.width, stars.height) * scale
    max_side, min_side = 0.5 * field_deg, config.min_triangle_side_arcsec / ARCSEC
    bright = min(len(stars), config.triangle_stars)
    frame_pts = np.column_stack([frame.real, frame.imag])[:bright]
    reach = float(np.abs(ref_plane).max()) if ref_plane.size else 0.0
    ratio = max(1.0, (math.pi * reach**2) / (stars.width * stars.height * scale**2))
    gaia_count = min(reference.ra.size, max(60, int(2 * bright * ratio)))
    brightest = np.argsort(reference.mag, kind="stable")[:gaia_count]
    ref_pts = np.column_stack([xi[brightest], eta[brightest]])

    f_desc, f_vert = triangles(frame_pts, max_side, min_side)
    key = (reference.artifact_id or id(reference), centre, round(max_side, 6), gaia_count)
    if cache is not None and key in cache:
        r_desc, r_vert, r_tree = cache[key]
    else:
        r_desc, r_vert = triangles(ref_pts, max_side * (1 + config.max_scale_error), min_side)
        r_tree = cKDTree(_shape(r_desc)) if len(r_desc) else None
        if cache is not None:
            cache[key] = (r_desc, r_vert, r_tree)
    if not len(f_desc) or r_tree is None:
        raise Unsolved("too few stars spread across the frame to form triangles")
    # triangles match by shape (the ratios of their sides, to within the stars' position errors), then
    # by size, to within the scale's uncertainty
    shape_tolerance = 2 * config.side_tolerance_arcsec / ARCSEC / f_desc[:, 0] + config.side_tolerance
    hits = r_tree.query_ball_point(_shape(f_desc), shape_tolerance)
    lengths = np.fromiter((len(h) for h in hits), dtype=int, count=len(hits))
    if not lengths.sum():
        raise Unsolved("no triangle of the frame matches one of Gaia's; is the centre or the scale wrong?")
    f_idx = np.repeat(np.arange(len(hits)), lengths)
    r_idx = np.fromiter((r for h in hits for r in h), dtype=int, count=int(lengths.sum()))
    sized = np.abs(r_desc[r_idx, 0] / f_desc[f_idx, 0] - 1.0) <= config.max_scale_error
    f_idx, r_idx = f_idx[sized], r_idx[sized]
    if not f_idx.size:
        raise Unsolved("triangles match in shape but not in size; is the pixel scale right?")
    p = frame_pts[f_vert[f_idx]]
    q = ref_pts[r_vert[r_idx]]
    p_complex, q_complex = p[..., 0] + 1j * p[..., 1], q[..., 0] + 1j * q[..., 1]

    candidates: list[_Candidate] = []
    for mirrored in (False, True):
        source = np.conj(p_complex) if mirrored else p_complex
        a, t = _similarities(source, q_complex)
        scale_ok = np.abs(np.abs(a) - 1.0) <= config.max_scale_error
        residual = np.abs(a[:, None] * source + t[:, None] - q_complex).max(axis=1)
        good = scale_ok & (residual <= 2 * config.side_tolerance_arcsec / ARCSEC + config.side_tolerance * max_side)
        if not good.any():
            continue
        # vote in (rotation, offset) cells: a rotation degree and a tenth of the frame's width
        angle = np.degrees(np.angle(a[good]))
        cell = field_deg / 10
        keys = np.column_stack(
            [np.round(angle / 1.0), np.round(t[good].real / cell), np.round(t[good].imag / cell)]
        ).astype(int)
        _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
        for c in np.argsort(-counts, kind="stable")[:8]:
            members = np.flatnonzero(inverse.ravel() == c)
            candidates.append(
                _Candidate(
                    complex(np.median(a[good][members].real) + 1j * np.median(a[good][members].imag)),
                    complex(np.median(t[good][members].real) + 1j * np.median(t[good][members].imag)),
                    mirrored,
                    votes=int(counts[c]),
                )
            )
    if not candidates:
        raise Unsolved("no consistent rotation and offset; the frame may be blurred or the scale wrong")

    radius = max(3 * scale, 6.0 / ARCSEC)
    for candidate in candidates:
        source = np.conj(frame) if candidate.mirrored else frame
        sky = candidate.a * source + candidate.t
        candidate.pairs = _match(np.column_stack([sky.real, sky.imag]), ref_tree, radius)
        candidate.matched = len(candidate.pairs)
    best = max(candidates, key=lambda c: (c.matched, c.votes))
    if best.matched < config.min_matches:
        raise Unsolved(
            f"only {best.matched} stars matched Gaia; at least {config.min_matches} are needed",
            matched=best.matched,
        )
    # the starting linear map from (dx, dy) to the tangent plane at ``centre``
    m = np.array([[best.a.real, -best.a.imag], [best.a.imag, best.a.real]]) * scale
    if best.mirrored:
        m = m @ np.array([[1.0, 0.0], [0.0, -1.0]])
    crval = deproject(np.array([best.t.real]), np.array([best.t.imag]), *centre)
    return _fit(stars, reference, m, (float(crval[0][0]), float(crval[1][0])), radius, config)


def refine(
    stars: Stars,
    reference: Reference,
    guess: Mapping[str, Any],
    config: SolverConfig | None = None,
    radius_arcsec: float = 20.0,
) -> Solution:
    """The frame's TAN solution started from another frame's (``guess``, a WCS in raw FITS pixels), with
    no triangle search: Gaia is matched within ``radius_arcsec`` of where the guess puts it, then fitted
    as ``solve`` fits. The same limits apply, so a guess that is too far off is refused, not forced."""
    config = config or SolverConfig()
    if len(stars) < config.min_matches:
        raise Unsolved(f"only {len(stars)} stars were found; at least {config.min_matches} are needed")
    b = stars.binning
    # binning drops an odd raw row or column, so a frame of the same size may be one pixel larger
    if any(
        abs(int(guess.get(k, n)) - n) > b - 1 for k, n in (("NAXIS1", stars.width * b), ("NAXIS2", stars.height * b))
    ):
        raise Unsolved("the neighbour's solution is for a frame of another size")
    cx, cy = (stars.width - 1) / 2.0, (stars.height - 1) / 2.0
    ra0, dec0 = pixel_to_sky(guess, np.array([b * cx + (b - 1) / 2 + 1]), np.array([b * cy + (b - 1) / 2 + 1]))
    cd = np.array([[guess["CD1_1"], guess["CD1_2"]], [guess["CD2_1"], guess["CD2_2"]]], dtype=float)
    return _fit(stars, reference, cd * b, (float(ra0[0]), float(dec0[0])), radius_arcsec / ARCSEC, config)


def _fit(
    stars: Stars,
    reference: Reference,
    m: np.ndarray,
    start: tuple[float, float],
    radius: float,
    config: SolverConfig,
) -> Solution:
    """Least-squares TAN fit about the frame's centre pixel, rematched and clipped until it settles.
    ``m`` maps solving pixels about the centre to the tangent plane at ``start``, in degrees."""
    cx, cy = (stars.width - 1) / 2.0, (stars.height - 1) / 2.0
    dx, dy = stars.x - cx, stars.y - cy
    ra0, dec0 = start
    pairs: list[tuple[int, int]] = []
    rms = math.inf
    for _ in range(8):
        xi, eta = project(reference.ra, reference.dec, ra0, dec0)
        tree = cKDTree(np.column_stack([xi, eta]))
        sky = (m @ np.vstack([dx, dy])).T
        pairs = _match(sky, tree, radius)
        if len(pairs) < config.min_matches:
            break
        star_i, ref_i = np.array(pairs).T
        design = np.column_stack([dx[star_i], dy[star_i], np.ones(len(pairs))])
        target = np.column_stack([xi[ref_i], eta[ref_i]])
        coeffs, *_ = np.linalg.lstsq(design, target, rcond=None)
        residual = np.hypot(*(design @ coeffs - target).T)
        sigma = 1.4826 * float(np.median(residual)) if len(residual) else 0.0
        keep = residual <= max(3 * sigma, 0.2 / ARCSEC)
        if keep.sum() >= config.min_matches and not keep.all():
            coeffs, *_ = np.linalg.lstsq(design[keep], target[keep], rcond=None)
            residual = np.hypot(*(design[keep] @ coeffs - target[keep]).T)
            pairs = [p for p, k in zip(pairs, keep, strict=True) if k]
        m = coeffs[:2].T
        new = deproject(np.array([coeffs[2, 0]]), np.array([coeffs[2, 1]]), ra0, dec0)
        moved = abs(coeffs[2, 0]) + abs(coeffs[2, 1])
        ra0, dec0 = float(new[0][0]), float(new[1][0])
        rms = float(np.sqrt(np.mean(residual**2))) * ARCSEC
        radius = max(min(radius, 4 * rms / ARCSEC), 2.0 / ARCSEC)
        if moved < 1e-9:
            break
    if len(pairs) < config.min_matches:
        raise Unsolved(f"only {len(pairs)} stars matched after fitting; at least {config.min_matches} are needed")
    if rms >= config.max_rms_arcsec:
        raise Unsolved(
            f"the fit's RMS is {rms:.2f} arcsec, over the {config.max_rms_arcsec} arcsec limit",
            matched=len(pairs),
            rms_arcsec=rms,
        )
    b = stars.binning
    cd = m / b  # per raw pixel
    det = float(np.linalg.det(cd))
    # the position angle of the frame's +y axis, east of north
    rotation = round(math.degrees(math.atan2(cd[0, 1], cd[1, 1])), 6) % 360.0
    raw_w, raw_h = stars.width * b, stars.height * b
    # a solving pixel's centre cx is raw pixel b*cx + (b-1)/2, 0-based; FITS counts from 1
    wcs = {
        "CTYPE1": "RA---TAN",
        "CTYPE2": "DEC--TAN",
        "CUNIT1": "deg",
        "CUNIT2": "deg",
        "RADESYS": "ICRS",
        "EQUINOX": 2000.0,
        "CRVAL1": ra0,
        "CRVAL2": dec0,
        "CRPIX1": b * cx + (b - 1) / 2 + 1,
        "CRPIX2": b * cy + (b - 1) / 2 + 1,
        "CD1_1": float(cd[0, 0]),
        "CD1_2": float(cd[0, 1]),
        "CD2_1": float(cd[1, 0]),
        "CD2_2": float(cd[1, 1]),
        "NAXIS1": raw_w,
        "NAXIS2": raw_h,
    }
    return Solution(
        wcs=wcs,
        matched=len(pairs),
        rms_arcsec=rms,
        scale_arcsec=math.sqrt(abs(det)) * ARCSEC,
        rotation_deg=rotation,
        mirrored=det > 0,
        centre=(ra0, dec0),
        detected=len(stars),
    )


def pixel_to_sky(wcs: Mapping[str, Any], x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A solution's sky position of 1-based FITS pixels, without astropy."""
    dx, dy = x - wcs["CRPIX1"], y - wcs["CRPIX2"]
    xi = wcs["CD1_1"] * dx + wcs["CD1_2"] * dy
    eta = wcs["CD2_1"] * dx + wcs["CD2_2"] * dy
    return deproject(xi, eta, wcs["CRVAL1"], wcs["CRVAL2"])


def scale_from_header(header: Mapping[str, Any]) -> float | None:
    """Arcseconds per pixel from FOCALLEN (mm) and XPIXSZ (micrometres), when both are given."""
    focal, pixel = header.get("FOCALLEN"), header.get("XPIXSZ") or header.get("PIXSIZE1")
    if isinstance(focal, int | float) and isinstance(pixel, int | float) and focal > 0 and pixel > 0:
        return 206.264806 * float(pixel) / float(focal)
    return None
