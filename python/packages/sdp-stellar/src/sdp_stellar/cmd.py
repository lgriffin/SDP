"""CmdCoherence: how tight a candidate's colour-magnitude sequence is against field stars of the same count.

Absolute magnitude M_G = G + 5 log10(parallax / 100 mas). A quadratic in BP-RP is fitted to the
members and to ``draws`` random field samples of the same size; coherence is the median field
scatter over the member scatter (robust, 1.4826 MAD), so a real cluster sequence scores well above 1.
"""

from __future__ import annotations

import numpy as np

MIN_WITH_COLOUR = 8


def absolute_g(g: np.ndarray, parallax_mas: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(g + 5 * np.log10(parallax_mas / 100.0), dtype=np.float64)


def _scatter(colour: np.ndarray, mag: np.ndarray) -> float:
    coeffs = np.polyfit(colour, mag, 2)
    resid = mag - np.polyval(coeffs, colour)
    return float(1.4826 * np.median(np.abs(resid - np.median(resid))))


def coherence(members: np.ndarray, colour: np.ndarray, mag: np.ndarray, seed: int, draws: int = 50) -> float | None:
    """None when fewer than MIN_WITH_COLOUR members have a colour: the check is inconclusive, not failed."""
    ok = np.isfinite(colour) & np.isfinite(mag)
    m = members[ok[members]]
    if m.size < MIN_WITH_COLOUR:
        return None
    field = np.setdiff1d(np.nonzero(ok)[0], m)
    if field.size < m.size:
        return None
    member_scatter = max(_scatter(colour[m], mag[m]), 1e-6)
    rng = np.random.default_rng(seed)
    field_scatter = [
        _scatter(colour[s], mag[s]) for s in (rng.choice(field, m.size, replace=False) for _ in range(draws))
    ]
    return float(np.median(field_scatter) / member_scatter)
