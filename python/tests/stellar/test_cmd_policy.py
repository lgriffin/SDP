"""T1: the CMD coherence policies (STL-010). ``cluster-distance`` places every member at the cluster's
distance, so a faint real cluster whose members' own parallaxes are noisy is no longer judged loose,
while a random handful of field stars at the same distance still is."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from sdp_stellar import analysis, sweep
from sdp_stellar.cmd import (
    MIN_FIELD_AT_DISTANCE,
    absolute_g,
    cluster_parallax,
    coherence,
    coherence_at_distance,
    field_at_distance,
)
from sdp_stellar.membership import Candidate
from sdp_stellar.protocol import load_predeclaration

pytestmark = [pytest.mark.tier("T1"), pytest.mark.req("STL-010")]

CLUSTER_PLX, FRACTIONAL_ERR = 1.0, 0.2  # 1 kpc, parallax/error 5: the predeclared cut's limit


def population(seed: int, members: int = 60, field: int = 3000) -> dict[str, np.ndarray]:
    """A cluster on one main sequence at 1 kpc, measured at parallax/error 5, in a field of main-sequence
    stars between 600 pc and 1.7 kpc; members are rows 0..members-1."""
    rng = np.random.default_rng(seed)
    abs_g = rng.uniform(1.0, 7.0, members)
    colour_c = 0.35 + 0.28 * (abs_g - 1.0) + rng.normal(0, 0.02, members)
    g_c = abs_g + 5 * np.log10(100.0 / CLUSTER_PLX)
    # the field is a main sequence too, widened by its mix of ages, metallicities and binaries
    true_f = rng.uniform(0.6, 1.6, field)
    abs_f = rng.uniform(1.0, 7.0, field)
    colour_f = 0.35 + 0.28 * (abs_f - 1.0) + rng.normal(0, 0.12, field)
    g_f = abs_f + rng.normal(0, 0.35, field) + 5 * np.log10(100.0 / true_f)
    true = np.concatenate([np.full(members, CLUSTER_PLX), true_f])
    err = FRACTIONAL_ERR * true
    return {
        "colour": np.concatenate([colour_c, colour_f]),
        "g": np.concatenate([g_c, g_f]),
        "parallax": true + rng.normal(0, 1, true.size) * err,
        "parallax_err": err,
        "members": np.arange(members),
    }


def own_parallax(p: dict[str, np.ndarray], members: np.ndarray, seed: int) -> float | None:
    return coherence(members, p["colour"], absolute_g(p["g"], p["parallax"]), seed)


def at_distance(p: dict[str, np.ndarray], members: np.ndarray, seed: int) -> float | None:
    return coherence_at_distance(members, p["colour"], p["g"], p["parallax"], p["parallax_err"], seed)


def pass_mark() -> float:
    return float(load_predeclaration()["parameters"]["candidate_checks"]["min_cmd_coherence"])


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_faint_real_cluster_passes_at_its_distance_where_its_own_parallaxes_blur_it(seed: int) -> None:
    p = population(seed)
    blurred, sharp = own_parallax(p, p["members"], seed), at_distance(p, p["members"], seed)
    assert blurred is not None and sharp is not None
    assert blurred < pass_mark() <= sharp


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_field_stars_at_the_same_distance_are_still_no_tighter_than_the_field(seed: int) -> None:
    p = population(seed)
    rng = np.random.default_rng(seed)
    near = np.nonzero((p["parallax"] > 0.8) & (p["parallax"] < 1.2))[0]
    fake = rng.choice(near[near >= p["members"].size], 60, replace=False)
    value = at_distance(p, np.sort(fake), seed)
    assert value is not None
    assert value < pass_mark()


def test_the_cluster_parallax_is_the_inverse_variance_mean_of_usable_members() -> None:
    plx = np.array([1.0, 2.0, np.nan, 5.0])
    err = np.array([1.0, 0.5, 0.1, 0.0])
    # weights 1 and 4; the NaN parallax and the zero error are left out
    assert cluster_parallax(np.arange(4), plx, err) == pytest.approx((1.0 + 4 * 2.0) / 5)
    assert cluster_parallax(np.arange(1), np.array([-1.0]), np.array([0.1])) is None


def test_too_few_field_stars_at_the_cluster_distance_is_inconclusive_not_failed() -> None:
    p = population(4, members=40, field=MIN_FIELD_AT_DISTANCE - 1)
    assert at_distance(p, p["members"], 4) is None


def _table(p: dict[str, np.ndarray]) -> pa.Table:
    return pa.table(
        {
            "g_mag": p["g"],
            "bp_rp": p["colour"],
            "parallax_mas": p["parallax"],
            "parallax_mas_err": p["parallax_err"],
        }
    )


def test_the_predeclared_run_keeps_member_parallax_and_the_sweep_uses_the_cluster_distance() -> None:
    declared = load_predeclaration()["parameters"]
    assert analysis.cmd_policy(declared) == "member-parallax"
    assert analysis.cmd_policy(sweep.load_settings()) == "cluster-distance"
    p = population(5)
    table = _table(p)
    mag = absolute_g(p["g"], p["parallax"])
    c = Candidate("r-c000", p["members"], np.ones(p["members"].size), 1.0, int(p["members"].size))
    assert analysis._coherence(c, table, p["colour"], mag, declared, 5) == own_parallax(p, p["members"], 5)
    swept = analysis._coherence(c, table, p["colour"], mag, {"cmd_policy": "cluster-distance"}, 5)
    assert swept == at_distance(p, p["members"], 5)


def test_an_unknown_cmd_policy_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown cmd_policy"):
        analysis.cmd_policy({"cmd_policy": "isochrone"})


def test_a_cluster_that_dominates_its_distance_is_compared_with_its_nearest_field_stars() -> None:
    parallax = np.array([2.0, 2.01, 2.02, 2.03, 1.0, 1.9, 3.5, 2.5])
    members = np.arange(4)
    usable = np.ones(parallax.size, dtype=bool)
    usable[5] = False  # no colour
    # no non-member lies inside the members' range, so the four nearest usable ones stand in
    assert field_at_distance(members, usable, parallax, 2.015).tolist() == [4, 6, 7]
    wide = np.array([2.0, 2.4, 2.1, 2.3, 2.2, 2.25, 2.15, 9.0])
    assert field_at_distance(np.arange(2), usable, wide, 2.2).tolist() == [2, 3, 4, 6]
