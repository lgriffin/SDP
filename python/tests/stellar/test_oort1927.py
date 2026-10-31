"""UC-04: Oort's 1927 rotation constants, his arithmetic and the fit on synthetic galaxies (STL-014 to STL-018)."""

from __future__ import annotations

import copy
import math
from typing import Any

import numpy as np
import pytest

from sdp_stellar import oort1927 as o

pytestmark = pytest.mark.tier("T1")


def small_choices() -> dict[str, Any]:
    """The committed choices on a two-scenario grid: the baseline and one other shell."""
    choices = copy.deepcopy(o.load_choices())
    choices["scenarios"] = {
        "shell_kpc": [[0.3, 1.0], [0.5, 1.5]],
        "b_max_deg": [30.0],
        "g_max": [15.0],
        "parallax_zero_point_mas": [-0.017],
        "population": ["all"],
    }
    return choices


@pytest.fixture(scope="module")
def flat() -> dict[str, Any]:
    return o.synthetic_galaxy(n=200_000, slope=0.0, seed=11)


# --- Part A ---------------------------------------------------------------------------------------


@pytest.mark.req("STL-014")
def test_oorts_arithmetic_is_recomputed_beside_his_printed_figures() -> None:
    a = o.arithmetic()
    mean = a["weighted_mean_A_km_s_pc"]
    assert mean["printed"] == 0.0310 and mean["printed_error"] == 0.0037
    assert mean["computed"] == pytest.approx(0.03064, abs=5e-5)  # 0.0004 below the printed total
    assert mean["computed_error"] == pytest.approx(0.00362, abs=5e-5)
    for row in a["rA_times_parallax"]:
        assert round(row["computed_A"], 3) == row["printed_A"]
    radii = a["radius_from_A"]
    assert radii["printed"]["R_pc"] == pytest.approx(5929, abs=1)
    assert radii["after_note"]["R_pc"] == pytest.approx(5101, abs=1)
    assert radii["k2_zero"]["R_pc"] == pytest.approx(6581, abs=1)
    for v in radii.values():  # each within Oort's rounding to 100 pc
        assert abs(v["R_pc"] - v["R_pc_printed"]) < 50
    assert a["central_mass_solar"]["computed"] == pytest.approx(9.14e10, rel=0.01)
    assert a["central_mass_solar"]["computed"] > a["central_mass_solar"]["printed_at_least"]
    assert a["keplerian_semi_amplitude_km_s"]["computed"] == 22.5


def test_kappa_and_g_come_from_astropy_units() -> None:
    assert pytest.approx(4.74047, rel=1e-6) == o.KAPPA
    assert pytest.approx(4.3009e-3, rel=1e-4) == o.G_PC


# --- Part B: the fit on synthetic galaxies --------------------------------------------------------


@pytest.mark.req("STL-015")
def test_a_flat_rotation_curve_gives_back_its_oort_constants_and_the_suns_motion(flat: dict[str, Any]) -> None:
    truth = o.oort_constants(r0_kpc=8.0, v0_km_s=220.0, slope=0.0)
    stars = o.observe(flat, 0.0)
    f = o.fit(stars.take(o.scenario_mask(stars, o.Scenario((0.3, 1.0), 30.0, 15.0, 0.0, "all"))))
    assert f.value("A") == pytest.approx(truth["A"], rel=0.05)
    assert f.value("B") == pytest.approx(truth["B"], rel=0.05)
    assert abs(f.value("C")) < 4 * f.error("C") and abs(f.value("K")) < 4 * f.error("K")
    assert (f.value("U"), f.value("V"), f.value("W")) == pytest.approx((10.0, 12.0, 7.0), abs=1.0)
    assert f.derived()["omega"] == pytest.approx(220.0 / 8.0, rel=0.05)
    assert 0.1 < f.error("A") < 0.5  # 20 km/s scatter over some 30,000 stars


@pytest.mark.req("STL-015")
@pytest.mark.parametrize(("slope", "offset"), [(-0.3, 20.0), (0.3, -35.0)])
def test_a_rising_or_falling_curve_about_a_turned_centre_gives_its_amplitude_and_l0(
    slope: float, offset: float
) -> None:
    truth = o.oort_constants(r0_kpc=8.0, v0_km_s=220.0, slope=slope)
    cols = o.synthetic_galaxy(n=200_000, slope=slope, centre_offset_deg=offset, seed=13)
    stars = o.observe(cols, 0.0)
    f = o.fit(stars.take(o.scenario_mask(stars, o.Scenario((0.3, 1.0), 30.0, 15.0, 0.0, "all"))))
    d = f.derived()
    assert d["amplitude"] == pytest.approx(truth["A"], rel=0.05)
    assert f.value("B") == pytest.approx(truth["B"], rel=0.05)
    assert d["l0_deg"] == pytest.approx(offset, abs=2.0)


@pytest.mark.req("STL-015")
def test_radial_velocities_alone_give_a_but_not_b(flat: dict[str, Any]) -> None:
    truth = o.oort_constants(r0_kpc=8.0, v0_km_s=220.0, slope=0.0)
    stars = o.observe(flat, 0.0)
    f = o.fit(
        stars.take(o.scenario_mask(stars, o.Scenario((0.3, 1.0), 30.0, 15.0, 0.0, "all"))),
        ("r",),
        o.RV_PARAMETERS,
    )
    assert f.value("A") == pytest.approx(truth["A"], rel=0.05)
    assert "B" not in f.parameters and "omega" not in f.derived()


