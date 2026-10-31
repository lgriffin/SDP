"""T4: cross-survey-contradiction finds the stars whose 2MASS or Pan-STARRS measurements disagree with
Gaia beyond the field-calibrated budget, and sets aside the comparisons it cannot trust (INV-026).

Every 2MASS and PS1 row comes out of the packaged dialects, as in production, so the test reads the
columns the archives really curate. The field is synthetic: a magnitude-dependent scatter, a colour
transformation the test has to learn, a frame offset between 2MASS and Gaia, and planted stars.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from scipy import stats

from sdp.adapters.archives.dialect import load_dialects
from sdp.kernel.domain.errors import PlanInvalid
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.investigation import EvidenceBundle, IdentificationMethod, IdentifiedObject
from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.science.plan import Backend, TaskContext
from sdp_investigate import registered_tests
from sdp_investigate.contradiction import (
    BLENDED,
    POOR_QUALITY,
    TOO_FEW,
    Channel,
    CrossSurveyContradiction,
    calibrate,
    equivalent_z,
    settings,
    solve_calibration,
)

pytestmark = [pytest.mark.tier("T4"), pytest.mark.req("INV-026")]

DIALECTS = load_dialects()
TWOMASS, PS1 = DIALECTS["2mass-psc"], DIALECTS["ps1-dr2"]
GAIA_ART, IR_ART, PS_ART = (ArtifactId("sha256:" + c * 64) for c in "abc")
THRESHOLDS: dict[str, Any] = {"source_id": "gaia", "surveys": {"2mass": "2mass", "ps1": "ps1"}}
GAIA_EPOCH, TWOMASS_JD, PS1_MJD = 2016.0, 2451180.5, 56000.0  # 2MASS 1999.0, PS1 2012.2
TWOMASS_EPOCH = 1999.0 + (TWOMASS_JD - 2451179.5) / 365.25
PS1_EPOCH = 2000.0 + (PS1_MJD + 2400000.5 - 2451545.0) / 365.25
FRAME_OFFSET_MAS = (40.0, -25.0)

MOVED = 3  # 2MASS saw it 700 mas from where Gaia's motion puts it
KS_EXCESS = 5  # 0.5 mag brighter in Ks than its colour predicts
FADED = 7  # 0.4 mag fainter in every 2MASS and PS1 band: a variable or a wrong match
PAIR = (10, 11)  # 1.2 arcsec apart, equal brightness: 2MASS cannot split them
POOR = 13  # Ks flagged U (upper limit) by 2MASS and far off
PLANTED = {MOVED, KS_EXCESS, FADED, *PAIR, POOR}


def predicted(band: str, g: float, bp_rp: float) -> float:
    """The synthetic world's G - band relations: the test never sees them, it fits them."""
    terms = {
        "j": (0.15, 0.95, 0.10),
        "h": (0.20, 1.30, 0.12),
        "k": (0.25, 1.40, 0.15),
        "g": (-0.05, -0.30, -0.10),
        "r": (0.05, 0.25, 0.02),
        "i": (0.10, 0.55, 0.05),
    }[band]
    return g - (terms[0] + terms[1] * bp_rp + terms[2] * bp_rp**2)


def sigma_mag(g: float) -> float:
    return 0.015 + 0.012 * (g - 11.0)


