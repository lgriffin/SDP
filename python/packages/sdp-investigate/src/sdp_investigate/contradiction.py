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
errors the rows report (each row's own when it carries them: 2MASS ``*_msigcom`` and its error
ellipse, Pan-STARRS PSF magnitude and mean position errors, INV-027; Gaia's proper-motion error grown
over the baseline) and a calibration term ``s`` the field sets: the extra scatter that makes the median
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

from sdp.kernel.domain.association import Detection
from sdp.kernel.domain.errors import PlanInvalid
from sdp.kernel.domain.investigation import EvidenceBundle, Subject
from sdp.kernel.ports.investigate import MEASURED, OFF_IMAGE, ForcedPhotometer, SkyPoint
from sdp.kernel.science.plan import TaskContext
from sdp_investigate.astrometry import MAS_PER_DEG, ra_offset_deg
from sdp_investigate.base import Features, FieldTest, Scored, catalogue_row, finite
from sdp_investigate.changes import Frame, _photometer_params, _points, aperture, enclosed, prepare_frame
from sdp_investigate.imaging import IMAGE, anchor, detection_records
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
CONTAMINATED = "contaminated (a diffraction spike, a halo, a ghost or persistence)"
SATURATED_ON_IMAGE = "saturated or masked on the image"
#: neighbours are first looked for this much wider than a survey's resolution, then judged where both
#: stars were at the survey's epoch: 2 arcsec covers 100 mas/yr over 20 years
BLEND_MARGIN_ARCSEC = 2.0
#: a reported error at or above these is no measurement of the error (a sentinel or a failed fit)
MAX_MAG_ERROR = 5.0
MAX_POSITION_ERROR_MAS = 10_000.0
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
    error: str | None = None
    """The archive's own magnitude error, read from ``quality_flags`` when ``<column>_err`` is absent."""


@dataclass(frozen=True)
class Survey:
    survey: str
    resolution_arcsec: float
    """Two sources closer than this blend in the survey's measurement."""
    bands: tuple[Band, ...]
    quality_flag: str | None = None
    """The survey's per-band quality string; a band needs a good letter in it to count."""
    good_quality: str = ""
    row_quality: bool = False
    """Set aside every comparison of a row whose canonical ``quality_class`` is ``poor`` (Pan-STARRS:
    fewer than three detections)."""
    single_epoch: bool = False
    """Positions are measured at one date, so they carry the star's parallax shift on that date
    (2MASS); a mean over many dates (Pan-STARRS) averages most of it away."""
    position_error: tuple[str, str] | None = None
    """The archive's two 1-sigma position errors in arcsec (2MASS: the error ellipse's axes;
    Pan-STARRS: RA and Dec), read from ``quality_flags`` when ``pos_err_mas`` is absent."""
    clean_flag: str | None = None
    """The survey's per-band contamination string (AllWISE ``cc_flags``): a band counts only when its
    letter is ``0``, and a position only when every band's is."""


