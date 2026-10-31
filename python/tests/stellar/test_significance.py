"""T1/T8: the per-candidate significance test (STL-011). A dense group scores many sigma above the
stars around it, a random handful of the field scores near zero, malformed settings are refused, and
in the sweep a potentially new candidate must beat every false candidate of its tile's null fields."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from sdp.adapters.bootstrap import clusterers
from sdp_stellar import analysis, sweep
from sdp_stellar.significance import MAX_SIGMA, SignificanceSettings, significance, tree

from .test_sweep import SYN_B, benchmarks, owner, praesepe_field, settings, tile_rows

pytestmark = [pytest.mark.tier("T1"), pytest.mark.req("STL-011")]

DEFAULT = {"k": 10, "field_multiple": 5, "min_field": 50, "min_sigma": 3.0}


def field_with_blob(seed: int, blob: int = 40, field: int = 4000, width: float = 0.15) -> np.ndarray:
    """A 5-D Gaussian field with a tight group at (1, 1, 1, 1, 1); the group is rows 0..blob-1."""
    rng = np.random.default_rng(seed)
    return np.vstack([rng.normal(1.0, width, (blob, 5)), rng.normal(0.0, 1.0, (field, 5))])


def sigma_of(features: np.ndarray, members: np.ndarray, **changes: Any) -> float | None:
    return significance(tree(features), features, members, SignificanceSettings.from_parameters({**DEFAULT, **changes}))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_dense_group_is_many_sigma_above_the_stars_around_it(seed: int) -> None:
    x = field_with_blob(seed)
    value = sigma_of(x, np.arange(40))
    assert value is not None and value > 5.0


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_random_handful_of_the_field_is_not_significant(seed: int) -> None:
    x = field_with_blob(seed)
    rng = np.random.default_rng(seed)
    members = np.sort(rng.choice(np.arange(40, x.shape[0]), 40, replace=False))
    value = sigma_of(x, members)
    assert value is not None and value < DEFAULT["min_sigma"]


def test_the_significance_is_capped_where_the_p_value_underflows(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from scipy import stats

    x = field_with_blob(4)
    for p, expected in ((0.0, MAX_SIGMA), (1e-320, MAX_SIGMA), (0.9, 0.0)):
        monkeypatch.setattr(stats, "mannwhitneyu", lambda *a, p=p, **k: SimpleNamespace(pvalue=p))
        assert sigma_of(x, np.arange(40)) == expected


def test_too_few_members_or_surrounding_stars_cannot_be_assessed() -> None:
    x = field_with_blob(5, blob=40, field=30)
    assert sigma_of(x, np.arange(10)) is None  # 10 members cannot have a 10th neighbour among themselves plus one
    assert sigma_of(x, np.arange(40)) is None  # 30 field stars are fewer than min_field 50
    assert sigma_of(x, np.arange(40), min_field=20) is not None


def test_the_field_sample_is_the_stars_nearest_the_candidate_not_the_whole_tile() -> None:
    # the same blob, beside a far-off second blob: only the surroundings are compared, so it changes nothing
    x = field_with_blob(6)
    far = np.random.default_rng(6).normal(50.0, 0.01, (500, 5))
    assert sigma_of(np.vstack([x, far]), np.arange(40)) == pytest.approx(sigma_of(x, np.arange(40)))


@pytest.mark.parametrize(
    "changes",
    [{"k": 0}, {"k": 2.5}, {"k": True}, {"min_field": 1}, {"field_multiple": 0}, {"min_sigma": float("nan")}],
)
def test_malformed_significance_settings_are_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="significance"):
        SignificanceSettings.from_parameters({**DEFAULT, **changes})


def test_the_predeclared_run_has_no_significance_test_and_the_sweep_has_one() -> None:
    from sdp_stellar.protocol import load_predeclaration

    assert analysis.significance_settings(load_predeclaration()["parameters"]) is None
    assert analysis.significance_settings(sweep.load_settings()) == SignificanceSettings(10, 5.0, 50, 3.0)


def test_the_assessment_refuses_to_run_the_test_without_the_feature_matrix() -> None:
    t = owner(*SYN_B)
    rows = tile_rows(t, praesepe_field())
    with pytest.raises(ValueError, match="feature matrix"):
        analysis._assess("r", rows, [], [], settings(), 1)


@pytest.mark.tier("T8")
def test_in_the_sweep_the_planted_cluster_is_significant_and_the_field_itself_is_not() -> None:
    t = owner(*SYN_B)
    result = sweep.sweep_tile(tile_rows(t, praesepe_field()), t, settings(), clusterers(), benchmarks(), 2026)
    new = [c for c in result["candidates"] if c["status"] == "potentially_new"]
    assert len(new) == 1 and new[0]["significance_sigma"] > 5.0
    field = max(result["candidates"], key=lambda c: c["n_members"])
    assert field["status"] == "rejected"
    # rejected as the field itself before the neighbour queries, so it is never tested
    assert any("the field itself" in r for r in field["reasons"]) and field["significance_sigma"] is None
    assert result["null_survivor_sigmas"] == []
    row = next(r for r in sweep.candidate_rows([result]) if r["status"] == "potentially_new")
    assert row["significance_sigma"] == new[0]["significance_sigma"]


@pytest.mark.tier("T8")
def test_a_null_survivor_more_significant_than_the_candidate_makes_it_inconclusive() -> None:
    t = owner(*SYN_B)
    loose = {**sweep.load_settings()["candidate_checks"], "max_field_fraction": 1.0, "min_cmd_coherence": 0.0}
    lax = settings(candidate_checks=loose, significance={**DEFAULT, "min_sigma": 0.01})
    result = sweep.sweep_tile(tile_rows(t, praesepe_field()), t, lax, clusterers(), benchmarks(), 2026)
    worst = max(result["null_survivor_sigmas"])
    syn_b = next(c for c in result["candidates"] if c.get("significance_sigma") and c["n_members"] < 100)
    assert syn_b["significance_sigma"] <= worst
    assert syn_b["status"] == "inconclusive"
    assert "no higher than a false candidate's" in syn_b["reasons"][-1]


def _candidate(sigma: float | None) -> dict[str, Any]:
    return {"status": "potentially_new", "reasons": ["passed every check"], "significance_sigma": sigma}


@pytest.mark.parametrize(
    ("sigma", "survivors", "status"),
    [
        (9.0, [], "potentially_new"),
        (9.0, [4.0, 8.9], "potentially_new"),
        (9.0, [4.0, 9.0], "inconclusive"),
        (9.0, [None], "inconclusive"),
        (None, [1.0], "inconclusive"),
    ],
)
def test_a_new_candidate_is_held_to_each_false_candidate_of_its_tile(
    sigma: float | None, survivors: list[float | None], status: str
) -> None:
    c = _candidate(sigma)
    sweep._against_null(c, survivors)
    assert c["status"] == status
    assert len(c["reasons"]) == (1 if status == "potentially_new" else 2)


def test_neighbour_distances_are_the_same_queried_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    from sdp_stellar import significance as module

    x = field_with_blob(7)
    whole = sigma_of(x, np.arange(40))
    monkeypatch.setattr(module, "BATCH", 7)
    assert sigma_of(x, np.arange(40)) == whole


def test_a_candidate_already_failing_a_cheap_check_is_not_tested() -> None:
    from sdp_stellar.analysis import CandidateChecks, _worth_testing
    from sdp_stellar.membership import Candidate

    checks = CandidateChecks(10, 0.5, 3.0, 0.5)

    def cand(members: int, stability: float = 1.0) -> Candidate:
        return Candidate("r-c000", np.arange(members), np.ones(members), stability, members)

    assert _worth_testing(cand(40), checks, 1000)
    assert not _worth_testing(cand(9), checks, 1000)  # too few members
    assert not _worth_testing(cand(600), checks, 1000)  # the field itself
    assert not _worth_testing(cand(40, stability=0.4), checks, 1000)  # unstable


@pytest.mark.tier("T8")
def test_a_candidate_below_the_minimum_significance_is_rejected_and_says_so() -> None:
    t = owner(*SYN_B)
    strict = settings(significance={**DEFAULT, "min_sigma": 30.0})
    result = sweep.sweep_tile(tile_rows(t, praesepe_field()), t, strict, clusterers(), benchmarks(), 2026)
    syn_b = next(c for c in result["candidates"] if 50 < c["n_members"] < 100)
    assert syn_b["status"] == "rejected"
    assert (
        syn_b["reasons"][-1]
        == f"no denser than the stars around it: significance {syn_b['significance_sigma']:.1f} sigma < 30"
    )
