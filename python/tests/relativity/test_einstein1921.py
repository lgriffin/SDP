"""UC-03: Einstein's 1921 globular-cluster calculation (REL-011 to REL-015)."""

from __future__ import annotations

import copy
import math
import re

import numpy as np
import pytest
from scipy.integrate import quad

from sdp_relativity import einstein1921 as e

pytestmark = pytest.mark.tier("T1")


# --- REL-011: the 1921 arithmetic -------------------------------------------------------------


@pytest.mark.req("REL-011")
def test_the_factor_for_einsteins_density_law_is_three_pi_over_32_not_six_tenths() -> None:
    assert e.plummer_a() == pytest.approx(0.29452431127, rel=1e-10)
    assert e.plummer_a_numeric() == pytest.approx(e.plummer_a(), rel=1e-6)
    # "etwa 0,6" is the double-counted value, 3 pi / 16, to within 2%
    assert 2 * e.plummer_a() == pytest.approx(0.6, rel=0.02)
    assert abs(e.plummer_a() - 0.6) / 0.6 > 0.5


@pytest.mark.req("REL-011")
def test_part_a_sets_the_printed_numbers_beside_the_recomputed_and_corrected_ones() -> None:
    a = e.arithmetic()
    assert a["A_printed"] == 0.6
    assert a["printed"] == {
        "two_a_cm": 0.65e18,
        "two_a_ly": 0.65,
        "two_a_with_factor_ly": 65.0,
        "printed_cm_in_ly": pytest.approx(0.687, abs=0.001),
    }
    # Einstein's own coefficient on his own inputs: 0.71e18 cm, not the printed 0.65e18
    assert a["einstein_coefficient"]["coefficient"] == 1.2
    assert a["einstein_coefficient"]["two_a_cm"] == pytest.approx(0.7069e18, rel=1e-3)
    assert a["einstein_coefficient"]["two_a_ly"] == pytest.approx(0.747, abs=0.001)
    # the factor of 100 in N scales 2a by 100
    assert a["einstein_coefficient"]["two_a_with_factor_ly"] == pytest.approx(74.7, abs=0.1)
    # with A = 3 pi / 32 the radius roughly halves
    assert a["corrected_coefficient"]["coefficient"] == pytest.approx(2 * e.plummer_a())
    assert a["corrected_coefficient"]["two_a_ly"] == pytest.approx(0.367, abs=0.001)
    assert a["corrected_coefficient"]["two_a_with_factor_ly"] == pytest.approx(36.7, abs=0.1)
    assert a["radius_lower_bound_ly"] == 100.0


# --- REL-012: the isotropic Plummer relations ------------------------------------------------


def _rho(r: float) -> float:
    return float(3 / (4 * math.pi) * (1 + r * r) ** -2.5)


def _sigma_r_sq(r: float) -> float:
    """Jeans equation for the isotropic Plummer sphere, G = M = a = 1."""
    return float(quad(lambda x: _rho(x) * x / (1 + x * x) ** 1.5, r, math.inf)[0]) / _rho(r)


@pytest.mark.req("REL-012")
@pytest.mark.parametrize("radius", [0.0, 0.5, 2.0])
def test_the_projected_dispersion_follows_from_the_jeans_equation(radius: float) -> None:
    num = quad(lambda z: _rho(math.hypot(radius, z)) * _sigma_r_sq(math.hypot(radius, z)), 0, 200)[0]
    den = quad(lambda z: _rho(math.hypot(radius, z)), 0, 200)[0]
    g = e.gravitational_constant_pc()
    sigma0_sq = e.central_projected_dispersion_sq(1 / g, 1.0)  # G M / a = 1
    assert sigma0_sq == pytest.approx(3 * math.pi / 64)
    assert num / den == pytest.approx(sigma0_sq / math.sqrt(1 + radius**2), rel=1e-6)


@pytest.mark.req("REL-012")
def test_the_mean_square_speed_is_the_virial_one_and_inverts_to_the_mass() -> None:
    three_d = quad(lambda r: 4 * math.pi * r * r * _rho(r) * 3 * _sigma_r_sq(r), 0, math.inf)[0]
    g = e.gravitational_constant_pc()
    assert e.mean_square_speed(1 / g, 1.0) == pytest.approx(three_d, rel=1e-6)
    assert e.mean_square_speed(1 / g, 1.0) == pytest.approx(e.plummer_a())  # 2T = |W|
    sigma = math.sqrt(e.central_projected_dispersion_sq(4.2e5, 3.1))
    assert e.mass_from_dispersion(sigma, 3.1) == pytest.approx(4.2e5)