@pytest.mark.req("STL-015")
def test_high_velocity_stars_are_clipped_not_fitted(flat: dict[str, Any]) -> None:
    cols = {k: np.array(v, copy=True) for k, v in flat.items()}
    cols["pmra"][:500] += 400.0  # 500 stars with a few hundred km/s more than the disc
    stars = o.observe(cols, 0.0)
    base = o.Scenario((0.3, 1.0), 30.0, 15.0, 0.0, "all")
    f = o.fit(stars.take(o.scenario_mask(stars, base)))
    truth = o.oort_constants(r0_kpc=8.0, v0_km_s=220.0, slope=0.0)
    assert f.value("A") == pytest.approx(truth["A"], rel=0.05)
    assert f.n_clipped >= 0.9 * 500 * 0.15  # about 15% of the stars lie in this scenario


@pytest.mark.req("STL-015")
def test_too_few_stars_is_a_named_failure() -> None:
    cols = o.synthetic_galaxy(n=300, seed=3)
    stars = o.observe(cols, 0.0)
    with pytest.raises(ValueError, match="fewer than the declared 200"):
        o.fit(stars.take(o.scenario_mask(stars, o.baseline())))


# --- selection ------------------------------------------------------------------------------------


@pytest.mark.req("STL-016")
def test_the_shared_cuts_and_the_scenario_cuts_are_the_declared_ones(flat: dict[str, Any]) -> None:
    cols = {k: np.array(v, copy=True) for k, v in flat.items()}
    cols["ruwe"][:10] = 1.5
    cols["parallax_error"][10:20] = cols["parallax"][10:20] / 9.0
    cols["duplicated_source"][20:30] = True
    cols["pmra"][30:40] = np.nan
    keep = o.quality_mask(cols)
    assert not keep[:40].any() and keep[40:].all()
    stars = o.observe(cols, -0.017)
    assert stars.d_kpc[0] == pytest.approx(1.0 / (cols["parallax"][0] + 0.017))
    blue = o.Scenario((0.3, 1.0), 15.0, 13.0, -0.017, "blue")
    m = o.scenario_mask(stars, blue)
    assert (stars.bp_rp[m] < 0.5).all() and (np.abs(stars.b_deg[m]) < 15).all() and (stars.g[m] < 13).all()
    assert ((stars.d_kpc[m] >= 0.3) & (stars.d_kpc[m] <= 1.0)).all()
    assert len(o.scenarios()) == 48 and o.baseline() in o.scenarios()


# --- the reading ----------------------------------------------------------------------------------


@pytest.mark.req("STL-017")
@pytest.mark.parametrize(("slope", "status"), [(0.0, "supports_UC04_H1"), (1.0, "supports_UC04_H0")])
def test_the_verdict_follows_the_planted_rotation(slope: float, status: str) -> None:
    # slope 1 is solid-body rotation, Oort's A = 0
    cols = o.synthetic_galaxy(n=200_000, slope=slope, seed=17)
    report = o.run(cols, cols, choices=small_choices())
    assert report["verdict"]["status"] == status
    assert report["radial_velocities"]["reading"]["status"] == status
    assert report["choices_match_committed"] is False
    assert report["committed_choices_sha256"] == o.choices_sha256()
    assert len(report["proper_motions"]["scenarios"]) == 2


@pytest.mark.req("STL-017")
def test_oorts_value_is_compared_with_the_fitted_amplitude() -> None:
    # 31 km/s/kpc is Oort's A: plant it, and then half of it
    def galaxy_with_a(a: float) -> dict[str, Any]:
        return o.synthetic_galaxy(n=200_000, v0_km_s=2 * a * 8.0, slope=0.0, seed=19)

    near = o.run(galaxy_with_a(31.0), choices=small_choices())["proper_motions"]["comparison"]
    assert near["status"] == "agrees_with_oort" and near["oort_A_km_s_kpc"] == pytest.approx(31.0)
    far = o.run(galaxy_with_a(15.0), choices=small_choices())["proper_motions"]["comparison"]
    assert far["status"] == "disagrees_with_oort"
    assert all(z < -3 for z in far["z"].values())


@pytest.mark.req("STL-017")
def test_a_failed_scenario_or_a_quick_look_decides_nothing(flat: dict[str, Any]) -> None:
    choices = small_choices()
    choices["scenarios"]["shell_kpc"].append([0.01, 0.02])  # no star lies that close
    report = o.run(flat, choices=choices)
    r = report["verdict"]
    assert r["status"] == "inconclusive" and len(r["scenarios_failed"]) == 1
    quick = o.run(flat, baseline_only=True)
    assert quick["verdict"]["status"] == "inconclusive" and quick["verdict"]["quick_look"] is True
    assert quick["radial_velocities"]["not_run"].startswith("no radial velocities")


@pytest.mark.req("STL-017")
def test_the_report_carries_oorts_radius_from_the_fitted_constants(flat: dict[str, Any]) -> None:
    report = o.run(flat, choices=small_choices())
    pm = report["proper_motions"]
    omega = pm["baseline"]["derived"]["omega"]
    assert pm["oort_radius"]["R_kpc_from_V_over_omega"] == pytest.approx(272.0 / omega)
    a_pc = pm["baseline"]["A"]["value"] / 1000.0
    assert pm["oort_radius"]["R_kpc_from_A"]["k2_zero"] == pytest.approx(0.75 * 272.0 / a_pc / 1000.0)
    assert math.isfinite(pm["baseline"]["derived"]["l0_deg"])
