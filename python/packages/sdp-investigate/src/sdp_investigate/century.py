"""Century trends: has a star brightened or faded over the century the Harvard plates cover?

A star's own trend on DASCH plates is not evidence on its own. Measured against its quoted errors,
about a third of the constant stars near M67 "trend" at the 1% level (2026-11-04, 520 stars), because
plates share zero-point, colour and blending errors that do not average away. So the test judges each
star against the field's own stars, never against white noise:

1. **Plate ensemble.** Every exposure (light-curve epochs within ``same_exposure_days`` of each other;
   DASCH stamps each star's own time on a plate, a few seconds apart) is corrected by the median
   residual of the ``ensemble_k`` reference stars nearest the star in position, brightness and colour.
2. **Red-noise errors.** The trend is a weighted straight line in magnitudes per century, iteratively
   clipped. Its error is the larger of the line's own (scaled by the reduced chi-squared) and the one
   the scatter of ``bin_years`` bin means gives, so slow wanders between plate series count.
3. **Peers.** The slope is compared with what the field's reference stars of the same colour and
   brightness show (a clipped linear fit), with a systematic spread ``sigma_sys`` solved so their
   normalised slopes have a median absolute value of 0.6745, as a unit normal does.

Reference stars are those with enough plates and no Gaia neighbour within ``blend_radius_arcsec``
adding more than ``max_blend`` of the star's flux: the coarse patrol plates of the 1970s and 1980s
merge neighbours up to about a minute of arc away, and every first-pass candidate near M67 was a
neighbour's light. A blended star is still scored, against the unblended peers, and says so.

The null holds out half the reference stars, calibrates on the other half and scores the held-out
half, so its false-positive rate is measured on the field's own constant-looking stars.

A profile can give a modern magnitude in the plates' band (``anchor_column``, such as Gaia's synthetic
Johnson B, measured about ``anchor_epoch``): the trend line's magnitude at that epoch minus the modern
one is reported, and in units of its spread over the reference stars, so a trend that Gaia contradicts
(a jump on the post-gap plates, which tilts the line) can be dropped at the refine step.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import optimize, stats
from scipy.spatial import cKDTree

from sdp.kernel.domain.investigation import EvidenceBundle, Subject
from sdp.kernel.science.investigation import product_bytes
from sdp.kernel.science.plan import TaskContext
from sdp_investigate.base import Features, FieldTest, Scored, catalogue_row, finite
from sdp_investigate.formats import LIGHTCURVE, parse_lightcurve

MJD_J2000 = 51544.5
DAYS_PER_YEAR = 365.25
#: The year slopes are referred to; it only moves the intercept.
PIVOT_YEAR = 1950.0
#: The median |x| of a unit normal, which the reference stars' normalised slopes are scaled to.
NORMAL_MEDIAN_ABS = float(stats.norm.ppf(0.75))

DEFAULTS: Mapping[str, Any] = {
    "min_points": 100,
    "min_each_side": 20,
    "gap_start": 1953.0,
    "gap_end": 1969.0,
    "same_exposure_days": 0.0005,
    "ensemble_k": 15,
    "ensemble_arcmin": 10.0,
    "ensemble_mag": 0.5,
    "ensemble_colour": 0.3,
    "bin_years": 5.0,
    "clip": 4.0,
    "blend_radius_arcsec": 60.0,
    "max_blend": 0.1,
    "min_reference": 20,
    "anchor_column": None,
    "anchor_epoch": 2016.0,
}


def setting(thresholds: Mapping[str, Any], name: str) -> Any:
    return thresholds.get(name, DEFAULTS[name])


def jyear(mjd: np.ndarray) -> np.ndarray:
    return 2000.0 + (np.asarray(mjd, dtype=float) - MJD_J2000) / DAYS_PER_YEAR


def exposure_groups(mjd: np.ndarray, tolerance_days: float) -> np.ndarray:
    """One integer per epoch: epochs closer than the tolerance to the previous one (in time order)
    share a group, so one plate exposure seen through many stars' light curves is one group."""
    mjd = np.asarray(mjd, dtype=float)
    order = np.argsort(mjd, kind="stable")
    breaks = np.r_[True, np.diff(mjd[order]) > tolerance_days]
    groups = np.empty(len(mjd), dtype=int)
    groups[order] = np.cumsum(breaks) - 1
    return groups


