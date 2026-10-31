"""Cross-survey contradictions: the same star measured by two surveys, disagreeing beyond what their
uncertainties allow (INV-026).

Matching tells us 2MASS, Pan-STARRS and Gaia saw the same star. This test asks the next question: do
their measurements of it agree? Each *channel* is one quantity a survey shares with Gaia:

- **position**: where the survey saw the star at its own epoch, against Gaia's position moved there
  along Gaia's proper motion (2MASS about 17 years back, Pan-STARRS about 4);
- **band**: the survey's magnitude in one band, against what Gaia's G and BP-RP predict for it.

The prediction for a band is fitted to the field itself, a sigma-clipped quadratic of (G - band)
against BP-RP, so no published transformation coefficients are typed in and the field's own
reddening and zero point are absorbed.

**The uncertainty budget.** Each comparison's expected uncertainty is the quadrature sum of the
errors the rows report (each row's own when it carries them; Gaia's proper-motion error grown over
the baseline) and a calibration term ``s`` the field sets: the extra scatter that makes the median
normalised squared residual equal its expected value under the null (the chi-square median: 0.455
for one dimension, 1.386 for two). ``s`` is solved separately in G-magnitude bins, because faint
stars scatter more, after the field's median offset (frame tie, photometric zero point) is removed.
Every term is written into the result.

**Set aside, never counted.** A comparison is set aside, with its reason, when a lower-resolution
survey cannot separate the star from a neighbour (another examined Gaia source inside the survey's
resolution and within ``blend_delta_mag`` in G), when the survey flags the band as poor, or when a
channel has too few stars to calibrate. Only stars the profile examines count as neighbours, so a
fainter star inside the beam shows as a small offset rather than as a blend.

**The statistic.** One-dimensional comparisons give z; a position's two-dimensional distance is
turned into the z with the same two-sided p-value. A star's statistic is its largest counted |z|,
and its p-value is Sidak-corrected over the comparisons counted, so a star with eight comparisons is
not flagged more easily than a star with two. ``pattern`` labels what kind of disagreement it is, a
hint for the scientist and never a verdict. A star whose every comparison was set aside still gets a
result, with no p-value, so the scientist sees why it could not be judged.

**Null fields.** Every residual is redrawn from its own budget around the field's offset and the
whole calibration runs again, so the null false-alarm rate tests the budget, not just the data.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from scipy import stats
from scipy.spatial import cKDTree

from sdp.kernel.domain.errors import PlanInvalid
from sdp.kernel.domain.investigation import EvidenceBundle, Subject
from sdp.kernel.science.plan import TaskContext
from sdp_investigate.astrometry import MAS_PER_DEG, ra_offset_deg
from sdp_investigate.base import Features, FieldTest, Scored, catalogue_row, finite
from sdp_investigate.photometry import robust_polyfit

POSITION = "position"
BAND = "band"
#: the median of chi-square with 1 and 2 degrees of freedom: what a normalised squared residual's
#: median is when the budget is right
CHI2_MEDIAN = {d: float(stats.chi2.median(d)) for d in (1, 2)}
#: the clip a calibration round applies (the 3-sigma two-sided tail) and the chi-square mean below it
CLIP = {d: float(stats.chi2.isf(2 * stats.norm.sf(3.0), d)) for d in (1, 2)}
CLIPPED_MEAN = {d: d * float(stats.chi2.cdf(CLIP[d], d + 2) / stats.chi2.cdf(CLIP[d], d)) for d in (1, 2)}
#: reasons a comparison is set aside
BLENDED = "blended"
POOR_QUALITY = "poor quality"
TOO_FEW = "too few stars to calibrate"
#: a magnitude outside this range is a sentinel (Pan-STARRS writes -999), not a measurement
MAG_RANGE = (-2.0, 30.0)


@dataclass(frozen=True)
class Band:
    name: str
    column: str
    """The canonical column; the error, when a row has one, is ``<column>_err``."""
    flag: str
    """The archive's own column name, read from ``quality_flags`` when the canonical one is absent."""
    quality_index: int | None = None
    """The band's letter in the survey's quality string (2MASS ``ph_qual`` is J, H, Ks)."""


@dataclass(frozen=True)
class Survey:
    survey: str
    resolution_arcsec: float
    """Two sources closer than this blend in the survey's measurement."""
    bands: tuple[Band, ...]
    quality_flag: str | None = None
    good_quality: str = ""


