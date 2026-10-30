"""Lens distortion for a solved local frame: a SIP polynomial on top of its TAN solution (#71).

A 1.6 degree field can bend by more than an arcsecond towards its corners, which a TAN projection cannot
follow, and the TAN fit's clipping drops exactly the corner stars that show it. So the detected stars are
matched to Gaia again within a wider radius, and the TAN projection and polynomials of order 2 and 3 (SIP,
the FITS convention of Shupe et al. 2005) are fitted together. Every fourth star is held out of the fit:
a polynomial is kept only if it lowers the RMS on those stars by a tenth or more against a TAN fit to the
same stars, so noise is never fitted as distortion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from sdp.adapters.local.solver import (
    ARCSEC,
    Reference,
    Solution,
    SolverConfig,
    Stars,
    deproject,
    project,
    sip_offsets,
)

#: a polynomial is kept only below this share of the TAN fit's held-out RMS
KEEP_BELOW = 0.9
ROUNDS = 6


def _powers(order: int, lowest: int) -> list[tuple[int, int]]:
    return [(p, s - p) for s in range(lowest, order + 1) for p in range(s, -1, -1)]


def _design(u: np.ndarray, v: np.ndarray, powers: list[tuple[int, int]]) -> np.ndarray:
    return np.column_stack([u**p * v**q for p, q in powers]) if powers else np.zeros((u.size, 0))


def _lstsq(design: np.ndarray, target: np.ndarray) -> np.ndarray:
    # scale the columns so the fit is well conditioned over a few thousand pixels
    norm = np.abs(design).max(axis=0)
    norm[norm == 0] = 1.0
    coeffs, *_ = np.linalg.lstsq(design / norm, target, rcond=None)
    return np.asarray(coeffs / norm[:, None])


@dataclass(frozen=True)
class _Model:
    crval: tuple[float, float]
    cd: np.ndarray
    powers: list[tuple[int, int]]
    coeffs: np.ndarray  # one row per power: A, B

    def sky(self, u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        bend = _design(u, v, self.powers) @ self.coeffs if self.powers else np.zeros((u.size, 2))
        xi, eta = self.cd @ np.vstack([u + bend[:, 0], v + bend[:, 1]])
        return deproject(xi, eta, *self.crval)


def _fit(u: np.ndarray, v: np.ndarray, ra: np.ndarray, dec: np.ndarray, start: _Model, order: int | None) -> _Model:
    """TAN (and SIP of ``order``) by turns: the linear part with the polynomial fixed, then the polynomial
    with the linear part fixed."""
    model = replace(
        start, powers=_powers(order, 2) if order else [], coeffs=np.zeros((len(_powers(order, 2)) if order else 0, 2))
    )
    for _ in range(ROUNDS):
        bend = _design(u, v, model.powers) @ model.coeffs if model.powers else np.zeros((u.size, 2))
        xi, eta = project(ra, dec, *model.crval)
        linear = _lstsq(np.column_stack([u + bend[:, 0], v + bend[:, 1], np.ones(u.size)]), np.column_stack([xi, eta]))
        cd = linear[:2].T
        crval = deproject(np.array([linear[2, 0]]), np.array([linear[2, 1]]), *model.crval)
        model = replace(model, cd=cd, crval=(float(crval[0][0]), float(crval[1][0])))
        if not model.powers:
            continue
        xi, eta = project(ra, dec, *model.crval)
        ideal = np.linalg.solve(model.cd, np.vstack([xi, eta])).T
        model = replace(model, coeffs=_lstsq(_design(u, v, model.powers), ideal - np.column_stack([u, v])))
    return model


def _rms(model: _Model, u: np.ndarray, v: np.ndarray, ra: np.ndarray, dec: np.ndarray) -> float:
    sra, sdec = model.sky(u, v)
    d = np.hypot((sra - ra) * np.cos(np.radians(dec)), sdec - dec) * ARCSEC
    return float(np.sqrt(np.mean(d**2)))


def _rematch(
    wcs: dict[str, Any], stars: Stars, reference: Reference, radius_px: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Detected stars (raw pixels about CRPIX) and the Gaia stars the WCS puts within ``radius_px``."""
    b = stars.binning
    u = b * stars.x + (b - 1) / 2 + 1 - wcs["CRPIX1"]
    v = b * stars.y + (b - 1) / 2 + 1 - wcs["CRPIX2"]
    cd = np.array([[wcs["CD1_1"], wcs["CD1_2"]], [wcs["CD2_1"], wcs["CD2_2"]]])
    xi, eta = project(reference.ra, reference.dec, wcs["CRVAL1"], wcs["CRVAL2"])
    ideal = np.linalg.solve(cd, np.vstack([xi, eta])).T
    pu, pv = ideal[:, 0], ideal[:, 1]
    if "AP_ORDER" in wcs:
        f, g = sip_offsets(wcs, pu, pv, "AP")
        pu, pv = pu + f, pv + g
    distance, index = cKDTree(np.column_stack([pu, pv])).query(np.column_stack([u, v]), distance_upper_bound=radius_px)
    best: dict[int, tuple[float, int]] = {}
    for star, (d, ref) in enumerate(zip(distance, index, strict=True)):
        if np.isfinite(d) and (ref not in best or d < best[ref][0]):
            best[int(ref)] = (float(d), star)
    pairs = sorted((star, ref) for ref, (_, star) in best.items())
    s_i = np.array([p[0] for p in pairs], dtype=int)
    r_i = np.array([p[1] for p in pairs], dtype=int)
    return u[s_i], v[s_i], reference.ra[r_i], reference.dec[r_i]


