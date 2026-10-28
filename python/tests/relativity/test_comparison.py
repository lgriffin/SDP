"""T8: the four hypotheses compared on equal footing (REL-005)."""

from __future__ import annotations

import pytest

from sdp_relativity.comparison import BIC_STRONG, compare, pooled_system
from sdp_relativity.fitter import build_system, fit
from sdp_relativity.models import hypotheses
from sdp_relativity.observations import PoolingJustification, parse_bundle
from sdp_relativity.reduction import BASELINE, fit_scenarios
from sdp_relativity.synthetic import make_bundle

pytestmark = pytest.mark.tier("T8")

CENTRE = 1.3125  # midway between the half and GR limb values


@pytest.mark.req("REL-005")
@pytest.mark.parametrize(("planted", "expected"), [(0.0, "UC02-H0"), (0.875, "UC02-H1"), (1.75, "UC02-H2")])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_planted_hypothesis_is_preferred(planted: float, expected: str, seed: int) -> None:
    obs = parse_bundle(make_bundle(planted, seed)).instrument("sobral-4in")
    comparison = compare(build_system(obs, BASELINE), "gaussian", hypotheses(), [1.0], CENTRE)
    assert comparison.preferred == expected
    point_alternatives = {"UC02-H0", "UC02-H1", "UC02-H2"} - {expected}
    assert any(h in comparison.strongly_disfavoured() for h in point_alternatives)


@pytest.mark.req("REL-005")
def test_every_hypothesis_is_evaluated_in_every_fit_scenario() -> None:
    obs = parse_bundle(make_bundle(seed=4)).instrument("sobral-astro")
    for scenario in fit_scenarios():
        comparison = compare(build_system(obs, scenario), scenario.likelihood, hypotheses(), [0.5, 1.0, 2.0], CENTRE)
        assert [r.hypothesis_id for r in comparison.results] == ["UC02-H0", "UC02-H1", "UC02-H2", "UC02-H3"]
        assert set(comparison.log_bayes_vs_free) == {0.5, 1.0, 2.0}
        assert all(set(v) == {"UC02-H0", "UC02-H1", "UC02-H2"} for v in comparison.log_bayes_vs_free.values())
        assert min(comparison.delta_bic().values()) == 0.0


def test_bayes_factors_follow_the_likelihood_ratio() -> None:
    obs = parse_bundle(make_bundle(seed=5)).instrument("sobral-4in")
    comparison = compare(build_system(obs, BASELINE), "gaussian", hypotheses(), [1.0], CENTRE)
    bayes = comparison.log_bayes_vs_free[1.0]
    gr, half = comparison.result("UC02-H2").fit, comparison.result("UC02-H1").fit
    assert bayes["UC02-H2"] - bayes["UC02-H1"] == pytest.approx(gr.loglik - half.loglik, abs=1e-9)
    assert BIC_STRONG == 10.0


@pytest.mark.req("REL-004")
def test_pooling_shares_alpha_and_keeps_plate_constants_per_instrument() -> None:
    bundle = parse_bundle(make_bundle(seed=6))
    systems = [build_system(o, BASELINE) for o in bundle.sets]
    pooled = pooled_system(systems)
    assert pooled.nuisance == sum(s.nuisance for s in systems)
    assert pooled.n == sum(s.n for s in systems)
    assert pooled.plates == tuple(p for s in systems for p in s.plates)
    # Off-diagonal nuisance blocks are empty: no instrument's rows touch another's plate constants.
    r0 = c0 = 0
    for s in systems:
        block = pooled.design[r0 : r0 + s.n, : pooled.nuisance]
        assert not block[:, :c0].any() and not block[:, c0 + s.nuisance :].any()
        r0, c0 = r0 + s.n, c0 + s.nuisance
    pooled_sigma = fit(pooled).sigma_alpha
    assert pooled_sigma is not None
    assert all(pooled_sigma < (fit(s).sigma_alpha or 0) for s in systems)


@pytest.mark.req("REL-004")
def test_pooling_needs_a_stated_justification() -> None:
    with pytest.raises(ValueError, match="at least two"):
        PoolingJustification(("sobral-4in",), "one instrument", ("common alpha",))
    with pytest.raises(ValueError, match="reason and its assumptions"):
        PoolingJustification(("sobral-4in", "principe-astro"), "  ", ("common alpha",))
    with pytest.raises(ValueError, match="reason and its assumptions"):
        PoolingJustification(("sobral-4in", "principe-astro"), "predeclared axis", ())