def curve_owners(claims: Sequence[tuple[str, float, str]]) -> dict[str, str]:
    """Which target each light curve belongs to, from (curve, G magnitude, target) claims: a plate
    light curve two targets both matched goes to the brighter (the archive's catalogue star most
    likely is), so the fainter is left out rather than fitted twice."""
    best: dict[str, tuple[float, str]] = {}
    for curve, g, target in claims:
        if curve not in best or (g, target) < best[curve]:
            best[curve] = (g, target)
    return {curve: target for curve, (_, target) in best.items()}


def group_medians(index: np.ndarray, values: np.ndarray, groups: int) -> np.ndarray:
    """The median of ``values`` in each of ``groups`` groups numbered by ``index`` (each group non-empty)."""
    order = np.lexsort((values, index))
    counts = np.bincount(index, minlength=groups)
    start = np.r_[0, np.cumsum(counts)[:-1]]
    ordered = values[order]
    low, high = start + (counts - 1) // 2, start + counts // 2
    return np.asarray((ordered[low] + ordered[high]) / 2.0)


def blend_fractions(ra: np.ndarray, dec: np.ndarray, g_mag: np.ndarray, radius_arcsec: float) -> np.ndarray:
    """For each star, the summed G-band flux of the other listed stars within the radius, as a fraction
    of its own."""
    xyz = _unit(ra, dec)
    tree = cKDTree(xyz)
    chord = 2.0 * math.sin(math.radians(radius_arcsec / 3600.0) / 2.0)
    flux = 10.0 ** (-0.4 * np.asarray(g_mag, dtype=float))
    out = np.zeros(len(flux))
    for i, near in enumerate(tree.query_ball_point(xyz, chord)):
        out[i] = sum(flux[j] for j in near if j != i) / flux[i]
    return out


def _unit(ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
    r, d = np.radians(np.asarray(ra, dtype=float)), np.radians(np.asarray(dec, dtype=float))
    return np.column_stack([np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)])


@dataclass(frozen=True)
class Trend:
    """One star's straight-line fit: magnitude at PIVOT_YEAR and slope per century, with the slope's
    error, the points kept and the reduced chi-squared."""

    intercept: float
    slope: float
    slope_err: float
    points: int
    reduced_chi2: float


def fit_trend(year: np.ndarray, mag: np.ndarray, err: np.ndarray, clip: float, bin_years: float) -> Trend | None:
    """Weighted straight line in magnitudes per century, clipped at ``clip`` times the scaled errors.
    The slope error is the larger of the fit's (times the root reduced chi-squared, never below 1)
    and the one the ``bin_years`` bin means give when there are at least four bins."""
    t = (year - PIVOT_YEAR) / 100.0
    design = np.c_[np.ones(len(t)), t]
    weight = 1.0 / err**2
    keep = np.ones(len(t), dtype=bool)
    for attempt in range(6):
        if keep.sum() < 3 or np.ptp(t[keep]) == 0:
            return None
        a = design[keep]
        cov = np.linalg.inv(a.T @ (a * weight[keep, None]))
        coef = cov @ (a.T @ (weight[keep] * mag[keep]))
        normalised = (mag - design @ coef) / err
        reduced = float(np.sum(normalised[keep] ** 2) / (keep.sum() - 2)) if keep.sum() > 2 else math.inf
        scale = max(1.0, math.sqrt(reduced))
        new = np.abs(normalised) < clip * scale
        # the last pass only refits: what is returned always belongs to the points kept
        if attempt == 5 or np.array_equal(new, keep):
            break
        keep = new
    slope_err = math.sqrt(cov[1, 1]) * scale
    binned = _binned_slope_err(year[keep], (mag - design @ coef)[keep], t[keep], bin_years)
    return Trend(float(coef[0]), float(coef[1]), max(slope_err, binned), int(keep.sum()), reduced)


def _binned_slope_err(year: np.ndarray, residual: np.ndarray, t: np.ndarray, bin_years: float) -> float:
    if bin_years <= 0:
        return 0.0
    bins = np.floor(year / bin_years)
    labels, index = np.unique(bins, return_inverse=True)
    if len(labels) < 4:
        return 0.0
    means = np.bincount(index, residual) / np.bincount(index)
    centres = np.bincount(index, t) / np.bincount(index)
    design = np.c_[np.ones(len(centres)), centres]
    variance = float(np.var(means, ddof=2))
    return math.sqrt(float(np.linalg.inv(design.T @ design)[1, 1]) * variance)


