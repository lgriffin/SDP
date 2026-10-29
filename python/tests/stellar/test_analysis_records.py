"""The exact records of the UC-01 TaskKinds on hand-built inputs: GPU exclusion, the clusterer choice,
candidate classification at every pass mark, the null test, recovery rates and the synthesis."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.ports.compute import ClusterLabels
from sdp.kernel.science.plan import Backend, TaskContext
from sdp_stellar import analysis, cmd
from sdp_stellar import benchmark as bm
from sdp_stellar.features import apply_policy
from sdp_stellar.membership import Candidate
from sdp_stellar.null import null_field

pytestmark = pytest.mark.tier("T1")


def _ctx(seed: int = 7, **services: Any) -> TaskContext:
    return TaskContext("t", seed, NumericalPolicy(), Backend.CPU, services)


class _Fixed:
    """A clusterer that labels the first ``k`` rows cluster 0 and records each call."""

    def __init__(self, algorithm_id: str, k: int = 10, backend: str | None = None) -> None:
        self.algorithm_id = algorithm_id
        self.k = k
        self.calls: list[tuple[np.ndarray, dict[str, Any], int]] = []
        if backend is not None:
            self.backend = backend

    def search(self, values: np.ndarray, params: Mapping[str, Any], seed: int) -> ClusterLabels:
        self.calls.append((values, dict(params), seed))
        labels = np.where(np.arange(len(values)) < self.k, 0, -1)
        return ClusterLabels(labels, np.where(labels == 0, 1.0, 0.0), self.algorithm_id, params)


class _Tail(_Fixed):
    """A clusterer that labels the last ``k`` rows cluster 0: the rows an injection appends."""

    def search(self, values: np.ndarray, params: Mapping[str, Any], seed: int) -> ClusterLabels:
        self.calls.append((values, dict(params), seed))
        labels = np.where(np.arange(len(values)) >= len(values) - self.k, 0, -1)
        return ClusterLabels(labels, np.where(labels == 0, 1.0, 0.0), self.algorithm_id, params)


class _Bare:
    """A clusterer with no backend attribute at all."""

    def __init__(self, algorithm_id: str) -> None:
        self.algorithm_id = algorithm_id


# --- GPU exclusion and the clusterer choice ----------------------------------------------------


def test_each_reason_a_gpu_result_is_excluded() -> None:
    ok = {"passed": True, "algorithm_id": "cu", "reference_id": "sk", "release": "r1"}
    assert analysis.gpu_exclusion(None, "cu", "sk", "r1") == "no agreement record"
    assert analysis.gpu_exclusion({}, "cu", "sk", "r1") == "no agreement record"
    assert analysis.gpu_exclusion({**ok, "passed": False}, "cu", "sk", "r1") == "its agreement test did not pass"
    assert analysis.gpu_exclusion({**ok, "reference_id": "db"}, "cu", "sk", "r1") == (
        "the agreement record is for cu against db"
    )
    assert analysis.gpu_exclusion({**ok, "algorithm_id": "xx"}, "cu", "sk", "r1") == (
        "the agreement record is for xx against sk"
    )
    assert analysis.gpu_exclusion(ok, "cu", "sk", "r2") == (
        "the agreement record is from release 'r1', not this release 'r2'"
    )
    assert analysis.gpu_exclusion(ok, "cu", "sk", None) == (
        "the agreement record is from release 'r1', not this release None"
    )
    assert analysis.gpu_exclusion(ok, "cu", "sk", "r1") is None


def test_an_agreed_accelerator_stands_in_and_an_unagreed_one_is_noted() -> None:
    cpu, gpu = _Fixed("sk", backend="cpu"), _Fixed("cu", backend="cuda-12")
    agreed = {"sk": {"passed": True, "algorithm_id": "cu", "reference_id": "sk", "release": "r1"}}
    ctx = _ctx(clusterers={"sk": cpu}, accelerators={"sk": gpu}, gpu_agreement=agreed, release="r1")
    assert analysis._clusterer(ctx, "sk") == (gpu, "cuda-12", None)
    bare_gpu = _Bare("cu")
    ctx = _ctx(clusterers={"sk": cpu}, accelerators={"sk": bare_gpu}, gpu_agreement=agreed, release="r1")
    assert analysis._clusterer(ctx, "sk") == (bare_gpu, "cuda", None)
    ctx = _ctx(clusterers={"sk": cpu}, accelerators={"sk": gpu}, release="r1")
    assert analysis._clusterer(ctx, "sk") == (cpu, "cpu", "sk: GPU result (cu) excluded: no agreement record")


def test_without_accelerators_the_cpu_reference_runs_with_no_note() -> None:
    bare = _Bare("sk")
    assert analysis._clusterer(_ctx(clusterers={"sk": bare}), "sk") == (bare, "cpu", None)
    other = _Fixed("db", backend="cpu")
    ctx = _ctx(clusterers={"sk": bare}, accelerators={"db": other})
    assert analysis._clusterer(ctx, "sk")[2] is None


# --- pass marks --------------------------------------------------------------------------------


def test_the_pass_marks_accept_their_lowest_values_and_refuse_below() -> None:
    low = analysis.CandidateChecks.from_parameters(
        {"min_members": 1, "min_stability": 0, "min_cmd_coherence": 0.0, "max_field_fraction": 0}
    )
    assert (low.min_members, low.min_stability, low.min_cmd_coherence, low.max_field_fraction) == (1, 0.0, 0.0, 0.0)
    with pytest.raises(
        ValueError, match=r"^candidate_checks lacks \['max_field_fraction', 'min_cmd_coherence', 'min_stability'\]$"
    ):
        analysis.CandidateChecks.from_parameters({"min_members": 1})
    with pytest.raises(
        ValueError, match=r"^candidate_checks\.min_members must be a whole number of at least 1, got 0$"
    ):
        analysis.CandidateChecks.from_parameters(
            {"min_members": 0, "min_stability": 0, "min_cmd_coherence": 0, "max_field_fraction": 1}
        )
    with pytest.raises(
        ValueError, match=r"^candidate_checks\.min_stability must be a finite number of at least 0, got -0\.1$"
    ):
        analysis.CandidateChecks.from_parameters(
            {"min_members": 1, "min_stability": -0.1, "min_cmd_coherence": 0, "max_field_fraction": 1}
        )


# --- classification ----------------------------------------------------------------------------


def _region_table(n_members: int = 12, n_field: int = 48) -> pa.Table:
    rng = np.random.default_rng(3)
    n = n_members + n_field
    colour = rng.uniform(0.4, 2.2, n)
    g = 10 + 3 * colour + rng.normal(0, 1.5, n)
    g[:n_members] = 10 + 3 * colour[:n_members] + rng.normal(0, 0.02, n_members)
    return pa.table(
        {
            "source_key": [f"s{i}" for i in range(n)],
            "ra_deg": np.r_[np.full(n_members, 10.0), rng.uniform(9, 11, n_field)],
            "dec_deg": np.r_[np.full(n_members, 20.0), rng.uniform(19, 21, n_field)],
            "pm_ra_cosdec_masyr": np.r_[np.full(n_members, 5.0), rng.normal(0, 5, n_field)],
            "pm_dec_masyr": np.r_[np.full(n_members, -3.0), rng.normal(0, 5, n_field)],
            "parallax_mas": np.r_[np.full(n_members, 2.0), rng.uniform(0.5, 3, n_field)],
            "parallax_mas_err": np.full(n, 0.01),
            "g_mag": g,
            "bp_rp": colour,
            "ruwe": np.ones(n),
            "pm_ra_cosdec_masyr_err": np.full(n, 0.01),
            "pm_dec_masyr_err": np.full(n, 0.01),
        }
    )


CHECKS = {"min_members": 12, "min_stability": 0.5, "min_cmd_coherence": 1.5, "max_field_fraction": 0.2}
PARAMS: dict[str, Any] = {"candidate_checks": CHECKS, "crossmatch_radius_deg": 0.1, "pm_window_masyr": 1.0}


def _candidate(members: int = 12, stability: float = 0.5, reference_size: int = 14) -> Candidate:
    return Candidate("r-c003", np.arange(members), np.full(members, 0.9), stability, reference_size)


def _arrays(table: pa.Table) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray(table["bp_rp"]), cmd.absolute_g(np.asarray(table["g_mag"]), np.asarray(table["parallax_mas"]))


def _bench(benchmark_id: str, *rows: tuple[str, float, float, float, float]) -> bm.Benchmark:
    names = ("name", "ra_deg", "dec_deg", "pm_ra_cosdec_masyr", "pm_dec_masyr")
    table = pa.table(
        {k: pa.array([r[i] for r in rows], type=pa.string() if i == 0 else pa.float64()) for i, k in enumerate(names)}
    )
    return bm.Benchmark(benchmark_id, "v1", "CC0", "c", table)


def test_a_candidate_at_every_pass_mark_is_cross_matched_and_known_by_name() -> None:
    table = _region_table()
    colour, mag = _arrays(table)
    coh = cmd.coherence(np.arange(12), colour, mag, 5)
    assert coh is not None and coh > 1.5
    benchmarks = [_bench("a", ("Zed", 10.0, 20.0, 5.0, -3.0), ("Alf", 10.0, 20.0, 5.5, -3.0)), _bench("b")]
    out = analysis._classify(_candidate(), table, colour, mag, benchmarks, PARAMS, 5)
    centre = bm.centre(table, np.arange(12))
    assert out == {
        "candidate_id": "r-c003",
        "status": "known",
        "reasons": ["matches Alf, Zed"],
        "n_members": 12,
        "reference_size": 14,
        "mean_membership": pytest.approx(0.9),
        "stability": 0.5,
        "cmd_coherence": coh,
        "centre": centre,
        "crossmatch": [bm.cross_match(centre, b, 0.1, 1.0) for b in benchmarks],
        "member_keys": [f"s{i}" for i in range(12)],
    }


def test_a_candidate_matching_nothing_is_potentially_new_only_against_two_benchmarks() -> None:
    table = _region_table()
    colour, mag = _arrays(table)
    two = analysis._classify(_candidate(), table, colour, mag, [_bench("hr23"), _bench("cga20")], PARAMS, 5)
    assert (two["status"], two["reasons"]) == (
        "potentially_new",
        ["passed every check and matches no cluster in hr23 or cga20"],
    )
    one = analysis._classify(_candidate(), table, colour, mag, [_bench("hr23")], PARAMS, 5)
    assert (one["status"], one["reasons"]) == (
        "inconclusive",
        ["cross-matched against fewer than two benchmarks (STL-005)"],
    )
    assert [x["benchmark"] for x in one["crossmatch"]] == ["hr23"]


def test_a_candidate_below_each_pass_mark_is_rejected_with_each_reason() -> None:
    table = _region_table()
    colour, mag = _arrays(table)
    params = {**PARAMS, "candidate_checks": {**CHECKS, "min_cmd_coherence": 1e9}}
    coh = cmd.coherence(np.arange(11), colour, mag, 5)
    out = analysis._classify(_candidate(11, 0.49), table, colour, mag, [_bench("a"), _bench("b")], params, 5)
    assert out["status"] == "rejected" and out["crossmatch"] == []
    assert out["reasons"] == [
        "unstable across the grid: stability 0.49 < 0.5",
        "11 members at membership >= 0.5, fewer than 12",
        f"colour-magnitude sequence no tighter than the field: coherence {coh:.2f} < 1000000000.0",
    ]
    exact = {
        **PARAMS,
        "candidate_checks": {**CHECKS, "min_cmd_coherence": cmd.coherence(np.arange(12), colour, mag, 5)},
    }
    assert analysis._classify(_candidate(), table, colour, mag, [], exact, 5)["status"] == "inconclusive"


def test_a_candidate_holding_more_than_the_field_fraction_is_the_field_itself() -> None:
    table = _region_table()
    colour, mag = _arrays(table)
    at_mark = analysis._classify(_candidate(), table, colour, mag, [], PARAMS, 5)  # 12 of 60 is exactly 0.2
    assert at_mark["status"] == "inconclusive"
    tighter = {**PARAMS, "candidate_checks": {**CHECKS, "max_field_fraction": 0.19}}
    out = analysis._classify(_candidate(), table, colour, mag, [], tighter, 5)
    assert (out["status"], out["reasons"]) == (
        "rejected",
        ["12 members are more than 0.19 of the 60 stars searched: the field itself, not a cluster"],
    )


def test_a_candidate_without_colours_is_inconclusive_and_an_empty_one_has_no_centre() -> None:
    table = _region_table()
    colour, mag = _arrays(table)
    colour = colour.copy()
    colour[:12] = np.nan
    out = analysis._classify(_candidate(), table, colour, mag, [_bench("a"), _bench("b")], PARAMS, 5)
    assert (out["status"], out["reasons"], out["cmd_coherence"], out["crossmatch"]) == (
        "inconclusive",
        ["too few members with a colour for the CMD check"],
        None,
        [],
    )
    empty = analysis._classify(
        _candidate(0), table, colour, mag, [], {**PARAMS, "candidate_checks": {**CHECKS, "min_members": 1}}, 5
    )
    assert (empty["centre"], empty["cmd_coherence"], empty["member_keys"], empty["status"]) == (
        None,
        None,
        [],
        "rejected",
    )


# --- the candidate, null and injection tasks ---------------------------------------------------


def _prepared(table: pa.Table) -> dict[str, Any]:
    region = {"ra_deg": 10.0, "dec_deg": 20.0, "radius_deg": 1.0}
    fm = apply_policy("fsp-v1", table, 10.0, 20.0)
    return {
        "region": region,
        "cuts": {"kept_rows": table.num_rows},
        "table": table,
        "features": analysis._features_doc(fm),
    }


GRID: dict[str, Any] = {"algorithms": ["sk"], "hyperparameter_grid": {"sk": {"m": [3]}}}


def test_the_candidate_task_assesses_each_labelling_and_strips_the_arrays() -> None:
    table = _region_table()
    labelling = {
        "config": "sk|m=3",
        "labels": np.where(np.arange(60) < 12, 0, -1),
        "probabilities": np.where(np.arange(60) < 12, 1.0, 0.0),
        "n_clusters": 1,
        "backend": "cpu",
    }
    upstream = {"prep": _prepared(table), "s0": labelling}
    params = {**PARAMS, "prepare": "prep", "searches": ["s0"], "target_id": "r", "benchmark_roles": []}
    out = analysis.candidates(params, upstream, _ctx(5, inputs={}))
    assert out["target_id"] == "r" and out["reference_clusters"] == 1 and out["cuts"] == {"kept_rows": 60}
    assert out["labellings"] == [{"config": "sk|m=3", "n_clusters": 1, "backend": "cpu"}]
    (c,) = out["candidates"]
    assert c["candidate_id"] == "r-c000" and c["cmd_coherence"] == cmd.coherence(np.arange(12), *_arrays(table), 5)
    empty = analysis.candidates({**params, "searches": []}, upstream, _ctx(5, inputs={}))
    assert (empty["reference_clusters"], empty["candidates"], empty["labellings"]) == (0, [], [])


def test_the_null_test_reruns_the_pipeline_on_the_shuffled_field() -> None:
    table = _region_table()
    clusterer = _Fixed("sk", k=12, backend="cpu")
    params = {**PARAMS, **GRID, "prepare": "prep", "target_id": "r", "feature_space_policy": "fsp-v1"}
    out = analysis.null_test(params, {"prep": _prepared(table)}, _ctx(9, clusterers={"sk": clusterer}))
    # twelve unshuffled-looking rows survive as one inconclusive candidate: no benchmarks are offered
    assert out == {"target_id": "r", "seed": 9, "clusters": 1, "survivors": ["r-null-c000"]}
    ((values, grid_params, seed),) = clusterer.calls
    assert seed == 9 and grid_params == {"m": 3}
    assert np.array_equal(values, apply_policy("fsp-v1", null_field(table, 9), 10.0, 20.0).values)
    strict = {**params, "candidate_checks": {**CHECKS, "min_members": 13}}
    assert (
        analysis.null_test(strict, {"prep": _prepared(table)}, _ctx(9, clusterers={"sk": clusterer}))["survivors"] == []
    )


def test_the_injection_task_reports_its_cell_and_recovery() -> None:
    table = _region_table()
    clusterer = _Fixed("sk", k=0)
    params = {
        **GRID,
        "prepare": "prep",
        "target_id": "r",
        "feature_space_policy": "fsp-v1",
        "richness": "20",
        "distance_pc": "500",
        "pm_dispersion_masyr": 0.2,
    }
    out = analysis.injection(params, {"prep": _prepared(table)}, _ctx(4, clusterers={"sk": clusterer}))
    assert out == {
        "target_id": "r",
        "richness": 20,
        "distance_pc": 500.0,
        "seed": 4,
        "recovered": False,
        "completeness": 0.0,
        "purity": 0.0,
    }
    assert clusterer.calls[0][2] == 4 and len(clusterer.calls[0][0]) == 80
    found = analysis.injection(params, {"prep": _prepared(table)}, _ctx(4, clusterers={"sk": _Tail("sk", k=20)}))
    assert (found["recovered"], found["completeness"], found["purity"]) == (True, 1.0, 1.0)
    half = analysis.injection(params, {"prep": _prepared(table)}, _ctx(4, clusterers={"sk": _Tail("sk", k=40)}))
    assert (half["recovered"], half["completeness"], half["purity"]) == (True, 1.0, 0.5)


# --- recovery rates ----------------------------------------------------------------------------


def test_a_bootstrap_interval_is_seeded_and_absent_without_clusters() -> None:
    hits = np.array([1.0, 0.0, 1.0, 1.0])
    rng = np.random.default_rng(3)
    rates = [float(np.mean(rng.choice(hits, 4, replace=True))) for _ in range(50)]
    assert analysis._interval(hits, 50, 3) == [float(np.quantile(rates, 0.025)), float(np.quantile(rates, 0.975))]
    assert analysis._interval(np.array([]), 50, 3) is None
    assert analysis._interval(np.array([1.0]), 10, 3) == [1.0, 1.0]


def test_recovery_rates_count_known_and_new_candidates_with_centres_per_benchmark() -> None:
    table = bm.with_metadata(
        _bench("x", ("A", 10.0, 20.0, 5.0, -3.0), ("B", 10.5, 20.0, 0.0, 0.0), ("C", 10.0, 20.5, 9.0, 9.0)).table,
        "rel-1",
        "CC0",
        "c",
    )
    centre = {"ra_deg": 10.0, "dec_deg": 20.0, "pm_ra_cosdec_masyr": 5.0, "pm_dec_masyr": -3.0}
    near_b = {"ra_deg": 10.5, "dec_deg": 20.0, "pm_ra_cosdec_masyr": 0.0, "pm_dec_masyr": 0.0}
    near_c = {"ra_deg": 10.0, "dec_deg": 20.5, "pm_ra_cosdec_masyr": 9.0, "pm_dec_masyr": 9.0}
    upstream = {
        "cand": {
            "target_id": "r",
            "candidates": [
                {"centre": centre, "status": "known"},
                {"centre": near_b, "status": "rejected"},
                {"centre": near_c, "status": "potentially_new"},
                {"centre": None, "status": "known"},
            ],
        }
    }
    params = {
        "benchmark_roles": ["benchmark:hr23", "benchmark:gone"],
        "regions": [{"target_id": "r", "ra_deg": 10.0, "dec_deg": 20.0, "radius_deg": 1.0}],
        "candidate_tasks": ["cand"],
        "crossmatch_radius_deg": 0.1,
        "pm_window_masyr": 1.0,
        "bootstrap_resamples": 20,
    }
    out = analysis.recovery_rates(params, upstream, _ctx(2, inputs={"benchmark:hr23": table}))
    arr = np.array([1.0, 0.0, 1.0])
    assert out == {
        "hr23": {
            "release": "rel-1",
            "clusters_in_regions": 3,
            "recovered": ["A", "C"],
            "missed": ["B"],
            "rate": pytest.approx(2 / 3),
            "interval_95": analysis._interval(arr, 20, 2),
        }
    }
    far = {**params, "regions": [{"target_id": "r", "ra_deg": 100.0, "dec_deg": 20.0, "radius_deg": 1.0}]}
    assert analysis.recovery_rates(far, upstream, _ctx(2, inputs={"benchmark:hr23": table}))["hr23"] == {
        "release": "rel-1",
        "clusters_in_regions": 0,
        "recovered": [],
        "missed": [],
        "rate": None,
        "interval_95": None,
    }


def test_a_benchmark_is_offered_only_when_supplied_with_every_column() -> None:
    full = bm.with_metadata(_bench("x", ("A", 1.0, 2.0, 3.0, 4.0)).table, "r", "l", "c")
    partial = pa.table({"name": ["A"]})
    ctx = _ctx(inputs={"benchmark:a:b": full, "benchmark:p": partial})
    (b,) = analysis._benchmarks(ctx, ["benchmark:a:b", "benchmark:p", "benchmark:none"])
    assert (b.benchmark_id, b.release) == ("a:b", "r")


def test_recovering_a_benchmark_cluster_keeps_the_benchmark_identity() -> None:
    bench = _bench("hr23", ("A", 10.0, 20.0, 5.0, -3.0), ("B", 10.0, 20.0, 50.0, 50.0))
    centre = {"ra_deg": 10.0, "dec_deg": 20.0, "pm_ra_cosdec_masyr": 5.0, "pm_dec_masyr": -3.0}
    region = {"ra_deg": 10.0, "dec_deg": 20.0, "radius_deg": 1.0}
    assert bm.recovered(bench, [centre], region, 0.1, 1.0) == {"A": True, "B": False}
    moving = {**centre, "pm_ra_cosdec_masyr": -5.0, "pm_dec_masyr": 3.0}
    assert bm.cross_match(moving, bench, 0.1, 1.0)["matches"] == []


# --- region evidence and the synthesis ---------------------------------------------------------


def _cand(cid: str, status: str, crossmatch: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "candidate_id": cid,
        "status": status,
        "reasons": ["r0"],
        "n_members": 20,
        "mean_membership": 0.9,
        "stability": 0.8,
        "cmd_coherence": 3.0,
        "crossmatch": crossmatch or [],
        "centre": None,
    }


def test_a_new_candidate_carries_its_region_evidence_and_needs_clean_null_fields() -> None:
    nulls = [
        {"target_id": "r", "survivors": []},
        {"target_id": "r", "survivors": ["x"]},
        {"target_id": "s", "survivors": ["y"]},
    ]
    injections = [
        {"target_id": "r", "recovered": True},
        {"target_id": "r", "recovered": False},
        {"target_id": "s", "recovered": True},
    ]
    out = analysis._with_region_evidence(_cand("r-c001", "potentially_new"), nulls, injections)
    assert out["evidence"] == {
        "null_fields": 2,
        "null_fields_with_survivors": 1,
        "injections": 2,
        "injections_recovered": 1,
    }
    assert (out["status"], out["reasons"]) == (
        "inconclusive",
        ["r0", "1 of 2 null fields of this region produced surviving candidates"],
    )
    clean = analysis._with_region_evidence(
        _cand("s-x-c002", "potentially_new"), [{"target_id": "s-x", "survivors": []}], []
    )
    assert (clean["status"], clean["reasons"]) == ("potentially_new", ["r0"])
    alone = analysis._with_region_evidence(_cand("t-c000", "potentially_new"), nulls, injections)
    assert (alone["status"], alone["reasons"]) == ("inconclusive", ["r0", "no null field was run for this region"])
    assert alone["evidence"] == {
        "null_fields": 0,
        "null_fields_with_survivors": 0,
        "injections": 0,
        "injections_recovered": 0,
    }
    known = _cand("r-c004", "known")
    kept = analysis._with_region_evidence(known, nulls, injections)
    assert (
        (kept["status"], kept["reasons"]) == ("known", ["r0"])
        and known["reasons"] == ["r0"]
        and "evidence" not in known
    )


def test_the_synthesis_records_every_problem_and_rate() -> None:
    bench = bm.with_metadata(_bench("x").table, "hr23:v1", "CC0", "c")
    two = [{"benchmark": "hr23"}, {"benchmark": "cga20"}]
    upstream = {
        "cand-r": {
            "target_id": "r",
            "candidates": [
                _cand("r-c000", "potentially_new", two),
                _cand("r-c001", "potentially_new", [{"benchmark": "hr23"}]),
            ],
            "reference_clusters": 3,
            "labellings": [
                {
                    "config": "cu|m=1",
                    "backend": "cuda",
                    "algorithm_id": "sk",
                    "implementation_id": "cu",
                    "excluded_gpu": None,
                },
                {
                    "config": "sk|m=1",
                    "backend": "cpu",
                    "algorithm_id": "sk",
                    "implementation_id": "sk",
                    "excluded_gpu": "sk: note",
                },
            ],
            "cuts": {"kept_rows": 5},
        },
        "cand-s": {
            "target_id": "s",
            "candidates": [],
            "reference_clusters": 1,
            "labellings": [],
            "cuts": {"kept_rows": 2},
        },
        "null-r": {"target_id": "r", "survivors": []},
        "null-s": {"target_id": "s", "survivors": ["s-null-c000"]},
        "null-s2": {"target_id": "s", "survivors": []},
        "inj-r": {"target_id": "r", "richness": 50, "distance_pc": 1000.0, "recovered": True},
        "recovery": {"hr23": {"interval_95": [0.5, 1.0]}, "cga20": {"interval_95": None}},
    }
    params = {
        "benchmark_roles": ["benchmark:hr23", "benchmark:cga20"],
        "benchmark_labels": ["hr23:v1", "cga20:v1"],
        "regions": [{"target_id": "r", "reason": "why"}, {"target_id": "s", "reason": "  "}, {"target_id": "t"}],
        "candidate_tasks": ["cand-r", "cand-s"],
        "null_tasks": ["null-r", "null-s", "null-s2"],
        "injection_tasks": ["inj-r"],
        "injection": {"richness": [50], "distance_pc": [1000]},
    }
    out = analysis.synthesis(params, upstream, _ctx(inputs={"benchmark:hr23": bench}))
    assert out["benchmark_problems"] == ["benchmark:cga20: not supplied"]
    assert out["regions_without_reason"] == ["s", "t"]
    assert out["recovery"] is upstream["recovery"] and out["recovery_reported"] == ["hr23"]
    assert (out["null_fields"], out["null_fields_with_survivors"], out["null_false_positive_rate"]) == (
        3,
        1,
        pytest.approx(1 / 3),
    )
    assert out["injection"] == [upstream["inj-r"]]
    assert out["injection_cells_missing"] == ["s/50/1000.0", "t/50/1000.0"]
    assert out["dropped_candidates"] == 2
    assert out["novel_without_two_crossmatches"] == ["r-c001"]
    assert out["gpu_results_without_agreement"] == ["cu|m=1"]
    assert out["gpu_exclusions"] == ["sk: note"]
    assert out["claim_violations"] == []
    assert out["cuts"] == {"r": {"kept_rows": 5}, "s": {"kept_rows": 2}}
    assert [c["candidate_id"] for c in out["candidates"]] == ["r-c000", "r-c001"]
    assert [c["rank"] for c in out["candidates"]] == [1, 2]
    assert [d["statement"] for d in out["dossiers"]] == [
        "r-c000 is a candidate open cluster, not a confirmed discovery.",
        "r-c001 is a candidate open cluster, not a confirmed discovery.",
    ]
    wrong = bm.with_metadata(_bench("x").table, "hr23:v0", "CC0", "c")
    stale = analysis.synthesis(params, upstream, _ctx(inputs={"benchmark:hr23": wrong}))
    assert stale["benchmark_problems"] == [
        "hr23: release hr23:v0 is not the predeclared hr23:v1",
        "benchmark:cga20: not supplied",
    ]
    agreed = {"sk": {"passed": True, "algorithm_id": "cu", "reference_id": "sk", "release": "r1"}}
    ok = analysis.synthesis(
        params, upstream, _ctx(inputs={"benchmark:hr23": bench}, gpu_agreement=agreed, release="r1")
    )
    assert ok["gpu_results_without_agreement"] == []
    none = analysis.synthesis({**params, "null_tasks": []}, upstream, _ctx(inputs={"benchmark:hr23": bench}))
    assert none["null_false_positive_rate"] is None


def test_the_synthesis_lints_every_reason_a_reader_sees() -> None:
    claim = {**_cand("r-c000", "rejected"), "reasons": ["we discovered it"]}
    upstream = {
        "cand-r": {"target_id": "r", "candidates": [claim], "reference_clusters": 1, "labellings": [], "cuts": {}},
        "recovery": {},
    }
    params = {
        "benchmark_roles": [],
        "benchmark_labels": [],
        "regions": [],
        "candidate_tasks": ["cand-r"],
        "null_tasks": [],
        "injection_tasks": [],
        "injection": {"richness": [], "distance_pc": []},
    }
    assert analysis.synthesis(params, upstream, _ctx(inputs={}))["claim_violations"] == ["we discovered it"]
    with pytest.raises(ValueError):
        analysis.synthesis({**params, "benchmark_labels": ["x"]}, upstream, _ctx(inputs={}))