@pytest.mark.req("REL-012")
def test_the_annulus_holds_the_plummer_share_of_the_stars() -> None:
    assert e.annulus_fraction(0.0, 1e9, 1.0) == pytest.approx(1.0)
    assert e.annulus_fraction(0.0, 1.0, 1.0) == 0.5
    assert e.annulus_fraction(1.0, 3.0, 1.0) == pytest.approx(0.9 - 0.5)


# --- REL-013: the fit recovers a planted cluster ---------------------------------------------


@pytest.fixture(scope="module")
def planted() -> dict[str, np.ndarray]:
    return e.synthetic_cluster(
        mass=3e5, a_pc=3.0, distance_pc=7000, n_cluster=12000, n_field=10000, mass_to_light=2.0, seed=7
    )


@pytest.mark.req("REL-013")
def test_the_fit_recovers_the_planted_mass_distance_and_light(planted: dict[str, np.ndarray]) -> None:
    stars, removed = e.select(planted)
    assert removed == dict.fromkeys(e.SELECTION_ORDER, 0)
    centre = e.find_centre(stars)
    assert math.hypot(*centre) < 0.003
    exact = e.Scenario(3.0, 0.6, 1.0, 0.0, 0.0)
    f = e.fit(stars, centre, exact)
    assert f.converged
    p = e.physical(stars, f, exact)
    assert p.distance_pc == pytest.approx(7000, rel=0.03)
    assert p.a_pc == pytest.approx(3.0, rel=0.1)
    assert p.mass_solar == pytest.approx(3e5, rel=0.15)
    assert p.mass_to_light == pytest.approx(2.0, rel=0.15)
    assert p.mass_solar_einstein_a == pytest.approx(p.mass_solar * e.plummer_a() / 0.6)
    assert p.v_rms_kms == pytest.approx(math.sqrt(e.mean_square_speed(p.mass_solar, p.a_pc)))


@pytest.mark.req("REL-013")
def test_a_heavy_planted_cluster_is_found_heavy() -> None:
    cols = e.synthetic_cluster(
        mass=3e5, a_pc=3.0, distance_pc=7000, n_cluster=12000, n_field=10000, mass_to_light=15.0, seed=11
    )
    stars, _ = e.select(cols)
    centre = e.find_centre(stars)
    exact = e.Scenario(3.0, 0.6, 1.0, 0.0, 0.0)
    p = e.physical(stars, e.fit(stars, centre, exact), exact)
    assert len(stars) == cols["ra"].size  # every planted star is brighter than the G cut
    assert p.mass_to_light == pytest.approx(15.0, rel=0.15)


@pytest.mark.req("REL-013")
def test_too_few_stars_in_the_annulus_is_refused_by_name() -> None:
    cols = e.synthetic_cluster(mass=3e5, a_pc=3.0, distance_pc=7000, n_cluster=40, n_field=5, mass_to_light=2.0, seed=3)
    stars, _ = e.select(cols)
    with pytest.raises(ValueError, match=r"^only \d+ stars in the annulus for r_in=3.0' .*; at least 50 are needed$"):
        e.fit(stars, (0.0, 0.0), e.baseline())


# --- REL-014: the decision rule ---------------------------------------------------------------


def _band(p16: float, p84: float) -> dict[str, dict[str, float]]:
    return {"mass_to_light": {"p16": p16, "p50": (p16 + p84) / 2, "p84": p84}}


@pytest.mark.req("REL-014")
def test_the_verdict_supports_einstein_only_when_the_band_and_every_scenario_are_below_ten() -> None:
    assert e.verdict(_band(1.5, 2.5), {"a": 2.0, "b": 3.0}, 10.0)["status"] == "supports_H1"
    assert e.verdict(_band(1.5, 10.5), {"a": 2.0, "b": 3.0}, 10.0)["status"] == "inconclusive"
    mixed = e.verdict(_band(1.5, 2.5), {"a": 2.0, "b": 10.0}, 10.0)
    assert mixed["status"] == "inconclusive"
    assert mixed["scenarios_below"] == ["a"] and mixed["scenarios_at_or_above"] == ["b"]


@pytest.mark.req("REL-014")
def test_the_verdict_goes_against_einstein_only_when_everything_is_at_or_above_ten() -> None:
    assert e.verdict(_band(10.0, 40.0), {"a": 12.0, "b": 10.0}, 10.0)["status"] == "supports_H0"
    assert e.verdict(_band(9.9, 40.0), {"a": 12.0}, 10.0)["status"] == "inconclusive"


@pytest.mark.req("REL-014")
def test_a_failed_scenario_or_a_missing_bootstrap_makes_the_verdict_inconclusive() -> None:
    failed = e.verdict(_band(1.0, 2.0), {"a": 2.0, "b": None}, 10.0)
    assert failed["status"] == "inconclusive" and failed["scenarios_failed"] == ["b"]
    assert e.verdict({}, {"a": 2.0}, 10.0)["status"] == "inconclusive"