def peer_model(slope: np.ndarray, err: np.ndarray, colour: np.ndarray, mag: np.ndarray) -> tuple[np.ndarray, float]:
    """The reference stars' slope as a clipped linear function of colour and brightness, and the
    systematic spread that makes their normalised slopes unit-normal in median absolute value."""
    design = _peer_design(colour, mag)
    keep = np.ones(len(slope), dtype=bool)
    for _ in range(5):
        coef = np.linalg.lstsq(design[keep], slope[keep], rcond=None)[0]
        residual = slope - design @ coef
        spread = 1.4826 * float(np.median(np.abs(residual[keep])))
        new = np.abs(residual) < 4.0 * max(spread, 1e-9)
        if np.array_equal(new, keep) or new.sum() < design.shape[1] + 2:
            break
        keep = new
    residual = slope - design @ coef

    def excess(s: float) -> float:
        return float(np.median(np.abs(residual) / np.sqrt(err**2 + s**2))) - NORMAL_MEDIAN_ABS

    upper = max(1.0, float(np.max(np.abs(residual))) * 10.0)
    sigma_sys = optimize.brentq(excess, 0.0, upper) if excess(0.0) > 0 else 0.0
    return coef, float(sigma_sys)


def _peer_design(colour: np.ndarray, mag: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(colour)), colour - 0.9, mag - 12.5])


