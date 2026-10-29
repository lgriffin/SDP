"""T8: UC-01 end to end on the in-memory kernel with synthetic fields, and the plugin contract.

The planted clusters are SYN-A (Pleiades field, in both synthetic benchmarks), SYN-B (Praesepe
field, in neither) and SYN-C (h and chi Persei field, in hr23 only). Each gate is exercised by
planting the fault it guards against.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from sdp.adapters.bootstrap import clusterers
from sdp.adapters.compute.clustering import SklearnHdbscan
from sdp.kernel.domain.errors import PlanInvalid
from sdp.kernel.domain.experiment import ExperimentState
from sdp.kernel.domain.verdict import VerdictStatus
from sdp.kernel.ports.compute import ClusterLabels
from sdp.kernel.science.plan import Backend
from sdp.testing.contracts.scientific_protocol import ScientificProtocolContract
from sdp_stellar.benchmark import with_metadata
from sdp_stellar.dossier import SENTENCE
from sdp_stellar.protocol import StellarClusterProtocol

from .conftest import QUICK, Run, benchmark, resolved, run_uc01, tables

pytestmark = pytest.mark.tier("T8")

GATES = [f"STL-G-0{i}" for i in range(1, 10)]


@pytest.mark.gate("STL-G-03")
@pytest.mark.gate("STL-G-04")
@pytest.mark.gate("STL-G-05")
@pytest.mark.req("NFR-002")
def test_a_clean_synthetic_run_passes_every_gate(clean_run: Run) -> None:
    assert clean_run.report is not None and clean_run.report.verdict.status is VerdictStatus.VERIFIED
    # Every gate passes on the CPU reference alone (NFR-002): no GPU backend or GPU clusterer took part.
    assert clean_run.manifest["environment"]["backend"] == "cpu"
    assert clean_run.finding("gpu_results_without_agreement") == 0
    assert {g: clean_run.outcome(g) for g in GATES} == dict.fromkeys(GATES, "pass")
    assert clean_run.finding("recovery_reported_benchmarks") == 2
    assert clean_run.finding("null_false_positive_rate") == 0.0
    assert clean_run.finding("injection_curve_cells_missing") == 0


def test_the_planted_clusters_come_out_as_planted(clean_run: Run) -> None:
    dossiers = {d["candidate_id"]: d for d in clean_run.narrative["dossiers"]}
    pleiades = [d for d in dossiers.values() if d["candidate_id"].startswith("pleiades") and d["status"] == "known"]
    assert len(pleiades) == 1 and pleiades[0]["reasons"] == ["matches SYN-A"]
    novel = [d for d in dossiers.values() if d["status"] == "potentially_new"]
    assert [d["candidate_id"][:8] for d in novel] == ["praesepe"]
    assert novel[0]["checks"]["mean_membership"] > 0.8
    assert clean_run.narrative["recovery"]["cga20"]["recovered"] == ["SYN-A"]


@pytest.mark.req("STL-007")
@pytest.mark.gate("STL-G-07")
def test_rejected_candidates_are_retained_with_their_reasons(clean_run: Run) -> None:
    dossiers = clean_run.narrative["dossiers"]
    rejected = [d for d in dossiers if d["status"] == "rejected"]
    assert rejected and all(d["reasons"] for d in rejected)
    assert clean_run.finding("dropped_candidates") == 0
    assert [d["rank"] for d in dossiers] == list(range(1, len(dossiers) + 1))


@pytest.mark.gate("STL-G-09")
def test_every_dossier_states_the_fixed_candidate_sentence(clean_run: Run) -> None:
    for d in clean_run.narrative["dossiers"]:
        assert d["statement"] == SENTENCE.format(candidate_id=d["candidate_id"])
    assert clean_run.narrative["claim_violations"] == []


@pytest.mark.req("STL-003")
def test_the_quality_cuts_and_their_effect_are_in_the_manifest(clean_run: Run) -> None:
    cuts = clean_run.narrative["quality_cuts"]
    assert set(cuts) == {"pleiades", "praesepe", "h-chi-persei", "ngp-control"}
    for report in cuts.values():
        assert report["cuts"]["ruwe_max"] == 1.4
        assert report["kept_rows"] < report["input_rows"]
    # the cuts are predeclared configuration, so the manifest carries them with the frozen predeclaration
    assert clean_run.manifest["intent"]["predeclaration"]["parameters"]["quality_cuts"]["ruwe_max"] == 1.4


@pytest.mark.req("STL-006")
def test_null_and_injection_runs_cover_every_region_with_recorded_seeds(clean_run: Run) -> None:
    injection = clean_run.narrative["injection"]
    assert {i["target_id"] for i in injection} == {"pleiades", "praesepe", "h-chi-persei", "ngp-control"}
    assert len(injection) == 4 * 3 * 3
    assert len({i["seed"] for i in injection}) == len(injection)
    assert clean_run.narrative["null_fields"] == 4 * QUICK["null_fields_per_region"]


@pytest.mark.req("STL-006")
def test_the_same_seed_reproduces_the_findings(clean_run: Run) -> None:
    again = run_uc01()
    assert again.manifest["findings"] == clean_run.manifest["findings"]


@pytest.mark.gate("STL-G-02")
@pytest.mark.gate("STL-G-03")
def test_a_benchmark_without_its_licence_or_release_fails_registration() -> None:
    data = tables()
    data["benchmark:cga20"] = with_metadata(benchmark("cga20"), "cga20:v0", "", "Cantat-Gaudin et al. 2020")
    run = run_uc01(data)
    assert run.outcome("STL-G-02") == "fail"
    problems = run.narrative["benchmark_problems"]
    assert "cga20: no licence" in problems and any("is not the predeclared cga20:v1" in p for p in problems)


@pytest.mark.req("STL-005")
@pytest.mark.gate("STL-G-06")
def test_without_a_second_benchmark_nothing_is_called_potentially_new() -> None:
    run = run_uc01(tables(("hr23",)))
    statuses = {d["candidate_id"]: d for d in run.narrative["dossiers"]}
    assert not [d for d in statuses.values() if d["status"] == "potentially_new"]
    praesepe = [
        d for d in statuses.values() if d["candidate_id"].startswith("praesepe") and d["status"] == "inconclusive"
    ]
    assert praesepe and praesepe[0]["reasons"] == ["cross-matched against fewer than two benchmarks (STL-005)"]
    assert run.outcome("STL-G-06") == "pass"
    assert run.outcome("STL-G-02") == "fail"  # benchmark:cga20 not supplied
    assert run.outcome("STL-G-03") == "fail"


@pytest.mark.gate("STL-G-04")
@pytest.mark.gate("STL-G-02")
def test_a_benchmark_missing_a_column_is_named_not_crashed_on() -> None:
    data = tables()
    data["benchmark:hr23"] = benchmark("hr23").drop_columns(["pm_dec_masyr"])
    run = run_uc01(data)
    assert run.experiment.state is ExperimentState.COMPLETED
    assert run.outcome("STL-G-02") == "fail"
    assert "hr23: table lacks ['pm_dec_masyr']" in run.narrative["benchmark_problems"]


def test_a_null_field_count_of_zero_leaves_the_false_positive_gate_missing() -> None:
    run = run_uc01(params={"null_fields_per_region": 0, "bootstrap_resamples": 50})
    assert run.outcome("STL-G-04") == "missing"
    assert run.report is not None and run.report.verdict.status is not VerdictStatus.VERIFIED


class _Gpu:
    """A stand-in CUDA HDBSCAN under its own id, returning the CPU reference's answer."""

    algorithm_id = "cuml-hdbscan"
    backend = Backend.CUDA

    def __init__(self) -> None:
        self.calls = 0

    def search(self, features: np.ndarray, params: Any, seed: int) -> ClusterLabels:
        self.calls += 1
        result = SklearnHdbscan().search(features, params, seed)
        return ClusterLabels(result.labels, result.probabilities, self.algorithm_id, dict(params))


