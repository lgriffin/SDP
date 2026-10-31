"""CmdCoherence: how tight a candidate's colour-magnitude sequence is against field stars of the same count.

Two versioned policies (STL-010), chosen by the settings' ``cmd_policy``:

``member-parallax`` (the UC-01 predeclaration's): absolute magnitude M_G = G + 5 log10(parallax / 100
mas) from each star's own parallax. A quadratic in BP-RP is fitted to the members and to ``draws``
random field samples of the same size; coherence is the median field scatter over the member scatter
(robust, 1.4826 MAD), so a real cluster sequence scores well above 1.

``cluster-distance`` (the sweep's): every member is placed at the cluster's distance, the
inverse-variance mean of the members' parallaxes, so a member's own parallax error (about 0.43 mag
at parallax/error 5) no longer blurs the sequence. The field draws come only from non-members whose
parallax lies inside the members' central 90% range (or, when those are fewer than the members, the
non-members nearest the cluster's parallax) and are placed at that same distance, so both
sides are treated alike: what is left is the spread a single-age, single-distance population does
not have. The quadratic is sigma clipped, so a few binaries or interlopers do not set the fit.
"""

from __future__ import annotations

import numpy as np

MIN_WITH_COLOUR = 8
POLICIES = ("member-parallax", "cluster-distance")
#: sigma clip of the cluster-distance fit, and its passes
CLIP, CLIP_PASSES = 3.0, 3
#: the fewest field stars at the cluster's distance the cluster-distance policy compares against
MIN_FIELD_AT_DISTANCE = 2 * MIN_WITH_COLOUR


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


def _clipped_scatter(colour: np.ndarray, mag: np.ndarray) -> float:
    keep = np.ones(colour.size, dtype=bool)
    for _ in range(CLIP_PASSES):
        coeffs = np.polyfit(colour[keep], mag[keep], 2)
        resid = mag - np.polyval(coeffs, colour)
        sigma = 1.4826 * float(np.median(np.abs(resid[keep] - np.median(resid[keep]))))
        new = np.abs(resid - np.median(resid[keep])) <= CLIP * max(sigma, 1e-6)
        if new.sum() < MIN_WITH_COLOUR or np.array_equal(new, keep):
            break
        keep = new
    return _scatter(colour[keep], mag[keep])


def cluster_parallax(members: np.ndarray, parallax: np.ndarray, parallax_err: np.ndarray) -> float | None:
    """The inverse-variance mean parallax of the members with a usable one, or None."""
    p, e = parallax[members], parallax_err[members]
    ok = np.isfinite(p) & np.isfinite(e) & (e > 0)
    if not ok.any():
        return None
    w = 1.0 / e[ok] ** 2
    value = float(np.sum(w * p[ok]) / np.sum(w))
    return value if value > 0 else None


def field_at_distance(members: np.ndarray, usable: np.ndarray, parallax: np.ndarray, cluster_plx: float) -> np.ndarray:
    """Non-members with a colour whose parallax lies inside the members' central 90% range; when that
    holds fewer stars than the cluster has members, the non-members nearest the cluster's parallax up
    to that count, so a cluster that dominates its own distance is still compared with its nearest
    field. Row indices, sorted."""
    lo, hi = np.quantile(parallax[members], [0.05, 0.95])
    others = np.setdiff1d(np.nonzero(usable)[0], members)
    inside = others[(parallax[others] >= lo) & (parallax[others] <= hi)]
    if inside.size >= members.size:
        return np.asarray(inside, dtype=np.int64)
    nearest = others[np.argsort(np.abs(parallax[others] - cluster_plx), kind="stable")[: members.size]]
    return np.sort(nearest)


def coherence_at_distance(
    members: np.ndarray,
    colour: np.ndarray,
    g: np.ndarray,
    parallax: np.ndarray,
    parallax_err: np.ndarray,
    seed: int,
    draws: int = 50,
) -> float | None:
    """The ``cluster-distance`` coherence; None when it cannot be assessed (too few members with a
    colour, or too few field stars at the cluster's distance), which is inconclusive, not failed."""
    ok = np.isfinite(colour) & np.isfinite(g) & np.isfinite(parallax)
    m = members[ok[members]]
    if m.size < MIN_WITH_COLOUR:
        return None
    plx = cluster_parallax(m, parallax, parallax_err)
    if plx is None:
        return None
    mag = absolute_g(g, np.full(g.shape, plx))
    field = field_at_distance(m, ok, parallax, plx)
    if field.size < MIN_FIELD_AT_DISTANCE:
        return None
    # a scatter is a width, not a sum, so a smaller draw estimates the same width with more noise
    size = min(m.size, field.size)
    member_scatter = max(_clipped_scatter(colour[m], mag[m]), 1e-6)
    rng = np.random.default_rng(seed)
    field_scatter = [
        _clipped_scatter(colour[s], mag[s]) for s in (rng.choice(field, size, replace=False) for _ in range(draws))
    ]
    return float(np.median(field_scatter) / member_scatter)