@pytest.mark.req("REL-014")
def test_a_bootstrap_with_any_failed_refit_decides_nothing() -> None:
    assert e.verdict(_band(1.5, 2.5), {"a": 2.0}, 10.0, bootstrap_complete=False) == {
        "status": "inconclusive",
        "threshold": 10.0,
        "scenarios_below": ["a"],
        "scenarios_at_or_above": [],
        "scenarios_failed": [],
        "bootstrap_complete": False,
    }


@pytest.mark.req("REL-014")
def test_a_refit_that_raises_is_recorded_as_a_failure_not_an_abort(planted: dict[str, np.ndarray]) -> None:
    stars, _ = e.select(planted)
    centre = e.find_centre(stars)
    sparse = stars.take(np.arange(60))  # too few stars in the annulus once resampled
    boot = e.bootstrap(sparse, centre, e.baseline(), np.zeros(len(e.PARAMS)), 2, 1921)
    assert boot.requested == 2 and boot.results == [] and not boot.complete
    assert len(boot.failures) == 2
    assert all(re.match(r"^only \d+ stars in the annulus", f) for f in boot.failures)


@pytest.mark.req("REL-014")
def test_the_predeclared_grid_has_48_scenarios_and_contains_the_baseline() -> None:
    grid = e.scenarios()
    assert len(grid) == 48 and len(set(grid)) == 48
    assert e.baseline() in grid
    assert e.baseline() == e.Scenario(3.0, 0.6, 1.0, -0.017, 0.0)


@pytest.mark.req("REL-014")
@pytest.mark.parametrize(("mass_to_light", "status"), [(2.0, "supports_H1"), (15.0, "supports_H0")])
def test_the_verdict_follows_the_planted_mass_to_light_ratio(mass_to_light: float, status: str) -> None:
    cols = e.synthetic_cluster(
        mass=3e5, a_pc=3.0, distance_pc=7000, n_cluster=12000, n_field=10000, mass_to_light=mass_to_light, seed=5
    )
    report = e.run(cols, resamples=5, all_scenarios=False)
    assert report["verdict"]["status"] == status


# --- REL-015: the report records what it was run under ------------------------------------------


@pytest.mark.req("REL-015")
def test_a_run_records_the_committed_choices_and_says_when_they_were_changed(planted: dict[str, np.ndarray]) -> None:
    report = e.run(planted, resamples=2, all_scenarios=False)
    assert report["protocol_id"] == "uc03-einstein-1921"
    assert report["committed_choices_sha256"] == e.choices_sha256()
    assert report["choices_match_committed"] is True
    assert report["baseline"]["bootstrap"]["seed"] == 1921
    assert list(report["scenarios"]) == [e.baseline().label]
    assert report["verdict"]["status"] in {"supports_H1", "inconclusive"}
    assert report["selection"]["kept"] == report["selection"]["stars_in"]

    assert report["baseline"]["bootstrap"]["succeeded"] == 2 and report["baseline"]["bootstrap"]["failures"] == []

    changed = copy.deepcopy(e.load_choices())
    changed["hypothesis_threshold_mass_to_light"] = 3.0
    changed["light"]["abs_g_sun"] = 5.67  # one magnitude fainter Sun: every light 2.5 times larger in suns
    altered = e.run(planted, resamples=2, all_scenarios=False, choices=changed)
    assert altered["choices_match_committed"] is False
    assert altered["verdict"]["threshold"] == 3.0
    ratio = report["baseline"]["physical"]["light_g_solar"] / altered["baseline"]["physical"]["light_g_solar"]
    assert ratio == pytest.approx(10**-0.4)
    band = report["baseline"]["bootstrap"]["percentiles"]["light_g_solar"]["p50"]
    altered_band = altered["baseline"]["bootstrap"]["percentiles"]["light_g_solar"]["p50"]
    assert band / altered_band == pytest.approx(10**-0.4)  # the bootstrap uses the run's choices too


@pytest.mark.req("REL-015")
def test_selection_counts_the_rows_each_cut_removes_in_order(planted: dict[str, np.ndarray]) -> None:
    cols = {k: np.array(v, copy=True) for k, v in planted.items()}
    cols["ruwe"][:5] = 2.0
    cols["phot_g_mean_mag"][3:8] = 20.0  # rows 3 and 4 already went on ruwe
    cols["pmra"][10] = np.nan
    stars, removed = e.select(cols)
    assert removed == {
        "finite": 1,
        "ruwe": 5,
        "astrometric_params_solved": 0,
        "duplicated_source": 0,
        "g_max": 3,
    }
    assert len(stars) == cols["ra"].size - 9