def field(n: int = 120, seed: int = 26) -> list[EvidenceBundle]:
    rng = np.random.default_rng(seed)
    bundles: list[EvidenceBundle] = []
    ra0, dec0 = 150.0, 20.0
    cosd = math.cos(math.radians(dec0))
    for i in range(n):
        g = 12.5 if i in (MOVED, KS_EXCESS, FADED) else float(rng.uniform(11.0, 17.0))
        bp_rp = float(rng.uniform(0.4, 2.6))
        ra = ra0 + float(rng.uniform(-0.2, 0.2)) / cosd
        dec = dec0 + float(rng.uniform(-0.2, 0.2))
        if i == PAIR[1]:  # 1.2 arcsec east of its twin, as bright
            twin = bundles[-1]
            ra, dec = twin.object.ra_deg + 1.2 / 3600.0 / cosd, twin.object.dec_deg
            g = float(twin.rows("gaia")[0]["g_mag"])
        pmra, pmdec = float(rng.normal(-3, 8)), float(rng.normal(2, 8))
        key = f"gaia:DR3:{i}"
        gaia = {
            "source_key": key,
            "ra_deg": ra,
            "dec_deg": dec,
            "epoch_jyear": GAIA_EPOCH,
            "pm_ra_cosdec_masyr": pmra,
            "pm_dec_masyr": pmdec,
            "pm_ra_cosdec_masyr_err": 0.04,
            "pm_dec_masyr_err": 0.04,
            "g_mag": g,
            "bp_rp": bp_rp,
        }
        s = sigma_mag(g)
        noise = {b: float(rng.normal(0, s)) for b in "jhkgri"}
        mags = {b: predicted(b, g, bp_rp) + noise[b] for b in "jhkgri"}
        if i == KS_EXCESS:
            mags["k"] -= 0.5
        if i == FADED:
            mags = {b: m + 0.4 for b, m in mags.items()}
        if i == PAIR[0]:
            mags["k"] -= 0.6  # the pair's blended light, never a contradiction
        if i == POOR:
            mags["k"] -= 1.0
        rows: dict[str, tuple[dict[str, Any], ...]] = {"gaia": (gaia,)}
        arts = {"gaia": GAIA_ART}
        dt = TWOMASS_EPOCH - GAIA_EPOCH
        offset = (700.0, 0.0) if i == MOVED else (0.0, 0.0)
        e_ra = pmra * dt + FRAME_OFFSET_MAS[0] + offset[0] + float(rng.normal(0, 80 + 20 * (g - 11)))
        e_dec = pmdec * dt + FRAME_OFFSET_MAS[1] + offset[1] + float(rng.normal(0, 80 + 20 * (g - 11)))
        native = {
            "designation": f"{i:08d}+0000000",
            "ra": (ra + e_ra / 3.6e6 / cosd) % 360.0,
            "dec": dec + e_dec / 3.6e6,
            "jdate": TWOMASS_JD,
            "j_m": mags["j"],
            "h_m": mags["h"],
            "k_m": mags["k"],
            "ph_qual": "AAU" if i == POOR else "AAA",
        }
        rows["2mass"] = (TWOMASS.row(native),)
        arts["2mass"] = IR_ART
        if i % 4 != 0:  # PS1 covers three stars in four
            dt = PS1_EPOCH - GAIA_EPOCH
            ps = {
                "objID": 10_000 + i,
                "raMean": (ra + (pmra * dt + float(rng.normal(0, 15))) / 3.6e6 / cosd) % 360.0,
                "decMean": dec + (pmdec * dt + float(rng.normal(0, 15))) / 3.6e6,
                "epochMean": PS1_MJD,
                "gMeanPSFMag": mags["g"],
                "rMeanPSFMag": -999.0 if i == 1 else mags["r"],  # PS1's sentinel for no measurement
                "iMeanPSFMag": mags["i"],
                "nDetections": 40,
            }
            rows["ps1"] = (PS1.row(ps),)
            arts["ps1"] = PS_ART
        obj = IdentifiedObject(key, IdentificationMethod.CATALOGUE, ra, dec, (GAIA_ART,), gaia_source_key=key)
        bundles.append(EvidenceBundle(object=obj, catalogue_rows=rows, catalogue_artifacts=arts))
    return bundles


def context(seed: int = 5) -> TaskContext:
    return TaskContext("t", seed, NumericalPolicy(rtol=1e-9), Backend.CPU)


@pytest.fixture
def results() -> dict[str, Any]:
    test = CrossSurveyContradiction()
    return {str(r.subject.object_key): r for r in test.run(field(), THRESHOLDS, context())}


def comparisons(result: Any) -> dict[str, dict[str, Any]]:
    return {c["channel"]: c for c in result.details["comparisons"]}


def test_it_is_registered_with_a_question_and_a_null_model() -> None:
    names = {t.name for t in registered_tests()}
    assert "cross-survey-contradiction" in names
    test = CrossSurveyContradiction()
    assert test.question and test.null_model and test.version == "1.0.0"