SURVEYS: dict[str, Survey] = {
    "2mass": Survey(
        "2mass",
        2.5,
        (
            Band("J", "j_mag", "j_m", 0, "j_msigcom"),
            Band("H", "h_mag", "h_m", 1, "h_msigcom"),
            Band("Ks", "k_mag", "k_m", 2, "k_msigcom"),
        ),
        quality_flag="ph_qual",
        good_quality="AB",
        single_epoch=True,
        position_error=("err_maj", "err_min"),
    ),
    "ps1": Survey(
        "ps1",
        1.5,
        (
            Band("g", "ps_g_mag", "gMeanPSFMag", error="gMeanPSFMagErr"),
            Band("r", "ps_r_mag", "rMeanPSFMag", error="rMeanPSFMagErr"),
            Band("i", "ps_i_mag", "iMeanPSFMag", error="iMeanPSFMagErr"),
            Band("z", "ps_z_mag", "zMeanPSFMag", error="zMeanPSFMagErr"),
            Band("y", "ps_y_mag", "yMeanPSFMag", error="yMeanPSFMagErr"),
        ),
        row_quality=True,
        position_error=("raMeanErr", "decMeanErr"),
    ),
    # W3 and W4 see few stars above their noise and through 6 to 12 arcsec beams, so only W1 and W2
    "allwise": Survey(
        "allwise",
        6.0,
        (
            Band("W1", "w1_mag", "w1mpro", 0, "w1sigmpro"),
            Band("W2", "w2_mag", "w2mpro", 1, "w2sigmpro"),
        ),
        quality_flag="ph_qual",
        good_quality="AB",
        position_error=("sigra", "sigdec"),
        clean_flag="cc_flags",
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
    # Hubble and Webb images, when the profile fetches them (as image-changes does)
    "images": True,
    "align_wcs": True,
    "fwhm_px": 2.5,
    "nsigma": 5.0,
    "anchor_sigma_arcsec": 0.002,
    "min_sigma_arcsec": 0.005,
    "aperture_fwhm": 1.0,
    "image_search_arcsec": 1.0,
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
    if not isinstance(surveys, Mapping):
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
    hold ``min_per_bin`` counted stars each, at most ``bins``, and fewer when tied magnitudes would
    leave a bin short."""
    n = int(counted.sum())
    k = max(1, min(bins, n // min_per_bin))
    while k > 1:
        edges = np.quantile(g_mag[counted], np.linspace(0.0, 1.0, k + 1)[1:-1])
        assigned = np.asarray(np.searchsorted(edges, g_mag, side="right"))
        # tied magnitudes can leave a bin short; then fewer, wider bins
        if np.bincount(assigned[counted], minlength=k).min() >= min_per_bin:
            return assigned
        k -= 1
    return np.zeros(len(g_mag), dtype=int)


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
    version = "1.2.0"
    question = "Do 2MASS, Pan-STARRS, AllWISE and Hubble or Webb images measure this star where and as bright as Gaia predicts?"
    null_model = "every residual redrawn from its own uncertainty budget about the field's offset, then recalibrated"
    inputs = ("catalogue:gaia", "catalogue:2mass", "catalogue:ps1", "catalogue:allwise", "product:image")
    needs_products = (IMAGE,)
    #: images are a channel when the profile fetches them; a profile of catalogues alone runs too
    products_optional = True

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
        pm = np.array([[_or_zero(r.get("pm_ra_cosdec_masyr")), _or_zero(r.get("pm_dec_masyr"))] for _, r in gaia_rows])
        epochs = np.array([_or_zero(r.get("epoch_jyear")) for _, r in gaia_rows])
        tree = cKDTree(_unit(ra, dec))
        delta = float(cfg["blend_delta_mag"])
        for survey_id, source_id in sorted(cfg["surveys"].items()):
            survey = SURVEYS[survey_id]
            resolution = float(cfg.get(f"{survey_id}_resolution_arcsec", survey.resolution_arcsec))
            # look wider than the resolution, then judge each neighbour where both stars were then
            chord = 2.0 * math.sin(math.radians((resolution + BLEND_MARGIN_ARCSEC) / 3600.0) / 2.0)
            builders = {POSITION: _Builder()} | {band.name: _Builder() for band in survey.bands}
            for i, (b, gaia) in enumerate(gaia_rows):
                rows = b.rows(source_id)
                if not rows:
                    continue
                row = rows[0]
                near = [j for j in tree.query_ball_point(_unit(ra[i : i + 1], dec[i : i + 1])[0], chord) if j != i]
                epoch = _number(row, "epoch_jyear")
                when = epoch if epoch is not None else float(epochs[i])
                blended = any(
                    g[j] - g[i] < delta and _separation_then(ra, dec, pm, epochs, i, j, when) < resolution for j in near
                )
                bp_rp = float(gaia["bp_rp"]) if finite(gaia.get("bp_rp")) else math.nan
                poor = survey.row_quality and row.get("quality_class") == "poor"
                contaminated = not all(_clean_band(row, survey, b.quality_index) for b in survey.bands)
                _position(
                    builders[POSITION],
                    i,
                    gaia,
                    row,
                    g[i],
                    bp_rp,
                    blended,
                    poor,
                    survey.single_epoch,
                    survey.position_error,
                    CONTAMINATED if contaminated else None,
                )
                for band in survey.bands:
                    if math.isnan(bp_rp):
                        continue
                    _band(builders[band.name], i, band, survey, row, g[i], bp_rp, gaia, blended, poor)
            for name, builder in builders.items():
                if builder.index:
                    kind = POSITION if name == POSITION else BAND
                    channels.append(builder.build(f"{survey_id}:{name}", survey_id, kind))
        images = {
            p.artifact_id.value: p.product.product_id
            for b, _ in gaia_rows
            for p in b.products
            if p.product_type == IMAGE
        }
        left_out: dict[str, str] = {}
        if images and cfg["images"]:
            field = _Field(gaia_rows, ra, dec, g, pm, epochs, tree, delta)
            channels.extend(_image_channels(sorted(images.items()), field, cfg, context, left_out))
        return Features(keys, {"channels": channels, "images_left_out": left_out})

    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        cfg = settings(thresholds)
        flag_z = float(cfg["flag_z"])
        per_object: dict[int, list[tuple[Channel, Calibration, int]]] = {}
        for channel in features.arrays["channels"]:
            cal = calibrate(channel, cfg)
            for e, i in enumerate(channel.index):
                per_object.setdefault(int(i), []).append((channel, cal, e))
        out: list[Scored] = []
        # images the field could not use, and why: said on every result, so a missing image is seen
        left_out = dict(features.arrays.get("images_left_out") or {})
        extra = {"images_left_out": left_out} if left_out else {}
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
                            **extra,
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
                        **extra,
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
        return Features(
            features.keys, {"channels": channels, "images_left_out": features.arrays.get("images_left_out", {})}
        )


@dataclass(frozen=True)
class _Field:
    """The examined Gaia stars, as the image channels need them."""

    rows: Sequence[tuple[EvidenceBundle, Mapping[str, Any]]]
    ra: np.ndarray
    dec: np.ndarray
    g: np.ndarray
    pm: np.ndarray
    epochs: np.ndarray
    tree: Any
    delta_mag: float

    def blended(self, i: int, reach_arcsec: float, when: float) -> bool:
        """Whether an examined Gaia star no more than ``delta_mag`` fainter sat within ``reach_arcsec``
        of star ``i`` at ``when``."""
        chord = 2.0 * math.sin(math.radians((reach_arcsec + BLEND_MARGIN_ARCSEC) / 3600.0) / 2.0)
        near = self.tree.query_ball_point(_unit(self.ra[i : i + 1], self.dec[i : i + 1])[0], chord)
        return any(
            j != i
            and self.g[j] - self.g[i] < self.delta_mag
            and _separation_then(self.ra, self.dec, self.pm, self.epochs, i, j, when) < reach_arcsec
            for j in near
        )


def _image_channels(
    images: Sequence[tuple[str, str]],
    stars: _Field,
    cfg: Mapping[str, Any],
    context: TaskContext,
    left_out: dict[str, str],
) -> list[Channel]:
    """Two channels per Hubble or Webb image: each Gaia star's brightness by forced photometry in one
    aperture at its counterpart, and its position from that counterpart: the detection nearest where
    Gaia puts it, within ``image_search_arcsec``.

    Each image is aligned to Gaia at its own date and described by the ``photometer`` (band, FWHM), as
    epoch-change does. The magnitude is instrumental, -2.5 log10 of the aperture flux corrected to a
    whole star; the field's colour relation absorbs the image's zero point and filter, as it does a
    survey band. A star is blended when an examined Gaia neighbour no more than ``blend_delta_mag``
    fainter puts light in its aperture (within the aperture radius plus one FWHM), or when its
    counterpart is ambiguous: a second detection less than one FWHM further away, or a detection
    another star also calls its nearest (two stars the detector did not split). Saturated or
    masked stars are set aside. A star with no counterpart is measured where Gaia puts it and has no
    position comparison: a missing star is image-counterpart's question.
    """
    photometer: ForcedPhotometer = context.service("photometer")
    anchors = {b.object_key: anchor(b, row, float(cfg["anchor_sigma_arcsec"])) for b, row in stars.rows}
    channels: list[Channel] = []
    for artifact_id, product_id in images:
        frame = prepare_frame(artifact_id, product_id, anchors, cfg, context, photometer, left_out)
        if frame is None:
            continue
        radius, annulus = aperture(frame.fwhm_arcsec, float(cfg["aperture_fwhm"]))
        reach = radius + frame.fwhm_arcsec
        whole = enclosed(radius, frame.fwhm_arcsec)
        found = _detections(frame, product_id, cfg)
        search = float(cfg["image_search_arcsec"])
        # each star's counterpart: the nearest detection, unless a second is about as near
        hits: dict[str, tuple[Detection, bool]] = {}
        placed = _points(anchors, frame.epoch_jyear)
        for point in placed:
            near = found.near(point, search) if found is not None else []
            if near:
                hits[point.key] = (near[0][1], len(near) > 1 and near[1][0] < near[0][0] + frame.fwhm_arcsec)
        # a detection two stars both call their nearest is the light of both: ambiguous for each
        claims: dict[str, int] = {}
        for hit, _ in hits.values():
            claims[hit.record_id] = claims.get(hit.record_id, 0) + 1
        hits = {k: (hit, ambiguous or claims[hit.record_id] > 1) for k, (hit, ambiguous) in hits.items()}
        # a star is measured at its own counterpart, so a moved star's brightness is still its own
        points = [
            SkyPoint(p.key, hits[p.key][0].ra_deg, hits[p.key][0].dec_deg)
            if p.key in hits and not hits[p.key][1]
            else p
            for p in placed
        ]
        measured = {
            m.key: m
            for m in photometer.measure(frame.frame, points, radius, annulus, _photometer_params(cfg)).measurements
        }
        instrument = frame.description.instrument or "unknown"
        bands, positions = _Builder(), _Builder()
        for i, (b, gaia) in enumerate(stars.rows):
            m = measured.get(b.object_key)
            if m is None or m.status == OFF_IMAGE:
                continue
            shared = b.object_key in hits and hits[b.object_key][1]
            blended = shared or stars.blended(i, reach, frame.epoch_jyear)
            bp_rp = float(gaia["bp_rp"]) if finite(gaia.get("bp_rp")) else math.nan
            gap = frame.epoch_jyear - float(gaia["epoch_jyear"])
            if not math.isnan(bp_rp):
                if m.status != MEASURED:
                    bands.add(i, stars.g[i], bp_rp, math.nan, 0.0, gap, SATURATED_ON_IMAGE)
                elif m.flux > 0 and m.flux_err > 0:
                    mag = -2.5 * math.log10(m.flux / whole)
                    sigma = 2.5 / math.log(10.0) * m.flux_err / m.flux
                    bands.add(i, stars.g[i], bp_rp, mag, sigma, gap, BLENDED if blended else None)
            if b.object_key in hits:
                hit, _ = hits[b.object_key]
                # the detector's centroid error is a rough guess, so the field sets the whole budget
                row = {"ra_deg": hit.ra_deg, "dec_deg": hit.dec_deg, "epoch_jyear": frame.epoch_jyear}
                _position(
                    positions,
                    i,
                    gaia,
                    row,
                    stars.g[i],
                    bp_rp,
                    blended,
                    m.status != MEASURED,
                    single_epoch=True,
                    other=None,
                )
        survey = f"image:{instrument}"
        if bands.index:
            channels.append(bands.build(f"image:{product_id}:{frame.band}", survey, BAND))
        if positions.index:
            channels.append(positions.build(f"image:{product_id}:{POSITION}", survey, POSITION))
    return channels


@dataclass(frozen=True)
class _Detections:
    """An aligned image's detections, searchable by sky position."""

    records: Sequence[Detection]
    tree: Any

    def near(self, point: SkyPoint, radius_arcsec: float) -> list[tuple[float, Detection]]:
        """The detections within ``radius_arcsec`` of ``point`` with their distance in arcsec, nearest first."""
        chord = 2.0 * math.sin(math.radians(radius_arcsec / 3600.0) / 2.0)
        here = _unit(np.array([point.ra_deg]), np.array([point.dec_deg]))[0]
        out = []
        for j in self.tree.query_ball_point(here, chord):
            there = _unit(np.array([self.records[j].ra_deg]), np.array([self.records[j].dec_deg]))[0]
            out.append((math.degrees(2.0 * math.asin(float(np.linalg.norm(there - here)) / 2.0)) * 3600.0, j))
        return [(d, self.records[j]) for d, j in sorted(out)]


def _detections(frame: Frame, product_id: str, cfg: Mapping[str, Any]) -> _Detections | None:
    if frame.detections is None or not frame.detections.detections:
        return None
    records, _, _, _ = detection_records(
        frame.detections,
        product_id,
        frame.epoch_jyear,
        frame.description.instrument or "unknown",
        float(cfg["fwhm_px"]),
        float(cfg["min_sigma_arcsec"]),
    )
    ra = np.array([r.ra_deg for r in records])
    dec = np.array([r.dec_deg for r in records])
    return _Detections(records, cKDTree(_unit(ra, dec)))


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


def _or_zero(value: Any) -> float:
    return float(value) if finite(value) else 0.0


def _separation_then(
    ra: np.ndarray, dec: np.ndarray, pm: np.ndarray, epochs: np.ndarray, i: int, j: int, when: float
) -> float:
    """Arcseconds between stars i and j at ``when``, each moved along its Gaia proper motion
    (a star without one stays put), in the tangent plane about star i."""
    cosd = math.cos(math.radians(float(dec[i])))
    dra = float(ra_offset_deg(np.array([ra[j]]), np.array([ra[i]]))[0]) * cosd * 3600.0
    ddec = float(dec[j] - dec[i]) * 3600.0
    moved = (pm[j] * (when - epochs[j]) - pm[i] * (when - epochs[i])) / 1000.0
    return math.hypot(dra + float(moved[0]), ddec + float(moved[1]))


def parallax_shift(ra_deg: float, dec_deg: float, epoch_jyear: float) -> tuple[float, float]:
    """The parallax displacement per mas of parallax, (RA*cos dec, Dec), seen from the Earth at
    ``epoch_jyear``. The Sun's geocentric position comes from the Astronomical Almanac's low-precision
    formulae (about 0.01 deg), ample for a shift of at most the parallax itself."""
    n = (epoch_jyear - 2000.0) * 365.25
    mean_longitude = math.radians((280.460 + 0.9856474 * n) % 360.0)
    anomaly = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = mean_longitude + math.radians(1.915 * math.sin(anomaly) + 0.020 * math.sin(2 * anomaly))
    r = 1.00014 - 0.01671 * math.cos(anomaly) - 0.00014 * math.cos(2 * anomaly)
    eps = math.radians(23.439 - 0.0000004 * n)
    # the Earth sits opposite the Sun: its position about the Sun, in AU, equatorial
    x, y, z = -r * math.cos(lam), -r * math.cos(eps) * math.sin(lam), -r * math.sin(eps) * math.sin(lam)
    a, d = math.radians(ra_deg), math.radians(dec_deg)
    # the star appears displaced away from the Earth's position, across the line of sight
    shift_ra = x * math.sin(a) - y * math.cos(a)
    shift_dec = x * math.cos(a) * math.sin(d) + y * math.sin(a) * math.sin(d) - z * math.cos(d)
    return shift_ra, shift_dec


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


def _error(row: Mapping[str, Any], canonical: str, archive: str | None, largest: float) -> float:
    """A reported 1-sigma error: the canonical column, else the archive's own in ``quality_flags``.

    Zero when the row has none, or when the value is no error at all (Pan-STARRS writes -999,
    and a zero or an implausibly large error says nothing): the field's calibration term then
    carries the whole budget.
    """
    for name in (canonical, archive):
        value = _number(row, name) if name else None
        if value is not None:
            return value if 0.0 < value < largest else 0.0
    return 0.0


def position_error(row: Mapping[str, Any], archive: tuple[str, str] | None) -> float:
    """The survey position's 1-sigma error per axis, in mas.

    ``pos_err_mas`` when the row has it; otherwise the root mean square of the archive's two
    errors (2MASS ellipse axes, Pan-STARRS RA and Dec), in arcsec. Zero when either is missing.
    """
    own = _error(row, "pos_err_mas", None, MAX_POSITION_ERROR_MAS)
    if own or archive is None:
        return own
    a, b = (_error(row, name, None, MAX_POSITION_ERROR_MAS / 1000.0) for name in archive)
    if not (a and b):
        return 0.0
    return 1000.0 * math.sqrt((a * a + b * b) / 2.0)


def _position(
    builder: _Builder,
    i: int,
    gaia: Mapping[str, Any],
    row: Mapping[str, Any],
    g: float,
    bp_rp: float,
    blended: bool,
    poor: bool = False,
    single_epoch: bool = False,
    survey_error: tuple[str, str] | None = None,
    other: str | None = None,
) -> None:
    """One position comparison; ``other`` is a reason to set it aside besides a blend or a poor row."""
    epoch = _number(row, "epoch_jyear")
    ra, dec = _number(row, "ra_deg"), _number(row, "dec_deg")
    motion = (gaia.get("pm_ra_cosdec_masyr"), gaia.get("pm_dec_masyr"), gaia.get("epoch_jyear"))
    if epoch is None or ra is None or dec is None or not finite(*motion):
        return
    dt = epoch - float(gaia["epoch_jyear"])
    cosd = math.cos(math.radians(float(gaia["dec_deg"])))
    pred_dec = float(gaia["dec_deg"]) + float(gaia["pm_dec_masyr"]) * dt / MAS_PER_DEG
    pred_ra = float(gaia["ra_deg"]) + float(gaia["pm_ra_cosdec_masyr"]) * dt / MAS_PER_DEG / cosd
    if single_epoch and finite(gaia.get("parallax_mas")):
        shift_ra, shift_dec = parallax_shift(float(gaia["ra_deg"]), float(gaia["dec_deg"]), epoch)
        pred_ra += float(gaia["parallax_mas"]) * shift_ra / MAS_PER_DEG / cosd
        pred_dec += float(gaia["parallax_mas"]) * shift_dec / MAS_PER_DEG
    dra = float(ra_offset_deg(np.array([ra]), np.array([pred_ra]))[0]) * cosd * MAS_PER_DEG
    ddec = (dec - pred_dec) * MAS_PER_DEG
    pm_err = math.hypot(
        float(gaia.get("pm_ra_cosdec_masyr_err") or 0.0), float(gaia.get("pm_dec_masyr_err") or 0.0)
    ) / math.sqrt(2.0)
    sigma = math.hypot(position_error(row, survey_error), pm_err * abs(dt))
    builder.add(i, g, bp_rp, (dra, ddec), sigma, dt, BLENDED if blended else POOR_QUALITY if poor else other)


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
    poor: bool = False,
) -> None:
    mag = _number(row, band.column, band.flag)
    if mag is None or not MAG_RANGE[0] < mag < MAG_RANGE[1]:
        return
    reason: str | None = BLENDED if blended else POOR_QUALITY if poor else None
    if reason is None and survey.quality_flag and band.quality_index is not None:
        # a band counts only with a good letter; a missing or short quality string is no grade
        quality = str(_flags(row).get(survey.quality_flag) or "")
        if len(quality) <= band.quality_index or quality[band.quality_index] not in survey.good_quality:
            reason = POOR_QUALITY
    if reason is None and not _clean_band(row, survey, band.quality_index):
        reason = CONTAMINATED
    epoch = _number(row, "epoch_jyear")
    gap = epoch - float(gaia["epoch_jyear"]) if epoch is not None and finite(gaia.get("epoch_jyear")) else math.nan
    builder.add(i, g, bp_rp, mag, _error(row, f"{band.column}_err", band.error, MAX_MAG_ERROR), gap, reason)


def _clean_band(row: Mapping[str, Any], survey: Survey, index: int | None) -> bool:
    """Whether the survey's contamination string clears this band: its letter is ``0``. A survey
    without one clears every band; a missing or short string clears none."""
    if survey.clean_flag is None or index is None:
        return True
    flags = str(_flags(row).get(survey.clean_flag) or "")
    return len(flags) > index and flags[index] == "0"


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
