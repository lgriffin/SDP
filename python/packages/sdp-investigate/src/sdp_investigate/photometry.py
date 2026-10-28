"""Photometric tests: colour–magnitude coherence, photometric corroboration and variability."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from astropy.timeseries import LombScargle
from scipy import stats

from sdp.kernel.domain.investigation import EvidenceBundle, Subject
from sdp.kernel.science.investigation import product_bytes
from sdp.kernel.science.plan import TaskContext
from sdp_investigate.base import Features, FieldTest, Scored, catalogue_row, finite, robust_sigma
from sdp_investigate.formats import LIGHTCURVE, parse_lightcurve


def robust_polyfit(x: np.ndarray, y: np.ndarray, degree: int, clip: float = 3.0, iterations: int = 3) -> np.ndarray:
    """Least squares with iterative sigma clipping, so the field sequence is not pulled by outliers."""
    keep = np.ones(len(x), dtype=bool)
    coeffs = np.polyfit(x, y, degree)
    for _ in range(iterations):
        residual = y - np.polyval(coeffs, x)
        sigma = robust_sigma(residual[keep])
        new = np.abs(residual) <= clip * sigma
        if new.sum() <= degree + 1 or np.array_equal(new, keep):
            break
        keep = new
        coeffs = np.polyfit(x[keep], y[keep], degree)
    return np.asarray(coeffs)


def _sequence_scores(keys: tuple[str, ...], x: np.ndarray, y: np.ndarray, degree: int, label: str) -> list[Scored]:
    if len(x) < degree + 3:
        return []
    coeffs = robust_polyfit(x, y, degree)
    residual = y - np.polyval(coeffs, x)
    sigma = robust_sigma(residual)
    out = []
    for key, r in zip(keys, residual, strict=True):
        z = float(r / sigma)
        out.append(
            Scored(
                Subject.of_object(key),
                statistic=abs(z),
                p_value=float(2 * stats.norm.sf(abs(z))),
                effect_size=float(r),
                details={"z": z, f"{label}_sigma": sigma, "fit": [float(c) for c in coeffs]},
            )
        )
    return out


class ColourMagnitudeCoherence(FieldTest):
    """Does the object sit off the field's colour–magnitude sequence?

    Absolute G against BP−RP for objects with positive parallax, a sigma-clipped quadratic as the
    field sequence; the statistic is the robust z of the object's offset from it.
    """

    name = "cmd-coherence"
    question = "Do members form a sequence tighter than the field?"
    null_model = "colours shuffled among the field's objects (field comparison sample)"
    inputs = ("catalogue:gaia",)

    def features(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Features:
        keys, colour, absmag = [], [], []
        for b in bundles:
            row = catalogue_row(b, thresholds.get("source_id"), "bp_rp")
            if not row or not finite(row.get("g_mag"), row.get("bp_rp"), row.get("parallax_mas")):
                continue
            plx = float(row["parallax_mas"])
            if plx <= 0:
                continue
            keys.append(b.object_key)
            colour.append(float(row["bp_rp"]))
            absmag.append(float(row["g_mag"]) + 5 * math.log10(plx / 100.0))
        return Features(tuple(keys), {"colour": np.array(colour), "absmag": np.array(absmag)})

    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        degree = int(thresholds.get("degree", 2))
        return _sequence_scores(features.keys, features.arrays["colour"], features.arrays["absmag"], degree, "sequence")

    def null_features(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> Features:
        return Features(features.keys, {**features.arrays, "colour": rng.permutation(features.arrays["colour"])})


class PhotometricCorroboration(FieldTest):
    """Do the infrared colours (2MASS J−K) agree with the Gaia BP−RP the object is classified by?

    A sigma-clipped linear J−K against BP−RP relation over the field; the statistic is the robust z
    of the object's departure from it.
    """

    name = "photometric-corroboration"
    question = "Do 2MASS and Pan-STARRS colours agree with the Gaia-based classification?"
    null_model = "field colour distribution: infrared colours shuffled among the field's objects"
    inputs = ("catalogue:gaia", "catalogue:2mass")

    def features(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Features:
        keys, bp_rp, jk = [], [], []
        for b in bundles:
            gaia = catalogue_row(b, thresholds.get("source_id"), "bp_rp")
            ir = catalogue_row(b, thresholds.get("ir_source_id"), "k_mag")
            if not gaia or not ir or not finite(gaia.get("bp_rp"), ir.get("j_mag"), ir.get("k_mag")):
                continue
            keys.append(b.object_key)
            bp_rp.append(float(gaia["bp_rp"]))
            jk.append(float(ir["j_mag"]) - float(ir["k_mag"]))
        return Features(tuple(keys), {"bp_rp": np.array(bp_rp), "jk": np.array(jk)})

    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        return _sequence_scores(features.keys, features.arrays["bp_rp"], features.arrays["jk"], 1, "colour")

    def null_features(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> Features:
        return Features(features.keys, {**features.arrays, "jk": rng.permutation(features.arrays["jk"])})


class Variability(FieldTest):
    """Is the object variable over the available baseline (DASCH, ZTF, TESS light curves)?

    χ² of the light curve about its weighted mean (dof n−1) decides; the Lomb–Scargle peak
    (astropy.timeseries) is reported alongside, with its false-alarm probability.
    """

    name = "variability"
    question = "Is the object variable over the available baseline?"
    null_model = "constant source with the series' own noise"
    inputs = ("product:lightcurve",)
    needs_products = (LIGHTCURVE,)

    def features(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Features:
        keys, curves = [], []
        for b in bundles:
            products = [p for p in b.products if p.product_type == LIGHTCURVE]
            if not products:
                continue
            lc = parse_lightcurve(product_bytes(context, products[0].artifact_id.value))
            if len(lc.mjd) >= int(thresholds.get("min_points", 3)) and np.all(lc.mag_err > 0):
                keys.append(b.object_key)
                curves.append((lc.mjd, lc.mag, lc.mag_err))
        return Features(tuple(keys), {"curves": curves})

    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        out = []
        for key, (t, mag, err) in zip(features.keys, features.arrays["curves"], strict=True):
            w = 1 / err**2
            mean = float(np.sum(w * mag) / np.sum(w))
            chi2 = float(np.sum(((mag - mean) / err) ** 2))
            dof = len(mag) - 1
            details: dict[str, Any] = {"points": len(mag), "baseline_days": float(t[-1] - t[0]), "dof": dof}
            if len(mag) >= 5 and t[-1] > t[0]:
                ls = LombScargle(t, mag, err)
                frequency, power = ls.autopower(samples_per_peak=5, nyquist_factor=2, maximum_frequency=None)
                if len(power):
                    best = int(np.argmax(power))
                    details["ls_period_days"] = float(1 / frequency[best])
                    details["ls_power"] = float(power[best])
                    details["ls_false_alarm"] = float(ls.false_alarm_probability(power[best]))
            out.append(
                Scored(
                    Subject.of_object(key),
                    statistic=chi2,
                    p_value=float(stats.chi2.sf(chi2, dof)),
                    effect_size=float(np.std(mag)),
                    details=details,
                )
            )
        return out

    def null_features(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> Features:
        curves = []
        for t, mag, err in features.arrays["curves"]:
            w = 1 / err**2
            mean = float(np.sum(w * mag) / np.sum(w))
            curves.append((t, mean + rng.normal(0.0, err), err))
        return Features(features.keys, {"curves": curves})