@pytest.mark.scientific
def test_the_planted_contradictions_are_found_and_labelled(results: dict[str, Any]) -> None:
    moved, excess, faded = (results[f"gaia:DR3:{i}"] for i in (MOVED, KS_EXCESS, FADED))
    assert moved.p_value < 1e-6 and moved.details["worst_channel"] == "2mass:position"
    assert moved.details["pattern"] == "position"
    assert 600 < moved.effect_size < 800
    assert excess.p_value < 1e-6 and excess.details["worst_channel"] == "2mass:Ks"
    assert excess.details["pattern"] == "one-band"
    assert excess.effect_size == pytest.approx(-0.5, abs=0.12)
    assert faded.p_value < 1e-6 and faded.details["pattern"] == "all-bands-one-way"
    assert len(faded.details["flagged"]) >= 4


@pytest.mark.scientific
def test_the_field_is_not_flagged_once_its_budget_is_calibrated(results: dict[str, Any]) -> None:
    field_p = [r.p_value for k, r in results.items() if int(k.rsplit(":", 1)[1]) not in PLANTED]
    assert len(field_p) > 100
    assert sum(p <= 0.01 for p in field_p) <= 3
    assert min(field_p) > 1e-5


def test_the_frame_offset_is_removed_not_flagged(results: dict[str, Any]) -> None:
    position = comparisons(results["gaia:DR3:0"])["2mass:position"]
    assert position["field_offset_mas"] == pytest.approx(list(FRAME_OFFSET_MAS), abs=25.0)


def test_a_blend_is_set_aside_with_its_reason_and_never_counted(results: dict[str, Any]) -> None:
    for i in PAIR:
        result = results[f"gaia:DR3:{i}"]
        twomass = [c for c in result.details["comparisons"] if c["survey"] == "2mass"]
        assert twomass and all(c["set_aside"] == BLENDED and not c["counted"] for c in twomass)
        assert "2mass:Ks" not in result.details["flagged"]
    # the pair has no PS1 row it can trust either (PS1 resolves 1.5 arcsec), so nothing is judged
    alone = results[f"gaia:DR3:{PAIR[0]}"]
    assert alone.p_value is None and alone.details["pattern"] == "not-assessable"


def test_a_band_the_survey_flags_as_poor_is_set_aside(results: dict[str, Any]) -> None:
    result = results[f"gaia:DR3:{POOR}"]
    by = comparisons(result)
    assert by["2mass:Ks"]["set_aside"] == POOR_QUALITY and not by["2mass:Ks"]["counted"]
    assert by["2mass:J"]["counted"]
    assert result.p_value > 1e-4


def test_a_sentinel_magnitude_is_no_comparison(results: dict[str, Any]) -> None:
    assert "ps1:r" not in comparisons(results["gaia:DR3:1"])
    assert "ps1:g" in comparisons(results["gaia:DR3:1"])


def test_every_comparison_writes_out_its_budget(results: dict[str, Any]) -> None:
    for c in results["gaia:DR3:2"].details["comparisons"]:
        assert c["counted"]
        assert c["total_sigma"] == pytest.approx(math.hypot(c["reported_sigma"], c["calibration_sigma"]))
        assert c["epoch_gap_years"] < 0
    band = comparisons(results["gaia:DR3:2"])["2mass:J"]
    assert band["unit"] == "mag"
    assert band["residual_mag"] == pytest.approx(
        band["observed_mag"] - band["predicted_mag"] - band["field_offset_mag"]
    )


def test_the_budget_grows_for_faint_stars(results: dict[str, Any]) -> None:
    calib = sorted(
        (r.details and comparisons(r)["2mass:J"]["calibration_sigma"], k)
        for k, r in results.items()
        if comparisons(r).get("2mass:J", {}).get("counted")
    )
    assert calib[0][0] < calib[-1][0] / 1.5


def test_a_channel_with_too_few_stars_is_set_aside() -> None:
    test = CrossSurveyContradiction()
    small = field(n=12)
    results = test.run(small, THRESHOLDS, context())
    assert results and all(r.p_value is None for r in results)
    features = test.features(small, THRESHOLDS, None)  # type: ignore[arg-type]
    cal = calibrate(features.arrays["channels"][0], settings(THRESHOLDS))
    assert set(cal.reasons) <= {TOO_FEW, BLENDED, POOR_QUALITY} and not cal.counted.any()