def fit_distortion(
    solution: Solution, stars: Stars, reference: Reference, config: SolverConfig
) -> tuple[Solution, dict[str, Any]]:
    """The solution with a SIP polynomial when one earns its place, and what was tried."""
    wcs = solution.wcs
    cd = np.array([[wcs["CD1_1"], wcs["CD1_2"]], [wcs["CD2_1"], wcs["CD2_2"]]])
    arcsec = math.sqrt(abs(np.linalg.det(cd))) * ARCSEC
    # wide enough for a corner star a few arcseconds off, narrow enough to stay unambiguous
    radius_px = max(4.0, 6 * solution.rms_arcsec) / arcsec
    u, v, ra, dec = _rematch(wcs, stars, reference, radius_px)
    if u.size < config.min_sip_matches:
        return solution, {"kept": False, "reason": f"fewer than {config.min_sip_matches} matched stars"}
    start = _Model((float(wcs["CRVAL1"]), float(wcs["CRVAL2"])), cd, [], np.zeros((0, 2)))
    held = np.arange(u.size) % 4 == 0
    train = ~held
    tan = _fit(u[train], v[train], ra[train], dec[train], start, None)
    tan_held = _rms(tan, u[held], v[held], ra[held], dec[held])
    tried: dict[str, float] = {}
    best: int | None = None
    for order in range(2, config.sip_order + 1):
        if train.sum() < 3 * (len(_powers(order, 2)) + 3):
            break
        model = _fit(u[train], v[train], ra[train], dec[train], start, order)
        tried[str(order)] = round(_rms(model, u[held], v[held], ra[held], dec[held]), 4)
        if tried[str(order)] < KEEP_BELOW * tan_held and (best is None or tried[str(order)] < tried[str(best)]):
            best = order
    info: dict[str, Any] = {
        "stars": int(u.size),
        "tan_held_out_rms_arcsec": round(tan_held, 4),
        "sip_held_out_rms_arcsec": tried,
    }
    if best is None:
        return solution, {**info, "kept": False, "reason": "no polynomial beat the TAN fit on held-out stars"}

    model = _fit(u, v, ra, dec, start, best)
    # the inverse (sky to pixel): from where the polynomial puts each star back to where it was detected
    forward = _design(u, v, model.powers) @ model.coeffs
    big_u, big_v = u + forward[:, 0], v + forward[:, 1]
    inverse_powers = _powers(best, 1)
    inverse = _lstsq(_design(big_u, big_v, inverse_powers), -forward)
    sip: dict[str, Any] = {
        "CTYPE1": "RA---TAN-SIP",
        "CTYPE2": "DEC--TAN-SIP",
        "CRVAL1": model.crval[0],
        "CRVAL2": model.crval[1],
        "CD1_1": float(model.cd[0, 0]),
        "CD1_2": float(model.cd[0, 1]),
        "CD2_1": float(model.cd[1, 0]),
        "CD2_2": float(model.cd[1, 1]),
        "A_ORDER": best,
        "B_ORDER": best,
        "AP_ORDER": best,
        "BP_ORDER": best,
    }
    for (p, q), (a, b) in zip(model.powers, model.coeffs, strict=True):
        sip[f"A_{p}_{q}"], sip[f"B_{p}_{q}"] = float(a), float(b)
    for (p, q), (a, b) in zip(inverse_powers, inverse, strict=True):
        sip[f"AP_{p}_{q}"], sip[f"BP_{p}_{q}"] = float(a), float(b)
    final = _rms(model, u, v, ra, dec)
    tan_all = _rms(_fit(u, v, ra, dec, start, None), u, v, ra, dec)
    kept = replace(solution, wcs={**wcs, **sip}, rms_arcsec=final)
    return kept, {
        **info,
        "kept": True,
        "order": best,
        "tan_rms_arcsec": round(tan_all, 4),
        "sip_rms_arcsec": round(final, 4),
    }