AGREED = {"algorithm_id": "cuml-hdbscan", "reference_id": "sklearn-hdbscan", "release": "0.1.0", "passed": True}


@pytest.mark.req("STL-004")
@pytest.mark.gate("STL-G-08")
@pytest.mark.parametrize(
    ("record", "release", "why"),
    [
        (None, "0.1.0", "no agreement record"),
        ({**AGREED, "passed": False}, "0.1.0", "did not pass"),
        ({**AGREED, "algorithm_id": "other-gpu"}, "0.1.0", "is for other-gpu"),
        (AGREED, "0.2.0", "not this release '0.2.0'"),
        (AGREED, None, "not this release None"),
    ],
)
def test_a_gpu_result_without_a_binding_agreement_is_excluded(record: Any, release: Any, why: str) -> None:
    gpu = _Gpu()
    services: dict[str, Any] = {"accelerators": {"sklearn-hdbscan": gpu}, "release": release}
    if record is not None:
        services["gpu_agreement"] = {"sklearn-hdbscan": record}
    run = run_uc01(services=services)
    assert gpu.calls == 0
    assert run.outcome("STL-G-08") == "pass"
    [note] = run.narrative["gpu_exclusions"]
    assert note.startswith("sklearn-hdbscan: GPU result (cuml-hdbscan) excluded") and why in note