SURVEYS: dict[str, Survey] = {
    "2mass": Survey(
        "2mass",
        2.5,
        (
            Band("J", "j_mag", "j_m", 0),
            Band("H", "h_mag", "h_m", 1),
            Band("Ks", "k_mag", "k_m", 2),
        ),
        quality_flag="ph_qual",
        good_quality="AB",
    ),
    "ps1": Survey(
        "ps1",
        1.5,
        (
            Band("g", "ps_g_mag", "gMeanPSFMag"),
            Band("r", "ps_r_mag", "rMeanPSFMag"),
            Band("i", "ps_i_mag", "iMeanPSFMag"),
            Band("z", "ps_z_mag", "zMeanPSFMag"),
            Band("y", "ps_y_mag", "yMeanPSFMag"),
        ),
    ),
}

DEFAULTS: dict[str, Any] = {
    "source_id": "gaia",
    "surveys": {"2mass": "2mass", "ps1": "ps1"},
    "blend_delta_mag": 2.5,
    "magnitude_bins": 3,
    "min_per_bin": 15,
    "degree": 2,
    "flag_z": 3.0,
}


@dataclass
class Channel:
    """One quantity a survey shares with Gaia, for every star that has both."""

    name: str
    survey: str
    kind: str
    index: np.ndarray
    """Each entry's position in the feature keys."""
    g_mag: np.ndarray
    bp_rp: np.ndarray
    observed: np.ndarray
    """A band's survey magnitude; a position's (dRA*cos dec, dDec) in mas, survey minus Gaia."""
    sigma: np.ndarray
    """The reported uncertainty: per axis for a position."""
    epoch_gap: np.ndarray
    reasons: list[str | None]

    @property
    def dof(self) -> int:
        return 2 if self.kind == POSITION else 1

    def with_observed(self, observed: np.ndarray) -> Channel:
        return replace(self, observed=observed)


@dataclass(frozen=True)
class Calibration:
    """A channel after the field has set its budget."""

    residual: np.ndarray
    """Observed minus predicted, then minus the field's offset."""
    offset: np.ndarray | float
    predicted: np.ndarray | None
    calibration: np.ndarray
    total: np.ndarray
    z: np.ndarray
    counted: np.ndarray
    reasons: list[str | None]
    fit: list[float] | None


