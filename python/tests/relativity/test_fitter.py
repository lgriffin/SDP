"""T8: the reference fitter recovers planted deflections on synthetic bundles (§9)."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from sdp_relativity.fitter import FitError, bootstrap, build_system, fit, fit_set
from sdp_relativity.observations import ObservationSet, parse_bundle
from sdp_relativity.reduction import BASELINE, FitScenario
from sdp_relativity.synthetic import make_bundle

pytestmark = pytest.mark.tier("T8")


def _value(v: float | None) -> float:
    assert v is not None
    return v


def sobral(alpha: float = 1.75, seed: int = 3) -> ObservationSet:
    return parse_bundle(make_bundle(alpha, seed)).instrument("sobral-4in")


@pytest.mark.parametrize("planted", [0.0, 0.875, 1.75])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_recovers_the_planted_deflection_within_three_sigma(planted: float, seed: int) -> None:
    for obs in parse_bundle(make_bundle(planted, seed)).sets:
        result = fit_set(obs, BASELINE)
        assert result.sigma_alpha is not None
        assert abs(result.alpha - planted) < 3 * result.sigma_alpha, obs.instrument


def test_noise_free_data_is_recovered_exactly() -> None:
    obs = sobral()
    system = build_system(obs, BASELINE)
    beta = np.linspace(-0.3, 0.3, system.design.shape[1])
    beta[-1] = 1.75
    exact = dataclasses.replace(system, observed=system.design @ beta)
    assert fit(exact).alpha == pytest.approx(1.75, abs=1e-10)
    assert fit(exact, "student_t").alpha == pytest.approx(1.75, abs=1e-10)


@pytest.mark.req("REL-010")
def test_the_six_constant_model_absorbs_unequal_plate_scales() -> None:
    obs = sobral()
    stretched = dataclasses.replace(
        obs,
        plates=tuple(
            dataclasses.replace(
                p,
                stars=tuple(
                    dataclasses.replace(
                        s, dx_arcsec=_value(s.dx_arcsec) + 0.004 * s.geometry.x_arcmin + 0.002 * s.geometry.y_arcmin
                    )
                    for s in p.stars
                ),
            )
            for p in obs.plates
        ),
    )
    affine = FitScenario(plate_scale="solved_affine")
    assert build_system(obs, affine).nuisance == 6 * len(obs.plates)
    assert fit_set(stretched, affine).alpha == pytest.approx(fit_set(obs, affine).alpha, abs=1e-9)
    assert abs(fit_set(stretched, BASELINE).alpha - fit_set(obs, BASELINE).alpha) > 1e-3


@pytest.mark.req("REL-010")
@pytest.mark.parametrize("plate_scale", ["solved", "solved_affine", "fixed_from_check_plates"])
def test_a_set_measured_in_one_coordinate_leaves_the_other_constants_out(plate_scale: str) -> None:
    obs = sobral()
    dec_only = dataclasses.replace(
        obs,
        plates=tuple(
            dataclasses.replace(p, stars=tuple(dataclasses.replace(s, dx_arcsec=None) for s in p.stars))
            for p in obs.plates
        ),
    )
    system = build_system(dec_only, FitScenario(plate_scale=plate_scale))
    per_plate = {"solved": 3, "solved_affine": 3, "fixed_from_check_plates": 1}[plate_scale]
    assert system.nuisance == per_plate * len(obs.plates)
    assert fit(system).sigma_alpha is not None


@pytest.mark.req("REL-010")
def test_fixed_scale_subtracts_known_terms_and_solves_an_unknown_rotation() -> None:
    obs = sobral()
    fixed = FitScenario(plate_scale="fixed_from_check_plates")
    terms = (0.003, -0.001, 0.002, 0.004)
    shifted = dataclasses.replace(
        obs,
        plates=tuple(
            dataclasses.replace(
                p,
                check_terms=terms,
                check_rotation=None,
                stars=tuple(
                    dataclasses.replace(
                        s,
                        dx_arcsec=_value(s.dx_arcsec)
                        - p.check_scale * s.geometry.x_arcmin
                        - _value(p.check_rotation) * s.geometry.y_arcmin
                        + terms[0] * s.geometry.x_arcmin
                        + terms[1] * s.geometry.y_arcmin
                        + 0.01 * s.geometry.y_arcmin,
                        dy_arcsec=_value(s.dy_arcsec)
                        - p.check_scale * s.geometry.y_arcmin
                        + _value(p.check_rotation) * s.geometry.x_arcmin
                        + terms[2] * s.geometry.x_arcmin
                        + terms[3] * s.geometry.y_arcmin
                        - 0.01 * s.geometry.x_arcmin,
                    )
                    for s in p.stars
                ),
            )
            for p in obs.plates
        ),
    )
    assert build_system(shifted, fixed).nuisance == 3 * len(obs.plates)
    rotation_solved = dataclasses.replace(
        obs, plates=tuple(dataclasses.replace(p, check_rotation=None) for p in obs.plates)
    )
    assert fit_set(shifted, fixed).alpha == pytest.approx(fit_set(rotation_solved, fixed).alpha, abs=1e-9)
    known = dataclasses.replace(
        shifted, plates=tuple(dataclasses.replace(p, check_rotation=0.01) for p in shifted.plates)
    )
    assert build_system(known, fixed).nuisance == 2 * len(obs.plates)


def test_student_t_resists_one_wild_measurement() -> None:
    obs = sobral()
    system = build_system(obs, BASELINE)
    worst = int(np.argmax(np.abs(system.design[:, -1])))
    observed = system.observed.copy()
    observed[worst] += 5.0
    spoiled = dataclasses.replace(system, observed=observed)
    clean = fit(system).alpha
    assert abs(fit(spoiled, "student_t").alpha - clean) < abs(fit(spoiled).alpha - clean)


def test_a_point_hypothesis_frees_only_the_plate_constants() -> None:
    system = build_system(sobral(), BASELINE)
    free, fixed = fit(system), fit(system, fixed_alpha=1.75)
    assert fixed.k == free.k - 1 and fixed.sigma_alpha is None and fixed.alpha == 1.75
    assert fixed.loglik <= free.loglik


def test_fixed_scale_with_per_plate_errors_widens_the_uncertainty() -> None:
    obs = sobral()
    independent = fit_set(obs, FitScenario(plate_scale="fixed_from_check_plates"))
    correlated = fit_set(obs, FitScenario(plate_scale="fixed_from_check_plates", error_correlation="per_plate"))
    assert independent.sigma_alpha is not None and correlated.sigma_alpha is not None
    assert correlated.sigma_alpha > independent.sigma_alpha


def test_converting_probable_errors_widens_the_uncertainty_by_the_factor() -> None:
    obs = sobral()
    quoted = fit_set(obs, FitScenario(error_convention="probable_as_standard"))
    converted = fit_set(obs, BASELINE)
    assert quoted.alpha == pytest.approx(converted.alpha, abs=1e-12)
    assert quoted.sigma_alpha is not None and converted.sigma_alpha is not None
    assert converted.sigma_alpha / quoted.sigma_alpha == pytest.approx(1.482602218505602, rel=1e-9)


def test_too_few_stars_to_solve_the_plate_constants_is_an_error() -> None:
    obs = sobral()
    tiny = obs.restrict([(obs.plates[0].plate_id, obs.plates[0].stars[0].star_id)])
    with pytest.raises(FitError, match="too few stars"):
        fit_set(tiny, BASELINE)


def test_bootstrap_is_seeded_and_brackets_the_fit() -> None:
    obs = sobral()
    a, b = bootstrap(obs, BASELINE, 60, seed=11), bootstrap(obs, BASELINE, 60, seed=11)
    assert a == b
    assert a != bootstrap(obs, BASELINE, 60, seed=12)
    assert a["p2_5"] < fit_set(obs, BASELINE).alpha < a["p97_5"]
    assert a["resamples"] == 60
