"""T4/T8: the registered field tests over the synthetic field: planted signals are recovered,
null fields stay near their nominal rate, and every test runs through the kernel (INV-006)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from sdp.kernel.application.investigate import load_candidate_rankers, load_investigation_tests
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.errors import PlanInvalid
from sdp.kernel.domain.investigation import (
    ArchiveRole,
    ArchiveSelection,
    NullFieldPlan,
    RankingPolicy,
    Stage,
    TestProfile,
    TestResult,
    TestSelection,
)
from sdp.kernel.ports.compute import ClusterLabels
from sdp.kernel.ports.investigate import InvestigateFieldCommand, InvestigationRun
from sdp.kernel.science.investigation import InvestigationTestRegistry
from sdp.kernel.science.plan import Backend, NumericalPolicy, TaskContext
from sdp.testing.fakes.compute import FakeClusterer
from sdp.testing.investigate import (
    ANOMALOUS,
    PLANTED,
    VARIABLE,
    gaia_archive,
    investigate_ports,
    synthetic_catalogue,
    synthetic_field,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.wiring import build_in_memory_kernel
from sdp_investigate import registered_tests
from sdp_investigate.astrometry import AstrometricCoherence, ParallaxConsistency, ra_offset_deg
from sdp_investigate.base import Features
from sdp_investigate.formats import lightcurve_csv, parse_lightcurve, parse_positions, positions_csv
from sdp_investigate.photometry import ColourMagnitudeCoherence, robust_polyfit
from sdp_stellar.ranking import WEIGHTS, StellarCandidateRanker

pytestmark = pytest.mark.tier("T4")

GAIA = {"source_id": "synthetic-gaia"}


def full_profile(trials: int = 5) -> TestProfile:
    base = synthetic_profile()
    tests = (
        TestSelection("parallax-consistency", "1.0.0", GAIA),
        TestSelection("cmd-coherence", "1.0.0", GAIA),
        TestSelection("long-baseline-motion", "1.0.0", GAIA, needs_products=("astrometry",)),
        TestSelection("variability", "1.0.0", {}, needs_products=("lightcurve",)),
        TestSelection("photometric-corroboration", "1.0.0", {**GAIA, "ir_source_id": "synthetic-2mass"}),
        TestSelection(
            "astrometric-coherence",
            "1.0.0",
            {
                **GAIA,
                "clusterer_params": {"eps": 0.6, "min_cluster_size": 5},
                "clusterer_grid": [{"eps": e, "min_cluster_size": 5} for e in (0.5, 0.6, 0.7)],
                "inner_trials": 39,
            },
        ),
    )
    return TestProfile(
        profile_id="field-survey",
        version="1.0.0",
        identify=base.identify,
        archives=(
            *base.archives[:1],
            ArchiveSelection("fake-plates", ArchiveRole.OBSERVATIONS, "dasch:DR7", match_radius_arcsec=2.0),
        ),
        tests=tests,
        ranking=RankingPolicy("significance", "1.0.0", threshold=0.3, alpha=0.05),
        null_fields=NullFieldPlan(trials=trials),
    )


@dataclass
class Survey:
    run: InvestigationRun
    all_results: tuple[TestResult, ...]

    def results(self, test: str) -> dict[str, TestResult]:
        """Every result of one test, opened as a candidate or not."""
        return {r.subject.key: r for r in self.all_results if r.test_name == test}


@pytest.fixture(scope="module")
def survey() -> Survey:
    catalogue = synthetic_catalogue()
    k = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=investigate_ports(*registered_tests(), catalogue=catalogue),
    )
    assert k.kernel.investigate_field is not None
    k.kernel.investigate_field.ports.services = {"clusterer": FakeClusterer()}
    run = k.kernel.investigate_field.execute(InvestigateFieldCommand(synthetic_field(), full_profile(), seed=5))
    stage = run.investigation_set.stages[Stage.TEST].artifact_id
    assert stage is not None
    results = decode_artifact(k.store.get(stage), JSON)["results"]
    return Survey(run, tuple(TestResult.from_document(r) for r in results))


@pytest.mark.req("INV-006")
def test_every_test_is_registered_through_the_entry_point_with_a_cpu_reference_and_null_model() -> None:
    registry = load_investigation_tests()
    expected = {f"{t.name}@{t.version}" for t in registered_tests()}
    assert expected <= set(registry.keys())
    kinds = registry.task_kinds()
    for t in registered_tests():
        assert t.null_model.strip()
        assert kinds.has_cpu_reference(f"investigate.{t.name}@{t.version}")
        assert kinds.has_cpu_reference(f"investigate.{t.name}@{t.version}.null")


@pytest.mark.req("INV-007")
def test_the_survey_runs_every_test_and_records_every_null(survey: Survey) -> None:
    s = survey.run.investigation_set
    assert {n.test_name for n in s.null_results} == {t.name for t in full_profile().tests}
    assert survey.run.manifest["multiplicity"]["tests_applied"] == 6
    assert survey.run.manifest["multiplicity"]["comparisons"] == 60 * 6


def pv(result: TestResult) -> float:
    assert result.p_value is not None
    return result.p_value


@pytest.mark.scientific
def test_variability_finds_the_variable_plate_star(survey: Survey) -> None:
    results = survey.results("variability")
    assert pv(results[VARIABLE]) < 1e-6
    assert abs(results[VARIABLE].details["ls_period_days"] - 6000.0) < 300
    quiet = [results[k].p_value for k in PLANTED if k != VARIABLE and k in results]
    assert all(p is not None and p > 1e-3 for p in quiet)


@pytest.mark.scientific
def test_long_baseline_motion_finds_the_position_history_that_disagrees(survey: Survey) -> None:
    results = survey.results("long-baseline-motion")
    assert pv(results[ANOMALOUS]) < 1e-6
    assert results[ANOMALOUS].details["baseline_years"] == pytest.approx(95.0)
    others = [results[k].p_value for k in PLANTED if k != ANOMALOUS and k in results]
    assert others and all(p is not None and p > 1e-3 for p in others)


@pytest.mark.scientific
def test_parallax_and_infrared_tests_single_out_the_planted_stars(survey: Survey) -> None:
    plx = survey.results("parallax-consistency")
    assert all(pv(plx[k]) < 1e-3 for k in PLANTED)
    ir = survey.results("photometric-corroboration")
    assert pv(ir["gaia:DR3:4"]) < 1e-6


@pytest.mark.scientific
def test_astrometric_coherence_opens_one_investigation_for_the_group(survey: Survey) -> None:
    structures = [i for i in survey.run.investigations if i.candidate.structure_id is not None]
    assert len(structures) == 1
    group = structures[0]
    assert set(group.candidate.members) == set(PLANTED)
    assert len(group.evidence_bundle_ids) == 5  # one investigation, five members, not five investigations
    result = group.test_results[0]
    assert result.p_value is not None and result.p_value < 0.05
    field_blobs = [
        r for r in survey.results("astrometric-coherence").values() if set(r.subject.members) != set(PLANTED)
    ]
    assert all(r.p_value is not None and r.p_value >= 0.05 for r in field_blobs)


@pytest.mark.scientific
def test_the_stellar_ranker_puts_the_co_moving_group_above_the_field_blobs(survey: Survey) -> None:
    group = next(r for r in survey.results("astrometric-coherence").values() if set(r.subject.members) == set(PLANTED))
    assert group.details["stability"] is not None and 0.0 < group.details["stability"] <= 1.0
    assert group.details["cmd_coherence"] is None  # five members: below the stellar module's minimum with a colour
    policy = RankingPolicy("stellar", "1.0.0", threshold=0.3, alpha=0.05)
    ranked = StellarCandidateRanker().rank(survey.all_results, policy)
    structures = [c for c in ranked if c.subject.structure_id is not None]
    assert set(structures[0].subject.members) == set(PLANTED)
    membership = float(np.mean(group.subject.membership))
    assert structures[0].score == pytest.approx(
        (WEIGHTS["membership"] * membership + WEIGHTS["stability"] * group.details["stability"])
        / (WEIGHTS["membership"] + WEIGHTS["stability"])
    )
    assert all(c.score == 0.0 for c in structures[1:])  # not significant against their null fields
    assert "stellar" in load_candidate_rankers()


@pytest.mark.req("INV-006")
@pytest.mark.parametrize("test", [ParallaxConsistency(), ColourMagnitudeCoherence()])
def test_null_fields_stay_near_the_nominal_false_positive_rate(test: ParallaxConsistency) -> None:
    rng = np.random.default_rng(3)
    n = 400
    feats = Features(
        tuple(f"k{i}" for i in range(n)),
        {
            "plx": rng.normal(1.0, 0.5, n),
            "err": np.full(n, 0.05),
            "colour": rng.uniform(0.5, 2.0, n),
            "absmag": 4.0 + 3.0 * rng.uniform(0.5, 2.0, n) + rng.normal(0, 0.3, n),
        },
    )
    alpha = 0.05
    rates = []
    for _ in range(10):
        scored = test.evaluate(test.null_features(feats, {}, rng), {}, rng)
        rates.append(sum(1 for s in scored if s.p_value is not None and s.p_value < alpha) / len(scored))
    assert 0.0 <= float(np.mean(rates)) <= 2.5 * alpha


def test_products_round_trip_through_the_canonical_formats() -> None:
    lc = parse_lightcurve(lightcurve_csv(np.array([2.0, 1.0]), np.array([10.5, 10.0]), np.array([0.1, 0.1])))
    assert lc.mjd.tolist() == [1.0, 2.0] and lc.mag.tolist() == [10.0, 10.5]
    pos = parse_positions(positions_csv(np.array([1900.0]), np.array([1.0]), np.array([2.0]), np.array([100.0])))
    assert pos.epoch_jyear.tolist() == [1900.0]
    with pytest.raises(ValueError, match="lacks columns"):
        parse_lightcurve(b"mjd,mag\n1,2\n")


def test_robust_fit_ignores_outliers() -> None:
    x = np.linspace(0, 1, 50)
    y = 2 * x + 1
    y[::10] += 5
    coeffs = robust_polyfit(x, y, 1)
    assert coeffs == pytest.approx([2.0, 1.0], abs=1e-6)


def test_motion_residuals_take_right_ascension_the_shortest_way_round() -> None:
    offsets = ra_offset_deg(np.array([359.9999, 0.0001, 10.0]), np.array([0.0001, 359.9999, 9.0]))
    assert offsets == pytest.approx([-0.0002, 0.0002, 1.0])


class CountingClusterer(FakeClusterer):
    calls: int = 0

    def search(self, features: np.ndarray, params: Mapping[str, Any], seed: int) -> ClusterLabels:
        type(self).calls += 1
        return super().search(features, params, seed)


def test_null_fields_skip_the_grid_and_a_long_grid_is_refused_up_front() -> None:
    test = AstrometricCoherence()
    rng = np.random.default_rng(1)
    x = np.vstack([rng.normal(0, 1, (40, 3)), rng.normal(6, 0.05, (6, 3))])
    feats = Features(
        tuple(f"k{i}" for i in range(len(x))),
        {
            "x": x,
            "g": np.zeros(len(x)),
            "g_mag": np.full(len(x), np.nan),
            "bp_rp": np.full(len(x), np.nan),
            "clusterer": CountingClusterer(),
        },
    )
    grid = [{"eps": 0.5, "min_cluster_size": 4}] * 3
    thresholds = {"clusterer_params": {"eps": 0.5, "min_cluster_size": 4}, "clusterer_grid": grid, "inner_trials": 2}
    CountingClusterer.calls = 0
    scored = test.evaluate(feats, thresholds, rng)
    assert scored and scored[0].details["stability"] == 1.0
    assert CountingClusterer.calls == 1 + 3 + 2  # the reference, the grid, the inner null fields
    CountingClusterer.calls = 0
    nulls = test.evaluate(test.null_features(feats, thresholds, rng), thresholds, rng)
    assert CountingClusterer.calls <= 1 + 2 and all("stability" not in s.details for s in nulls)  # never the grid
    registry = InvestigationTestRegistry.of(test)
    too_long = TestSelection("astrometric-coherence", "1.0.0", {"clusterer_grid": [{}] * (test.MAX_GRID + 1)})
    with pytest.raises(PlanInvalid, match="at most 12"):
        registry.validate([too_long])


def test_a_profile_names_another_installed_clusterer_and_an_unknown_one_is_refused() -> None:
    test = AstrometricCoherence()
    reference, fast = FakeClusterer(), FakeClusterer(algorithm_id="fast-planted")
    services = {"clusterer": reference, "clusterers": {"fast-planted": fast}}
    context = TaskContext("t", 1, NumericalPolicy(), Backend.CPU, services)
    assert test.features([], {}, context).arrays["clusterer"] is reference  # UC-01's reference by default
    assert test.features([], {"clusterer": "fake-planted"}, context).arrays["clusterer"] is reference
    assert test.features([], {"clusterer": "fast-planted"}, context).arrays["clusterer"] is fast
    with pytest.raises(ValueError, match="'cuml-hdbscan' is not installed; choose one of: fake-planted, fast-planted"):
        test.features([], {"clusterer": "cuml-hdbscan"}, context)
    registry = InvestigationTestRegistry.of(test)
    with pytest.raises(PlanInvalid, match="clusterer is an algorithm id"):
        registry.validate([TestSelection("astrometric-coherence", "1.0.0", {"clusterer": 3})])


def test_every_cluster_records_the_algorithm_the_profile_chose() -> None:
    test = AstrometricCoherence()
    rng = np.random.default_rng(2)
    x = np.vstack([rng.normal(0, 1, (40, 3)), rng.normal(6, 0.05, (6, 3))])
    arrays = {"x": x, "g": np.zeros(len(x)), "g_mag": np.full(len(x), np.nan), "bp_rp": np.full(len(x), np.nan)}
    feats = Features(
        tuple(f"k{i}" for i in range(len(x))), {**arrays, "clusterer": FakeClusterer(algorithm_id="fast-planted")}
    )
    thresholds = {
        "clusterer": "fast-planted",
        "clusterer_params": {"eps": 0.5, "min_cluster_size": 4},
        "inner_trials": 2,
    }
    scored = test.evaluate(feats, thresholds, rng)
    assert scored and all(s.details["algorithm"] == "fast-planted" for s in scored)