def settings(thresholds: Mapping[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULTS, **thresholds}
    surveys = merged["surveys"]
    if not isinstance(surveys, Mapping) or not surveys:
        raise PlanInvalid("cross-survey-contradiction names its surveys as {survey: source_id}")
    unknown = sorted(set(surveys) - set(SURVEYS))
    if unknown:
        raise PlanInvalid(f"cross-survey-contradiction knows the surveys {sorted(SURVEYS)}, not {unknown}")
    if int(merged["min_per_bin"]) < 5 or int(merged["magnitude_bins"]) < 1:
        raise PlanInvalid("cross-survey-contradiction needs min_per_bin >= 5 and magnitude_bins >= 1")
    return merged


def _solve(r2: np.ndarray, sigma2: np.ndarray, statistic: Any, target: float) -> float:
    """The smallest s >= 0 with statistic(r2 / (sigma2 + s^2)) <= target, by bisection."""
    if len(r2) == 0 or statistic(r2 / np.maximum(sigma2, 1e-300)) <= target:
        return 0.0
    lo, hi = 0.0, math.sqrt(float(np.max(r2)) / target) + 1e-12
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if statistic(r2 / (sigma2 + mid**2)) > target:
            lo = mid
        else:
            hi = mid
    return hi


def solve_calibration(r2: np.ndarray, sigma2: np.ndarray, dof: int) -> float:
    """The extra scatter ``s`` the reported errors miss, or 0 when they already account for it.

    A start from the median (the chi-square median for ``dof``), robust to the contradictions
    themselves, then a few rounds of the clipped mean: comparisons beyond the 3-sigma tail are left
    out and ``s`` is set so the rest have the truncated chi-square mean. The clipped mean is about
    three times as efficient as the median, which matters because ``s`` sets every z in its bin."""
    s = _solve(r2, sigma2, lambda x: float(np.median(x)), CHI2_MEDIAN[dof])
    for _ in range(5):
        keep = r2 / np.maximum(sigma2 + s**2, 1e-300) < CLIP[dof]
        if keep.sum() < 3:
            break
        s = _solve(r2[keep], sigma2[keep], lambda x: float(np.mean(x)), CLIPPED_MEAN[dof])
    return s


def p_value(d2: np.ndarray, dof: int, nu: np.ndarray | None = None) -> np.ndarray:
    """The two-sided p-value of a normalised squared residual. With ``nu``, the number of stars
    whose scatter set the budget, it allows for that budget being an estimate: Student's t for one
    dimension, F(2, 2 nu) for a position, instead of the normal and chi-square tails."""
    if nu is None:
        return np.asarray(stats.chi2.sf(d2, dof))
    if dof == 1:
        return np.asarray(2.0 * stats.t.sf(np.sqrt(d2), nu))
    return np.asarray(stats.f.sf(d2 / 2.0, 2, 2 * nu))


def equivalent_z(d2: np.ndarray, dof: int, nu: np.ndarray | None = None) -> np.ndarray:
    """The |z| with the same two-sided normal p-value, so every channel speaks in one unit."""
    p = p_value(d2, dof, nu)
    return np.asarray(stats.norm.isf(np.clip(p, 1e-300, 1.0) / 2.0))


def magnitude_bins(g_mag: np.ndarray, counted: np.ndarray, bins: int, min_per_bin: int) -> np.ndarray:
    """Each entry's G-magnitude bin; edges are quantiles of the counted entries, as many bins as
    hold ``min_per_bin`` counted stars each, at most ``bins``."""
    n = int(counted.sum())
    k = max(1, min(bins, n // min_per_bin))
    if k == 1:
        return np.zeros(len(g_mag), dtype=int)
    edges = np.quantile(g_mag[counted], np.linspace(0.0, 1.0, k + 1)[1:-1])
    return np.asarray(np.searchsorted(edges, g_mag, side="right"))


def calibrate(channel: Channel, cfg: Mapping[str, Any]) -> Calibration:
    counted = np.array([r is None for r in channel.reasons], dtype=bool)
    reasons = list(channel.reasons)
    min_per_bin = int(cfg["min_per_bin"])
    n = len(channel.index)
    if int(counted.sum()) < min_per_bin:
        reasons = [r or TOO_FEW for r in reasons]
        nan = np.full(n, math.nan)
        shape = (n, 2) if channel.kind == POSITION else (n,)
        return Calibration(np.full(shape, math.nan), math.nan, None, nan, nan, nan, np.zeros(n, bool), reasons, None)
    fit: list[float] | None = None
    predicted: np.ndarray | None = None
    if channel.kind == BAND:
        colour_term = channel.g_mag - channel.observed
        coeffs = robust_polyfit(channel.bp_rp[counted], colour_term[counted], int(cfg["degree"]))
        fit = [float(c) for c in coeffs]
        predicted = channel.g_mag - np.polyval(coeffs, channel.bp_rp)
        raw = channel.observed - predicted
        offset: np.ndarray | float = float(np.median(raw[counted]))
        residual = raw - offset
        r2 = residual**2
    else:
        offset = np.median(channel.observed[counted], axis=0)
        residual = channel.observed - offset
        r2 = np.sum(residual**2, axis=1)
    sigma2 = channel.sigma**2
    bins = magnitude_bins(channel.g_mag, counted, int(cfg["magnitude_bins"]), min_per_bin)
    calibration = np.zeros(n)
    nu = np.zeros(n)
    for b in np.unique(bins):
        here = bins == b
        calibration[here] = solve_calibration(r2[here & counted], sigma2[here & counted], channel.dof)
        nu[here] = max(int((here & counted).sum()) - (int(cfg["degree"]) + 1 if channel.kind == BAND else 1), 1)
    total = np.sqrt(sigma2 + calibration**2)
    d2 = r2 / np.maximum(total**2, 1e-300)
    return Calibration(
        residual, offset, predicted, calibration, total, equivalent_z(d2, channel.dof, nu), counted, reasons, fit
    )


def _signed_z(channel: Channel, cal: Calibration, i: int) -> float:
    z = float(cal.z[i])
    if channel.kind == BAND and cal.residual[i] < 0:
        return -z
    return z


def pattern(flagged: Sequence[tuple[Channel, float]]) -> str:
    """What kind of disagreement the flagged comparisons make: a hint, never a verdict."""
    if not flagged:
        return "consistent"
    positions = [c for c, _ in flagged if c.kind == POSITION]
    bands = [(c, z) for c, z in flagged if c.kind == BAND]
    if positions and bands:
        return "position-and-brightness"
    if positions:
        return "position"
    if len(bands) == 1:
        return "one-band"
    signs = {z > 0 for _, z in bands}
    return "all-bands-one-way" if len(signs) == 1 else "colour"


PATTERNS = {
    "not-assessable": "every comparison was set aside (blended, flagged poor, or too few stars to calibrate)",
    "consistent": "every counted comparison agrees within its budget",
    "position": "the survey saw it away from where Gaia's motion puts it: a wrong proper motion, an "
    "unresolved companion, or a different star matched",
    "one-band": "a single band disagrees: an infrared excess, variability caught in one band, or a bad measurement",
    "all-bands-one-way": "every flagged band is offset the same way: variability between the epochs, or a wrong match",
    "colour": "bands disagree in different directions: the spectrum differs from what Gaia's colour predicts "
    "(dust, a companion, an unusual star)",
    "position-and-brightness": "both position and brightness disagree: most often a different star was matched",
}


class CrossSurveyContradiction(FieldTest):
    """Do the surveys' measurements of the same star disagree beyond their expected uncertainties?"""

    name = "cross-survey-contradiction"
    question = "Do 2MASS and Pan-STARRS measure this star where and as bright as Gaia predicts?"
    null_model = "every residual redrawn from its own uncertainty budget about the field's offset, then recalibrated"
    inputs = ("catalogue:gaia", "catalogue:2mass", "catalogue:ps1")

    def features(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Features:
        cfg = settings(thresholds)
        gaia_rows: list[tuple[EvidenceBundle, Mapping[str, Any]]] = []
        for b in bundles:
            row = catalogue_row(b, cfg["source_id"], "g_mag")
            if row and finite(row.get("ra_deg"), row.get("dec_deg"), row.get("g_mag")):
                gaia_rows.append((b, row))
        keys = tuple(b.object_key for b, _ in gaia_rows)
        channels: list[Channel] = []
        if not gaia_rows:
            return Features(keys, {"channels": channels})
        ra = np.array([float(r["ra_deg"]) for _, r in gaia_rows])
        dec = np.array([float(r["dec_deg"]) for _, r in gaia_rows])
        g = np.array([float(r["g_mag"]) for _, r in gaia_rows])
        tree = cKDTree(_unit(ra, dec))
        delta = float(cfg["blend_delta_mag"])
        for survey_id, source_id in sorted(cfg["surveys"].items()):
            survey = SURVEYS[survey_id]
            resolution = float(cfg.get(f"{survey_id}_resolution_arcsec", survey.resolution_arcsec))
            chord = 2.0 * math.sin(math.radians(resolution / 3600.0) / 2.0)
            builders = {POSITION: _Builder()} | {band.name: _Builder() for band in survey.bands}
            for i, (b, gaia) in enumerate(gaia_rows):
                rows = b.rows(source_id)
                if not rows:
                    continue
                row = rows[0]
                neighbours = [
                    j for j in tree.query_ball_point(_unit(ra[i : i + 1], dec[i : i + 1])[0], chord) if j != i
                ]
                blended = any(g[j] - g[i] < delta for j in neighbours)
                bp_rp = float(gaia["bp_rp"]) if finite(gaia.get("bp_rp")) else math.nan
                _position(builders[POSITION], i, gaia, row, g[i], bp_rp, blended)
                for band in survey.bands:
                    if math.isnan(bp_rp):
                        continue
                    _band(builders[band.name], i, band, survey, row, g[i], bp_rp, gaia, blended)
            for name, builder in builders.items():
                if builder.index:
                    kind = POSITION if name == POSITION else BAND
                    channels.append(builder.build(f"{survey_id}:{name}", survey_id, kind))
        return Features(keys, {"channels": channels})

    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        cfg = settings(thresholds)
        flag_z = float(cfg["flag_z"])
        per_object: dict[int, list[tuple[Channel, Calibration, int]]] = {}
        for channel in features.arrays["channels"]:
            cal = calibrate(channel, cfg)
            for e, i in enumerate(channel.index):
                per_object.setdefault(int(i), []).append((channel, cal, e))
        out: list[Scored] = []
        for i in sorted(per_object):
            entries = per_object[i]
            counted = [(c, cal, e) for c, cal, e in entries if cal.counted[e]]
            records = [_record(c, cal, e) for c, cal, e in sorted(entries, key=lambda t: t[0].name)]
            if not counted:
                # every comparison was set aside: nothing to judge, but the scientist sees why
                out.append(
                    Scored(
                        Subject.of_object(features.keys[i]),
                        statistic=0.0,
                        p_value=None,
                        details={
                            "worst_channel": None,
                            "comparisons_counted": 0,
                            "comparisons_set_aside": len(entries),
                            "flagged": [],
                            "pattern": "not-assessable",
                            "pattern_reading": PATTERNS["not-assessable"],
                            "comparisons": records,
                        },
                    )
                )
                continue
            worst_channel, worst_cal, worst_e = max(counted, key=lambda t: (float(t[1].z[t[2]]), t[0].name))
            z_max = float(worst_cal.z[worst_e])
            n = len(counted)
            p_one = float(2.0 * stats.norm.sf(z_max))
            p = float(-np.expm1(n * np.log1p(-min(p_one, 1.0 - 1e-16))))
            flagged = [(c, _signed_z(c, cal, e)) for c, cal, e in counted if float(cal.z[e]) >= flag_z]
            kind = pattern(flagged)
            out.append(
                Scored(
                    Subject.of_object(features.keys[i]),
                    statistic=z_max,
                    p_value=p,
                    effect_size=_magnitude(worst_channel, worst_cal, worst_e),
                    uncertainty=float(worst_cal.total[worst_e]),
                    details={
                        "worst_channel": worst_channel.name,
                        "comparisons_counted": n,
                        "comparisons_set_aside": len(entries) - n,
                        "flagged": sorted(c.name for c, _ in flagged),
                        "pattern": kind,
                        "pattern_reading": PATTERNS[kind],
                        "comparisons": records,
                    },
                )
            )
        return out

    def null_features(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> Features:
        cfg = settings(thresholds)
        channels = []
        for channel in features.arrays["channels"]:
            cal = calibrate(channel, cfg)
            if not cal.counted.any():
                channels.append(channel)
                continue
            total = np.where(np.isfinite(cal.total), cal.total, 0.0)
            if channel.kind == BAND:
                assert cal.predicted is not None
                drawn = cal.predicted + float(cal.offset) + rng.normal(0.0, 1.0, len(total)) * total
            else:
                drawn = np.asarray(cal.offset) + rng.normal(0.0, 1.0, (len(total), 2)) * total[:, None]
            channels.append(channel.with_observed(drawn))
        return Features(features.keys, {"channels": channels})


class _Builder:
    def __init__(self) -> None:
        self.index: list[int] = []
        self.g: list[float] = []
        self.bp_rp: list[float] = []
        self.observed: list[Any] = []
        self.sigma: list[float] = []
        self.gap: list[float] = []
        self.reasons: list[str | None] = []

    def add(self, i: int, g: float, bp_rp: float, observed: Any, sigma: float, gap: float, reason: str | None) -> None:
        self.index.append(i)
        self.g.append(g)
        self.bp_rp.append(bp_rp)
        self.observed.append(observed)
        self.sigma.append(sigma)
        self.gap.append(gap)
        self.reasons.append(reason)

    def build(self, name: str, survey: str, kind: str) -> Channel:
        return Channel(
            name,
            survey,
            kind,
            np.array(self.index, dtype=int),
            np.array(self.g),
            np.array(self.bp_rp),
            np.array(self.observed, dtype=float),
            np.array(self.sigma),
            np.array(self.gap),
            list(self.reasons),
        )


def _unit(ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
    r, d = np.radians(ra), np.radians(dec)
    return np.column_stack((np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)))


def _flags(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """The archive's own values a dialect keeps: canonical rows carry them as a JSON object."""
    flags = row.get("quality_flags")
    if isinstance(flags, str):
        try:
            flags = json.loads(flags)
        except ValueError:
            return {}
    return flags if isinstance(flags, Mapping) else {}


def _number(row: Mapping[str, Any], *names: str) -> float | None:
    flags = _flags(row)
    for name in names:
        for source in (row, flags):
            value = source.get(name) if isinstance(source, Mapping) else None
            if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
                return float(value)
    return None


def _position(
    builder: _Builder,
    i: int,
    gaia: Mapping[str, Any],
    row: Mapping[str, Any],
    g: float,
    bp_rp: float,
    blended: bool,
) -> None:
    epoch = _number(row, "epoch_jyear")
    ra, dec = _number(row, "ra_deg"), _number(row, "dec_deg")
    motion = (gaia.get("pm_ra_cosdec_masyr"), gaia.get("pm_dec_masyr"), gaia.get("epoch_jyear"))
    if epoch is None or ra is None or dec is None or not finite(*motion):
        return
    dt = epoch - float(gaia["epoch_jyear"])
    cosd = math.cos(math.radians(float(gaia["dec_deg"])))
    pred_dec = float(gaia["dec_deg"]) + float(gaia["pm_dec_masyr"]) * dt / MAS_PER_DEG
    pred_ra = float(gaia["ra_deg"]) + float(gaia["pm_ra_cosdec_masyr"]) * dt / MAS_PER_DEG / cosd
    dra = float(ra_offset_deg(np.array([ra]), np.array([pred_ra]))[0]) * cosd * MAS_PER_DEG
    ddec = (dec - pred_dec) * MAS_PER_DEG
    pm_err = math.hypot(
        float(gaia.get("pm_ra_cosdec_masyr_err") or 0.0), float(gaia.get("pm_dec_masyr_err") or 0.0)
    ) / math.sqrt(2.0)
    sigma = math.hypot(_number(row, "pos_err_mas") or 0.0, pm_err * abs(dt))
    builder.add(i, g, bp_rp, (dra, ddec), sigma, dt, BLENDED if blended else None)


def _band(
    builder: _Builder,
    i: int,
    band: Band,
    survey: Survey,
    row: Mapping[str, Any],
    g: float,
    bp_rp: float,
    gaia: Mapping[str, Any],
    blended: bool,
) -> None:
    mag = _number(row, band.column, band.flag)
    if mag is None or not MAG_RANGE[0] < mag < MAG_RANGE[1]:
        return
    reason: str | None = BLENDED if blended else None
    if reason is None and survey.quality_flag and band.quality_index is not None:
        quality = str(_flags(row).get(survey.quality_flag) or "")
        if len(quality) > band.quality_index and quality[band.quality_index] not in survey.good_quality:
            reason = POOR_QUALITY
    epoch = _number(row, "epoch_jyear")
    gap = epoch - float(gaia["epoch_jyear"]) if epoch is not None and finite(gaia.get("epoch_jyear")) else math.nan
    builder.add(i, g, bp_rp, mag, _number(row, f"{band.column}_err") or 0.0, gap, reason)


def _magnitude(channel: Channel, cal: Calibration, e: int) -> float:
    if channel.kind == POSITION:
        return float(np.hypot(*cal.residual[e]))
    return float(cal.residual[e])


def _clean(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def _record(channel: Channel, cal: Calibration, e: int) -> dict[str, Any]:
    """One comparison as the dossier shows it: values, every budget term, z, and whether it counted."""
    record: dict[str, Any] = {
        "channel": channel.name,
        "survey": channel.survey,
        "kind": channel.kind,
        "counted": bool(cal.counted[e]),
        "set_aside": cal.reasons[e],
        "epoch_gap_years": _clean(float(channel.epoch_gap[e])),
        "reported_sigma": _clean(float(channel.sigma[e])),
        "calibration_sigma": _clean(float(cal.calibration[e])),
        "total_sigma": _clean(float(cal.total[e])),
        "z": _clean(_signed_z(channel, cal, e)) if math.isfinite(float(cal.z[e])) else None,
    }
    if channel.kind == POSITION:
        record |= {
            "unit": "mas",
            "offset_mas": [_clean(float(v)) for v in channel.observed[e]],
            "field_offset_mas": [_clean(float(v)) for v in np.atleast_1d(cal.offset)],
            "residual_mas": _clean(_magnitude(channel, cal, e)),
        }
    else:
        record |= {
            "unit": "mag",
            "observed_mag": _clean(float(channel.observed[e])),
            "predicted_mag": _clean(float(cal.predicted[e])) if cal.predicted is not None else None,
            "field_offset_mag": _clean(float(cal.offset)),
            "residual_mag": _clean(float(cal.residual[e])),
        }
    return record