def test_more_comparisons_need_a_larger_disagreement() -> None:
    test = CrossSurveyContradiction()
    rng = np.random.default_rng(0)
    one = test.evaluate(_features(1, 4.0), THRESHOLDS, rng)
    many = test.evaluate(_features(6, 4.0), THRESHOLDS, rng)
    p_one = next(float(s.p_value or 0.0) for s in one if s.subject.object_key == "k0")
    p_many = next(float(s.p_value or 0.0) for s in many if s.subject.object_key == "k0")
    # a 4-sigma offset in one axis of two, its budget set by 40 stars: F(2, 78) rather than exp(-8)
    assert p_one == pytest.approx(stats.f.sf(8.0, 2, 78), rel=0.3)
    assert p_many > 5 * p_one


def _features(channels: int, z0: float) -> Any:
    from sdp_investigate.base import Features

    n = 40
    rng = np.random.default_rng(1)
    built = []
    for c in range(channels):
        observed = rng.normal(0.0, 1.0, (n, 2))
        observed -= np.median(observed, axis=0)
        observed[0] = (0.0, 0.0)
        if c == 0:
            observed[0] = (z0 * 1.0, 0.0)
        built.append(
            Channel(
                f"s:{c}",
                "2mass",
                "position",
                np.arange(n),
                np.full(n, 14.0),
                np.full(n, 1.0),
                observed,
                np.full(n, 1.0),
                np.zeros(n),
                [None] * n,
            )
        )
    return Features(tuple(f"k{i}" for i in range(n)), {"channels": built})


def test_solve_calibration_recovers_the_missing_scatter() -> None:
    rng = np.random.default_rng(3)
    r = rng.normal(0.0, 0.1, 20000)
    assert solve_calibration(r**2, np.zeros_like(r), 1) == pytest.approx(0.1, rel=0.03)
    assert solve_calibration(r**2, np.full_like(r, 0.1**2), 1) == pytest.approx(0.0, abs=0.03)
    assert solve_calibration(r**2, np.full_like(r, 0.06**2), 1) == pytest.approx(0.08, rel=0.06)
    xy = rng.normal(0.0, 0.2, (20000, 2))
    assert solve_calibration(np.sum(xy**2, axis=1), np.zeros(20000), 2) == pytest.approx(0.2, rel=0.03)


def test_a_two_dimensional_distance_maps_to_the_z_of_the_same_p_value() -> None:
    d2 = np.array([4.0, 16.0])
    z = equivalent_z(d2, 2)
    p_chi2 = np.exp(-d2 / 2)
    assert 2 * stats.norm.sf(z) == pytest.approx(p_chi2, rel=1e-6)
    assert equivalent_z(np.array([9.0]), 1)[0] == pytest.approx(3.0)


def test_a_budget_estimated_from_few_stars_asks_for_a_larger_disagreement() -> None:
    d2 = np.array([16.0])
    assert equivalent_z(d2, 1, np.array([20]))[0] < equivalent_z(d2, 1, np.array([2000]))[0] < 4.0001
    assert equivalent_z(d2, 2, np.array([20]))[0] < equivalent_z(d2, 2)[0]
    assert equivalent_z(d2, 1, np.array([20]))[0] == pytest.approx(stats.norm.isf(stats.t.sf(4.0, 20)), rel=1e-9)


@pytest.mark.scientific
def test_null_fields_stay_near_the_nominal_false_positive_rate() -> None:
    null = CrossSurveyContradiction().null(field(), THRESHOLDS, context(9), trials=20, alpha=0.01)
    assert null.examined > 2000
    assert null.false_positives / null.examined < 0.025


def test_unknown_surveys_and_bad_bins_are_refused() -> None:
    with pytest.raises(PlanInvalid, match="wise"):
        settings({"surveys": {"wise": "allwise"}})
    with pytest.raises(PlanInvalid, match="min_per_bin"):
        settings({"min_per_bin": 2})


def test_a_survey_missing_from_the_field_simply_adds_no_channel() -> None:
    test = CrossSurveyContradiction()
    only_2mass = {**THRESHOLDS, "surveys": {"2mass": "2mass"}}
    results = test.run(field(), only_2mass, context())
    assert all(c["survey"] == "2mass" for r in results for c in r.details["comparisons"])