class CenturyTrend(FieldTest):
    """Has the star brightened or faded over the plates' century, beyond what its field's own stars
    of the same colour and brightness show? (INT-027)"""

    name = "century-trend"
    question = "Has the star brightened or faded over the century, beyond its field's own stars?"
    null_model = "the field's own unblended stars, half calibrating and half scored"
    inputs = ("product:lightcurve", "catalogue:ra_deg,dec_deg,g_mag,bp_rp")
    needs_products = (LIGHTCURVE,)

    def features(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Features:
        anchor_column = setting(thresholds, "anchor_column")
        stars: list[tuple[str, float, float, float, float, float, int]] = []
        curves = []
        listed: list[tuple[float, float, float]] = []
        claims = []
        for b in bundles:
            row = catalogue_row(b, thresholds.get("source_id"), "g_mag")
            products = [p for p in b.products if p.product_type == LIGHTCURVE]
            if row and products and finite(row.get("g_mag")):
                claims.append((products[0].artifact_id.value, float(row["g_mag"]), b.object_key))
        owner = curve_owners(claims)
        for b in bundles:
            row = catalogue_row(b, thresholds.get("source_id"), "g_mag")
            if not row or not finite(row.get("ra_deg"), row.get("dec_deg"), row.get("g_mag")):
                continue
            # every listed star can blend, with or without a colour
            listed.append((float(row["ra_deg"]), float(row["dec_deg"]), float(row["g_mag"])))
            products = [p for p in b.products if p.product_type == LIGHTCURVE]
            if not products or not finite(row.get("bp_rp")):
                continue
            if owner.get(products[0].artifact_id.value) != b.object_key:
                continue
            lc = parse_lightcurve(product_bytes(context, products[0].artifact_id.value))
            good = lc.mag_err > 0
            if good.sum() == 0:
                continue
            anchor = row.get(anchor_column) if anchor_column else None
            stars.append(
                (
                    b.object_key,
                    float(row["ra_deg"]),
                    float(row["dec_deg"]),
                    float(row["g_mag"]),
                    float(row["bp_rp"]),
                    float(anchor) if anchor is not None and finite(anchor) else math.nan,
                    len(listed) - 1,
                )
            )
            curves.append((lc.mjd[good], lc.mag[good], lc.mag_err[good]))
        return self.features_from_curves(stars, curves, listed, thresholds)

    def features_from_curves(
        self,
        stars: Sequence[tuple[str, float, float, float, float, float, int]],
        curves: Sequence[tuple[np.ndarray, np.ndarray, np.ndarray]],
        listed: Sequence[tuple[float, float, float]],
        thresholds: Mapping[str, Any],
    ) -> Features:
        """``listed`` is every catalogue star of the field, the ones with light curves among them
        (``stars``' last element is its row there), so neighbours without plates still blend."""
        every = np.array(listed, dtype=float).reshape(-1, 3)
        blend = blend_fractions(
            every[:, 0], every[:, 1], every[:, 2], float(setting(thresholds, "blend_radius_arcsec"))
        )
        arrays: dict[str, Any] = {
            "ra": np.array([s[1] for s in stars], dtype=float),
            "dec": np.array([s[2] for s in stars], dtype=float),
            "colour": np.array([s[4] for s in stars], dtype=float),
            "anchor": np.array([s[5] for s in stars], dtype=float),
            "blend": blend[[s[6] for s in stars]] if stars else np.zeros(0),
            "curves": list(curves),
        }
        if stars:
            arrays["fits"] = self._fits(arrays, thresholds)
        return Features(tuple(s[0] for s in stars), arrays)

    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        arrays = features.arrays
        n = len(features)
        if n == 0:
            return []
        fits = arrays["fits"] if "fits" in arrays else self._fits(arrays, thresholds)
        eligible = np.array([f is not None for f in fits[0]])
        clean = arrays["blend"] <= float(setting(thresholds, "max_blend"))
        calibrate = arrays.get("calibrate", np.ones(n, dtype=bool)) & eligible & clean
        scored = arrays.get("scored", np.ones(n, dtype=bool)) & eligible
        if calibrate.sum() < int(setting(thresholds, "min_reference")):
            return []
        trends, median_mag = fits
        slope = np.array([t.slope if t else math.nan for t in trends])
        err = np.array([t.slope_err if t else math.nan for t in trends])
        coef, sigma_sys = peer_model(
            slope[calibrate], err[calibrate], arrays["colour"][calibrate], median_mag[calibrate]
        )
        expected = _peer_design(arrays["colour"], median_mag) @ coef
        total = np.sqrt(err**2 + sigma_sys**2)
        z = (slope - expected) / total
        intercept = np.array([t.intercept if t else math.nan for t in trends])
        epoch = float(setting(thresholds, "anchor_epoch"))
        offset = intercept + slope * (epoch - PIVOT_YEAR) / 100.0 - arrays["anchor"]
        anchor_ref = offset[calibrate & np.isfinite(offset)]
        anchor_centre = float(np.median(anchor_ref)) if len(anchor_ref) >= 3 else math.nan
        anchor_spread = (
            1.4826 * float(np.median(np.abs(anchor_ref - anchor_centre))) if len(anchor_ref) >= 3 else math.nan
        )
        out = []
        for i in np.flatnonzero(scored):
            t = trends[i]
            assert t is not None
            details: dict[str, Any] = {
                "slope_mag_per_century": t.slope,
                "slope_err": t.slope_err,
                "peer_slope": float(expected[i]),
                "sigma_sys": sigma_sys,
                "points": t.points,
                "reduced_chi2": t.reduced_chi2,
                "blend_fraction": float(arrays["blend"][i]),
                "reference": bool(calibrate[i]),
                "reference_stars": int(calibrate.sum()),
            }
            if math.isfinite(offset[i]) and math.isfinite(anchor_spread) and anchor_spread > 0:
                details["anchor_offset_mag"] = float(offset[i])
                details["anchor_z"] = float((offset[i] - anchor_centre) / anchor_spread)
            out.append(
                Scored(
                    Subject.of_object(features.keys[i]),
                    statistic=float(z[i]),
                    p_value=float(2.0 * stats.norm.sf(abs(z[i]))),
                    effect_size=float(slope[i] - expected[i]),
                    uncertainty=float(total[i]),
                    details=details,
                )
            )
        return out

    def _fits(self, arrays: Mapping[str, Any], thresholds: Mapping[str, Any]) -> tuple[list[Trend | None], np.ndarray]:
        """Each star's trend after the plate-ensemble correction (None when it has too few plates on
        either side of the gap) and its median magnitude."""
        curves = arrays["curves"]
        n = len(curves)
        star = np.concatenate([np.full(len(c[0]), i) for i, c in enumerate(curves)])
        mjd = np.concatenate([c[0] for c in curves])
        mag = np.concatenate([c[1] for c in curves])
        err = np.concatenate([c[2] for c in curves])
        median_mag = np.array([float(np.median(c[1])) for c in curves])
        residual = mag - median_mag[star]
        reference = arrays["blend"] <= float(setting(thresholds, "max_blend"))
        corrected = mag - self._ensemble(arrays, star, mjd, residual, median_mag, reference, thresholds)
        year = jyear(mjd)
        gap_start, gap_end = float(setting(thresholds, "gap_start")), float(setting(thresholds, "gap_end"))
        trends: list[Trend | None] = []
        order = np.argsort(star, kind="stable")
        bounds = np.searchsorted(star[order], np.arange(n + 1))
        for i in range(n):
            mine = order[bounds[i] : bounds[i + 1]]
            mine = mine[np.isfinite(corrected[mine])]
            y, m, e = year[mine], corrected[mine], err[mine]
            before_n, after_n = int(np.sum(y < gap_start)), int(np.sum(y > gap_end))
            enough = len(y) >= int(setting(thresholds, "min_points")) and min(before_n, after_n) >= int(
                setting(thresholds, "min_each_side")
            )
            clip, bin_years = float(setting(thresholds, "clip")), float(setting(thresholds, "bin_years"))
            trends.append(fit_trend(y, m, e, clip, bin_years) if enough else None)
        return trends, median_mag

    def _ensemble(
        self,
        arrays: Mapping[str, Any],
        star: np.ndarray,
        mjd: np.ndarray,
        residual: np.ndarray,
        median_mag: np.ndarray,
        reference: np.ndarray,
        thresholds: Mapping[str, Any],
    ) -> np.ndarray:
        """Each point's plate correction: the median residual, on the same exposure, of the k reference
        stars nearest it in (position, magnitude, colour), never itself. NaN where the exposure has
        fewer than k other reference stars."""
        k = int(setting(thresholds, "ensemble_k"))
        scale_arcmin = float(setting(thresholds, "ensemble_arcmin"))
        # offsets from the field's centre, right ascension taken the short way round (a field across 0h)
        ra0 = math.degrees(
            math.atan2(np.mean(np.sin(np.radians(arrays["ra"]))), np.mean(np.cos(np.radians(arrays["ra"]))))
        )
        dec0 = math.radians(float(np.median(arrays["dec"])))
        east = ((arrays["ra"] - ra0 + 180.0) % 360.0 - 180.0) * math.cos(dec0)
        space = np.column_stack(
            [
                east * 60.0 / scale_arcmin,
                arrays["dec"] * 60.0 / scale_arcmin,
                median_mag / float(setting(thresholds, "ensemble_mag")),
                arrays["colour"] / float(setting(thresholds, "ensemble_colour")),
            ]
        )
        groups = exposure_groups(mjd, float(setting(thresholds, "same_exposure_days")))
        correction = np.full(len(mjd), math.nan)
        order = np.argsort(groups, kind="stable")
        bounds = np.flatnonzero(np.r_[True, np.diff(groups[order]) != 0, True])
        for lo, hi in itertools.pairwise(bounds):
            points = order[lo:hi]
            # one residual per reference star on the exposure, however often it was measured there
            ref_points = points[reference[star[points]]]
            ref_stars, index = np.unique(star[ref_points], return_inverse=True)
            if len(ref_stars) < k:
                continue
            ref_residual = group_medians(index, residual[ref_points], len(ref_stars))
            tree = cKDTree(space[ref_stars])
            wanted = min(k + 1, len(ref_stars))
            _, nearest = tree.query(space[star[points]], k=wanted)
            nearest = nearest.reshape(len(points), wanted)
            values = ref_residual[nearest]
            own = ref_stars[nearest] == star[points][:, None]
            # the star itself is dropped when it is among them, else the farthest beyond k
            values = np.where(own, np.nan, values)
            if wanted > k:
                values[~own.any(axis=1), k] = np.nan
            enough = np.sum(np.isfinite(values), axis=1) >= k
            correction[points[enough]] = np.nanmedian(values[enough], axis=1)
        return correction

    def null_features(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> Features:
        n = len(features)
        half = rng.random(n) < 0.5
        clean = features.arrays["blend"] <= float(setting(thresholds, "max_blend"))
        fits = features.arrays["fits"] if "fits" in features.arrays else self._fits(features.arrays, thresholds)
        arrays = {**features.arrays, "fits": fits, "calibrate": half, "scored": ~half & clean}
        return Features(features.keys, arrays)
