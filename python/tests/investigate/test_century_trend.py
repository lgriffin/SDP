"""The century-trend test (INT-027): a star's brightness trend over the Harvard plates' century,
judged against its own field's stars rather than its quoted errors.

The synthetic field has what made DASCH trends near M67 untrustworthy on 2026-11-04: zero points and
gradients that differ on every plate, a colour response that changes across the 1953-1969 gap, slow
wanders of a few hundredths of a magnitude, and errors quoted smaller than the scatter."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pytest

from sdp.kernel.domain.investigation import significant
from sdp_investigate.base import Features, Scored
from sdp_investigate.century import (
    CenturyTrend,
    blend_fractions,
    exposure_groups,
    fit_trend,
    jyear,
    peer_model,
)

pytestmark = [pytest.mark.tier("T1"), pytest.mark.req("INT-027")]

RA0, DEC0 = 132.8, 11.8
FADE = 0.6  # mag per century, planted on star 0


def mjd_of(year: np.ndarray) -> np.ndarray:
    return 51544.5 + (np.asarray(year) - 2000.0) * 365.25


@dataclass
class Field:
    stars: list[tuple[str, float, float, float, float, float, int]]
    curves: list[tuple[np.ndarray, np.ndarray, np.ndarray]]
    listed: list[tuple[float, float, float]]

    def features(self, **thresholds: object) -> Features:
        return CenturyTrend().features_from_curves(self.stars, self.curves, self.listed, thresholds)


def plate_field(
    seed: int = 3, n: int = 160, exposures: int = 500, blended: bool = True, anchor_error: float = 0.03
) -> Field:
    """``n`` stars on a 9 x 18 grid six arcminutes apart, B 10 to 13.5, BP-RP 0.5 to 1.5. Star 0 fades
    by FADE a century; star 1 (when ``blended``) has a neighbour 30 arcsec away one magnitude fainter
    that the coarse post-gap plates merge into it, so it reads 0.3 mag brighter after 1969."""
    rng = np.random.default_rng(seed)
    grid = [(i % 9, i // 9) for i in range(n)]
    ra = np.array([RA0 + 0.1 * x / math.cos(math.radians(DEC0)) for x, _ in grid])
    dec = np.array([DEC0 + 0.1 * y for _, y in grid])
    true = rng.uniform(10.0, 13.5, n)
    colour = rng.uniform(0.5, 1.5, n)
    years = np.sort(
        np.r_[
            rng.uniform(1890.0, 1952.5, int(exposures * 0.7)),
            rng.uniform(1970.0, 1989.5, exposures - int(exposures * 0.7)),
        ]
    )
    zero = rng.normal(0.0, 0.15, len(years))
    gradient = rng.normal(0.0, 0.1, len(years))  # mag per degree east
    colour_term = np.where(years < 1960.0, 0.08, -0.08) + rng.normal(0.0, 0.02, len(years))
    wander = rng.normal(0.0, 0.02, (n, 11))  # per star and decade
    curves = []
    for i in range(n):
        seen = rng.random(len(years)) < 0.6
        y = years[seen]
        mag = (
            true[i]
            + zero[seen]
            + gradient[seen] * (ra[i] - RA0) * math.cos(math.radians(DEC0))
            + colour_term[seen] * (colour[i] - 0.9)
            + wander[i, np.clip(((y - 1890.0) // 10).astype(int), 0, 10)]
            + rng.normal(0.0, 0.10, len(y))
        )
        if i == 0:
            mag = mag + FADE * (y - 1950.0) / 100.0
        if i == 1 and blended:
            mag = mag - np.where(y > 1969.0, 0.3, 0.0)
        # DASCH stamps each star's own time on a plate: a few seconds apart
        curves.append((mjd_of(y) + rng.uniform(0.0, 0.0002, len(y)), mag, np.full(len(y), 0.07)))
    anchor = true + rng.normal(0.0, anchor_error, n) + np.where(np.arange(n) == 0, FADE * 0.66, 0.0)
    stars = [
        (f"s{i}", float(ra[i]), float(dec[i]), float(true[i] - 0.5), float(colour[i]), float(anchor[i]), i)
        for i in range(n)
    ]
    listed = [(float(ra[i]), float(dec[i]), float(true[i] - 0.5)) for i in range(n)]
    if blended:
        listed.append(
            (float(ra[1] + 30.0 / 3600.0 / math.cos(math.radians(DEC0))), float(dec[1]), float(true[1] + 0.5))
        )
    return Field(stars, curves, listed)


@pytest.fixture(scope="module")
def field() -> Field:
    return plate_field()


@pytest.fixture(scope="module")
def scored(field: Field) -> dict[str, Scored]:
    return {s.subject.key: s for s in CenturyTrend().evaluate(field.features(), {}, np.random.default_rng(0))}


def pv(score: Scored) -> float:
    assert score.p_value is not None
    return score.p_value


def test_quoted_errors_make_constant_stars_trend(field: Field) -> None:
    """The problem the test exists for: a straight line through a constant star's raw plate
    magnitudes, with its quoted errors, often 'trends'."""
    flagged = 0
    for t, mag, err in field.curves[2:]:
        trend = fit_trend(jyear(t), mag, err, clip=4.0, bin_years=0.0)
        assert trend is not None
        flagged += abs(trend.slope / trend.slope_err) > 2.576
    assert flagged / (len(field.curves) - 2) > 0.2


def test_the_planted_fade_stands_out_and_the_constant_stars_do_not(scored: dict[str, Scored]) -> None:
    fade = scored["s0"]
    assert pv(fade) < 1e-6 and fade.statistic > 0
    assert fade.effect_size == pytest.approx(FADE, abs=0.12)
    assert fade.details["slope_mag_per_century"] == pytest.approx(FADE, abs=0.15)
    constant = [s for k, s in scored.items() if k not in ("s0", "s1")]
    assert len(constant) >= 150
    assert sum(significant(s.p_value, 0.01) for s in constant) <= 4
    sigma_sys = fade.details["sigma_sys"]
    assert 0.0 <= sigma_sys < 0.1 and {s.details["sigma_sys"] for s in scored.values()} == {sigma_sys}


def test_a_blended_star_is_scored_but_never_calibrates(scored: dict[str, Scored]) -> None:
    blend = scored["s1"]
    assert blend.details["blend_fraction"] == pytest.approx(10 ** (-0.4), rel=1e-6)
    assert blend.details["reference"] is False
    assert scored["s2"].details["reference"] is True
    assert blend.details["reference_stars"] == len(scored) - 1
    assert pv(blend) < 0.01 and blend.statistic < 0, "the merged neighbour reads as brightening"


def test_the_gaia_anchor_contradicts_a_step_and_agrees_with_a_real_fade(scored: dict[str, Scored]) -> None:
    """Star 1's post-gap plates are 0.3 mag too bright, which tilts its line to a 2016 magnitude
    brighter than its modern one: Gaia says no. Star 0 faded for real, so its line reaches 2016 at
    the magnitude Gaia measures."""
    assert scored["s1"].details["anchor_z"] < -4
    assert -0.6 < scored["s1"].details["anchor_offset_mag"] < -0.2
    assert abs(scored["s0"].details["anchor_z"]) < 3
    assert abs(scored["s0"].details["anchor_offset_mag"]) < 0.1
    typical = np.median([abs(s.details["anchor_z"]) for s in scored.values()])
    assert 0.4 < typical < 1.0


def test_without_an_anchor_magnitude_no_anchor_is_reported(field: Field) -> None:
    stars = [(*s[:5], math.nan, s[6]) for s in field.stars]
    out = CenturyTrend().evaluate(Field(stars, field.curves, field.listed).features(), {}, np.random.default_rng(0))
    assert out and all("anchor_z" not in s.details and "anchor_offset_mag" not in s.details for s in out)


def test_the_null_scores_held_out_unblended_stars_near_the_nominal_rate(field: Field) -> None:
    test = CenturyTrend()
    features = field.features()
    rng = np.random.default_rng(7)
    examined = positives = 0
    for _ in range(20):
        null = test.null_features(features, {}, rng)
        assert "s1" not in {s.subject.key for s in test.evaluate(null, {}, rng)}
        out = test.evaluate(null, {}, rng)
        calibrated = null.arrays["calibrate"]
        assert all(not calibrated[features.keys.index(s.subject.key)] for s in out)
        examined += len(out)
        positives += sum(significant(s.p_value, 0.01) for s in out if s.subject.key != "s0")
    assert examined > 20 * 60
    assert positives / examined < 0.03


def test_stars_without_plates_on_both_sides_of_the_gap_are_left_out(field: Field) -> None:
    curves = list(field.curves)
    t, mag, err = curves[5]
    before = jyear(t) < 1953.0
    curves[5] = (t[before], mag[before], err[before])
    out = CenturyTrend().evaluate(Field(field.stars, curves, field.listed).features(), {}, np.random.default_rng(0))
    assert "s5" not in {s.subject.key for s in out} and len(out) == len(field.stars) - 1
    fewer = CenturyTrend().evaluate(field.features(min_points=10_000), {}, np.random.default_rng(0))
    assert fewer == []


def test_too_few_reference_stars_score_nothing(field: Field) -> None:
    small = Field(field.stars[:30], field.curves[:30], field.listed[:30])
    assert CenturyTrend().evaluate(small.features(), {"min_reference": 31}, np.random.default_rng(0)) == []
    assert CenturyTrend().evaluate(small.features(), {"min_reference": 25}, np.random.default_rng(0))
    assert CenturyTrend().evaluate(Field([], [], []).features(), {}, np.random.default_rng(0)) == []


def test_epochs_seconds_apart_are_one_exposure() -> None:
    mjd = np.array([100.0003, 100.0, 200.0, 100.0001, 100.01, 200.0004])
    groups = exposure_groups(mjd, 0.0005)
    assert groups[0] == groups[1] == groups[3]
    assert groups[2] == groups[5] and len({groups[0], groups[2], groups[4]}) == 3
    assert list(exposure_groups(np.array([3.0, 1.0, 2.0]), 0.5)) == [2, 0, 1]


def test_blend_fraction_is_the_neighbours_flux_over_the_stars() -> None:
    ra = np.array([10.0, 10.0, 10.0])
    dec = np.array([0.0, 30.0 / 3600.0, 200.0 / 3600.0])
    g = np.array([12.0, 13.0, 12.0])
    out = blend_fractions(ra, dec, g, 60.0)
    assert out == pytest.approx([10 ** (-0.4), 10 ** (0.4), 0.0])


def test_the_trend_fit_reports_slope_per_century_and_red_noise() -> None:
    rng = np.random.default_rng(0)
    year = np.sort(rng.uniform(1890.0, 1990.0, 400))
    mag = 11.0 + 0.2 * (year - 1950.0) / 100.0 + rng.normal(0.0, 0.05, 400)
    white = fit_trend(year, mag, np.full(400, 0.05), clip=4.0, bin_years=5.0)
    assert white is not None
    assert white.slope == pytest.approx(0.2, abs=0.03) and white.intercept == pytest.approx(11.0, abs=0.01)
    assert white.reduced_chi2 == pytest.approx(1.0, abs=0.2) and white.points >= 395
    wandering = mag + 0.05 * np.sin(2 * np.pi * year / 40.0)
    red = fit_trend(year, wandering, np.full(400, 0.05), clip=4.0, bin_years=5.0)
    flat = fit_trend(year, wandering, np.full(400, 0.05), clip=4.0, bin_years=0.0)
    assert red is not None and flat is not None and red.slope_err > flat.slope_err
    assert fit_trend(np.full(5, 1950.0), np.ones(5), np.ones(5), 4.0, 5.0) is None


def test_peers_absorb_a_colour_dependent_slope() -> None:
    rng = np.random.default_rng(1)
    colour = rng.uniform(0.5, 1.5, 300)
    mag = rng.uniform(10.0, 13.5, 300)
    err = np.full(300, 0.03)
    slope = -0.1 * (colour - 0.9) + rng.normal(0.0, math.hypot(0.03, 0.04), 300)
    coef, sigma_sys = peer_model(slope, err, colour, mag)
    assert coef[1] == pytest.approx(-0.1, abs=0.02)
    assert sigma_sys == pytest.approx(0.04, abs=0.012)
    _, none = peer_model(np.zeros(50), np.full(50, 0.1), colour[:50], mag[:50])
    assert none == 0.0