def test_settings_say_what_they_refuse_and_accept_the_smallest_valid_values() -> None:
    with pytest.raises(PlanInvalid, match=r"names its surveys as \{survey: source_id\}"):
        settings({"surveys": {}})
    with pytest.raises(PlanInvalid, match="names its surveys"):
        settings({"surveys": ["2mass"]})
    with pytest.raises(PlanInvalid, match="needs min_per_bin >= 5 and magnitude_bins >= 1"):
        settings({"magnitude_bins": 0})
    assert settings({"min_per_bin": 5, "magnitude_bins": 1})["min_per_bin"] == 5


def test_magnitude_bins_split_at_the_counted_quantiles() -> None:
    from sdp_investigate.contradiction import magnitude_bins

    g = np.arange(30.0)
    counted = np.ones(30, dtype=bool)
    assert magnitude_bins(g, counted, 3, 10).tolist() == [0] * 10 + [1] * 10 + [2] * 10
    assert magnitude_bins(g, counted, 3, 15).tolist() == [0] * 15 + [1] * 15
    assert magnitude_bins(g, counted, 3, 20).tolist() == [0] * 30
    assert magnitude_bins(g, counted, 1, 5).dtype.kind == "i"
    # the edges come from counted stars only, and a star on an edge goes to the fainter bin
    half = np.arange(30) < 20
    assert magnitude_bins(g, half, 2, 10).tolist() == [0] * 10 + [1] * 20


def test_a_position_comparison_moves_gaia_back_and_grows_its_motion_error() -> None:
    from sdp_investigate.contradiction import _Builder, _position

    dec = 60.0
    gaia = {
        "ra_deg": 10.0,
        "dec_deg": dec,
        "epoch_jyear": 2016.0,
        "pm_ra_cosdec_masyr": 100.0,
        "pm_dec_masyr": -50.0,
        "pm_ra_cosdec_masyr_err": 0.3,
        "pm_dec_masyr_err": 0.4,
    }
    cosd = math.cos(math.radians(dec))
    # the survey saw it in 2000, 200 mas east and 100 mas north of where Gaia's motion puts it
    ra = 10.0 + (100.0 * -16.0 + 200.0) / 3.6e6 / cosd
    row = {"ra_deg": ra, "dec_deg": dec + (-50.0 * -16.0 + 100.0) / 3.6e6, "epoch_jyear": 2000.0, "pos_err_mas": 70.0}
    b = _Builder()
    _position(b, 4, gaia, row, 13.0, 1.1, False)
    channel = b.build("2mass:position", "2mass", "position")
    assert channel.observed[0] == pytest.approx([200.0, 100.0], abs=1e-6)
    assert channel.sigma[0] == pytest.approx(math.hypot(70.0, 0.5 / math.sqrt(2) * 16.0))
    assert channel.epoch_gap[0] == -16.0 and channel.bp_rp[0] == 1.1 and channel.index.tolist() == [4]
    assert channel.reasons == [None]
    _position(b, 5, gaia, {**row, "pos_err_mas": None}, 13.0, 1.1, True)
    assert b.sigma[-1] == pytest.approx(0.5 / math.sqrt(2) * 16.0) and b.reasons[-1] == BLENDED
    for missing in ("ra_deg", "dec_deg", "epoch_jyear"):
        _position(b, 6, gaia, {**row, missing: None}, 13.0, 1.1, False)
    _position(b, 6, {**gaia, "pm_dec_masyr": None}, row, 13.0, 1.1, False)
    assert len(b.index) == 2