@pytest.mark.req("STL-004")
@pytest.mark.gate("STL-G-08")
def test_an_agreed_gpu_adapter_stands_in_for_its_cpu_reference(clean_run: Run) -> None:
    gpu = _Gpu()
    agreed = run_uc01(
        services={
            "accelerators": {"sklearn-hdbscan": gpu},
            "gpu_agreement": {"sklearn-hdbscan": AGREED},
            "release": "0.1.0",
        }
    )
    assert gpu.calls > 0 and agreed.narrative["gpu_exclusions"] == []
    assert agreed.outcome("STL-G-08") == "pass"
    assert agreed.manifest["findings"] == clean_run.manifest["findings"]


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"crossmatch_radius_deg": 1.0}, "predeclared and cannot be overridden"),
        ({"bootstrap_resamples": 0}, "bootstrap_resamples must be an integer of at least 1"),
        ({"null_fields_per_region": -1}, "null_fields_per_region must be an integer of at least 0"),
        ({"null_fields_per_region": True}, "null_fields_per_region must be an integer"),
    ],
)
def test_bad_overrides_fail_the_run_at_planning(params: dict[str, Any], message: str) -> None:
    with pytest.raises(PlanInvalid, match=message):
        StellarClusterProtocol().plan(resolved(tables()), params, 1)
    run = run_uc01(params=params)
    assert run.experiment.state is ExperimentState.FAILED
    assert message in str(run.experiment.refusal or run.experiment.failure)


@pytest.mark.req("STL-008")
@pytest.mark.parametrize(
    ("checks", "message"),
    [
        ({"min_members": 10.5, "min_stability": 0.5, "min_cmd_coherence": 3.0}, "min_members must be a whole number"),
        ({"min_members": 0, "min_stability": 0.5, "min_cmd_coherence": 3.0}, "min_members must be a whole number"),
        ({"min_members": True, "min_stability": 0.5, "min_cmd_coherence": 3.0}, "min_members must be a whole number"),
        ({"min_members": 10, "min_stability": "0.5", "min_cmd_coherence": 3.0}, "min_stability must be a finite"),
        ({"min_members": 10, "min_stability": 0.5, "min_cmd_coherence": float("nan")}, "min_cmd_coherence must be"),
        ({"min_members": 10, "min_stability": -0.1, "min_cmd_coherence": 3.0}, "min_stability must be a finite"),
        ({"min_members": 10, "min_stability": 0.5}, r"lacks \['min_cmd_coherence'\]"),
    ],
)
def test_malformed_pass_marks_are_refused_at_planning_not_coerced(
    checks: dict[str, Any], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sdp_stellar import protocol as stellar_protocol

    declared = stellar_protocol.load_predeclaration()
    bad = {**declared, "parameters": {**declared["parameters"], "candidate_checks": checks}}
    monkeypatch.setattr(stellar_protocol, "load_predeclaration", lambda: bad)
    with pytest.raises(PlanInvalid, match=message):
        StellarClusterProtocol().plan(resolved(tables()), {}, 1)


def test_the_plan_has_one_branch_per_region() -> None:
    plan = StellarClusterProtocol().plan(resolved(tables()), QUICK, 1)
    ids = [t.task_id for t in plan.tasks]
    assert ids.count("recovery") == ids.count("synthesis") == 1
    assert sum(i.startswith("search:pleiades:") for i in ids) == 10
    assert sum(i.startswith("null:") for i in ids) == 4 * QUICK["null_fields_per_region"]
    assert plan.outputs == ("recovery", "synthesis")


class TestStellarClusterProtocol(ScientificProtocolContract):
    @pytest.fixture
    def protocol_case(self) -> tuple[Any, ...]:
        return (
            StellarClusterProtocol(),
            resolved(tables()),
            {"null_fields_per_region": 1, "bootstrap_resamples": 20},
            1,
        )

    @pytest.fixture
    def protocol_services(self) -> dict[str, Any]:
        return {"clusterers": clusterers()}


@pytest.mark.req("STL-008")
def test_the_pass_marks_are_predeclared_so_changing_one_changes_the_hash_and_the_outcome(
    clean_run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sdp_stellar import protocol as stellar_protocol

    declared = stellar_protocol.load_predeclaration()
    assert declared["parameters"]["candidate_checks"] == {
        "min_members": 10,
        "min_stability": 0.5,
        "min_cmd_coherence": 3.0,
    }
    strict = {
        **declared,
        "parameters": {
            **declared["parameters"],
            "candidate_checks": {"min_members": 10_000, "min_stability": 1.01, "min_cmd_coherence": 1000.0},
        },
    }
    monkeypatch.setattr(stellar_protocol, "load_predeclaration", lambda: strict)
    assert StellarClusterProtocol().predeclare().compute_hash() != clean_run.experiment.definition.predeclaration_hash
    run = run_uc01()
    statuses = {d["status"] for d in run.narrative["dossiers"]}
    assert statuses == {"rejected"}
    praesepe = next(d for d in run.narrative["dossiers"] if d["candidate_id"].startswith("praesepe"))
    assert any(r.endswith("members at membership >= 0.5, fewer than 10000") for r in praesepe["reasons"])
    assert any(
        r.endswith("< 1.01") and r.startswith("unstable across the grid: stability ") for r in praesepe["reasons"]
    )
    assert any(r.endswith("< 1000.0") for r in praesepe["reasons"])