def test_a_band_reads_its_error_its_quality_letter_and_refuses_sentinels() -> None:
    from sdp_investigate.contradiction import SURVEYS, _band, _Builder

    twomass = SURVEYS["2mass"]
    ks = twomass.bands[2]
    gaia = {"epoch_jyear": 2016.0}
    b = _Builder()
    row = {"k_mag": 11.0, "k_mag_err": 0.03, "epoch_jyear": 1999.0, "quality_flags": '{"ph_qual": "AAB"}'}
    _band(b, 0, ks, twomass, row, 13.0, 1.0, gaia, False)
    _band(b, 1, ks, twomass, {**row, "quality_flags": '{"ph_qual": "AAC"}'}, 13.0, 1.0, gaia, False)
    _band(b, 2, ks, twomass, {**row, "quality_flags": None}, 13.0, 1.0, gaia, False)
    _band(b, 3, ks, twomass, {**row, "k_mag": 30.0}, 13.0, 1.0, gaia, False)
    _band(b, 4, ks, twomass, {**row, "k_mag": -2.0}, 13.0, 1.0, gaia, False)
    ps1 = SURVEYS["ps1"]
    _band(b, 5, ps1.bands[0], ps1, {"quality_flags": '{"gMeanPSFMag": 14.5}'}, 14.0, 1.0, gaia, False)
    assert b.index == [0, 1, 2, 5]
    assert b.sigma[:3] == [0.03, 0.03, 0.03] and b.sigma[3] == 0.0
    assert b.reasons == [None, POOR_QUALITY, None, None]
    assert b.gap[0] == -17.0 and math.isnan(b.gap[3])
    assert b.observed[3] == 14.5


def test_a_patterns_label_follows_the_signs_and_kinds_of_what_disagrees() -> None:
    from sdp_investigate.contradiction import pattern

    pos = _features(1, 0.0).arrays["channels"][0]
    band = Channel(
        "2mass:J",
        "2mass",
        "band",
        *(pos.index, pos.g_mag, pos.bp_rp, pos.g_mag),
        *(pos.sigma, pos.epoch_gap, pos.reasons),
    )
    assert pattern([]) == "consistent"
    assert pattern([(pos, 4.0)]) == "position"
    assert pattern([(pos, 4.0), (band, -4.0)]) == "position-and-brightness"
    assert pattern([(band, 0.5)]) == "one-band"
    assert pattern([(band, 4.0), (band, 3.5)]) == "all-bands-one-way"
    assert pattern([(band, -4.0), (band, -0.5)]) == "all-bands-one-way"
    assert pattern([(band, 4.0), (band, -3.5)]) == "colour"
    assert pattern([(band, 0.5), (band, -0.5)]) == "colour"


def test_a_brighter_star_has_a_negative_z_and_huge_disagreements_stay_finite() -> None:
    from sdp_investigate.contradiction import _signed_z

    results = {str(r.subject.object_key): r for r in CrossSurveyContradiction().run(field(), THRESHOLDS, context())}
    ks = comparisons(results[f"gaia:DR3:{KS_EXCESS}"])["2mass:Ks"]
    assert ks["z"] < -5 and ks["residual_mag"] < 0
    faded = comparisons(results[f"gaia:DR3:{FADED}"])["2mass:J"]
    assert faded["z"] > 5
    assert np.isfinite(equivalent_z(np.array([5000.0]), 2)).all()
    assert np.isfinite(equivalent_z(np.array([5000.0]), 1, np.array([30]))).all()
    assert equivalent_z(np.array([0.0]), 2)[0] == 0.0
    assert callable(_signed_z)


def test_a_position_budget_from_few_stars_uses_f_with_twice_their_number() -> None:
    from sdp_investigate.contradiction import p_value

    assert p_value(np.array([16.0]), 2, np.array([20]))[0] == pytest.approx(stats.f.sf(8.0, 2, 40), rel=1e-12)
    assert p_value(np.array([16.0]), 2)[0] == pytest.approx(math.exp(-8.0), rel=1e-12)


def test_the_unit_vectors_are_unit_and_point_the_right_way() -> None:
    from sdp_investigate.contradiction import _unit

    v = _unit(np.array([0.0, 90.0, 200.0]), np.array([0.0, 0.0, 45.0]))
    assert np.linalg.norm(v, axis=1) == pytest.approx([1.0, 1.0, 1.0])
    assert v[0] == pytest.approx([1.0, 0.0, 0.0]) and v[1] == pytest.approx([0.0, 1.0, 0.0], abs=1e-12)
    c = math.cos(math.radians(45.0))
    assert v[2] == pytest.approx([c * math.cos(math.radians(200)), c * math.sin(math.radians(200)), c])
