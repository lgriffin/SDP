"""The TaskKinds of the UC-01 plan: pure functions of their params, upstream values and services.

Services: ``inputs`` (role -> decoded artifact), ``clusterers`` (algorithm id -> CPU reference
ClusterSearchAlgorithm) and, optionally, ``accelerators`` (algorithm id -> GPU implementation) with
``gpu_agreement`` (algorithm id -> agreement record). A GPU implementation is used only when its
agreement record for this release passed (STL-004); otherwise the CPU reference runs and the
exclusion is recorded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pyarrow as pa

from sdp.kernel.science.plan import TaskContext
from sdp_stellar import benchmark as bm
from sdp_stellar import dossier, ranking
from sdp_stellar.cmd import absolute_g, coherence
from sdp_stellar.features import FeatureMatrix, apply_policy
from sdp_stellar.inject import inject, recovery
from sdp_stellar.membership import Candidate, estimate
from sdp_stellar.null import null_field
from sdp_stellar.quality import QualityCuts, column
from sdp_stellar.search import Configuration, configurations, labelling_document, run

MIN_MEMBERS = 10
MIN_STABILITY = 0.5
MIN_COHERENCE = 3.0


def _configs(params: Mapping[str, Any]) -> list[Configuration]:
    return configurations(params["algorithms"], params["hyperparameter_grid"])


def _features(doc: Mapping[str, Any]) -> FeatureMatrix:
    return FeatureMatrix(np.asarray(doc["values"]), str(doc["policy_id"]), tuple(doc["axes"]), tuple(doc["scales"]))


def _features_doc(fm: FeatureMatrix) -> dict[str, Any]:
    return {"values": fm.values, "policy_id": fm.policy_id, "axes": list(fm.axes), "scales": list(fm.scales)}


def _clusterer(ctx: TaskContext, algorithm_id: str) -> tuple[Any, str, str | None]:
    """(implementation, backend, exclusion note)."""
    accelerators = ctx.services.get("accelerators", {})
    if algorithm_id in accelerators:
        record = ctx.services.get("gpu_agreement", {}).get(algorithm_id)
        if record and record.get("passed"):
            gpu = accelerators[algorithm_id]
            return gpu, str(getattr(gpu, "backend", "cuda")), None
        note = f"{algorithm_id}: GPU result excluded, no passing agreement with the CPU reference in this release"
    else:
        note = None
    cpu = ctx.service("clusterers")[algorithm_id]
    return cpu, str(getattr(cpu, "backend", "cpu")), note


def prepare(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    """Quality cuts (recorded, STL-003) then the feature-space policy (STL-002)."""
    table: pa.Table = ctx.service("inputs")[params["role"]]
    kept, report = QualityCuts.from_parameters(params["quality_cuts"]).apply(table)
    region = params["region"]
    fm = apply_policy(params["feature_space_policy"], kept, region["ra_deg"], region["dec_deg"])
    return {"region": dict(region), "cuts": report, "table": kept, "features": _features_doc(fm)}


def search(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    prepared = upstream[params["prepare"]]
    config = _configs(params)[int(params["config_index"])]
    impl, backend, note = _clusterer(ctx, config.algorithm_id)
    result = run(impl, _features(prepared["features"]), Configuration(config.algorithm_id, config.params), ctx.seed)
    doc = labelling_document(result, config, backend)
    doc["excluded_gpu"] = note
    return doc


def _assess(
    region: str,
    table: pa.Table,
    labellings: Sequence[Mapping[str, Any]],
    benchmarks: Sequence[bm.Benchmark],
    params: Mapping[str, Any],
    seed: int,
) -> list[dict[str, Any]]:
    colour = column(table, "bp_rp")
    mag = absolute_g(column(table, "g_mag"), column(table, "parallax_mas"))
    out: list[dict[str, Any]] = []
    for c in estimate(region, labellings):
        out.append(_classify(c, table, colour, mag, benchmarks, params, seed))
    return out


def _classify(
    c: Candidate,
    table: pa.Table,
    colour: np.ndarray,
    mag: np.ndarray,
    benchmarks: Sequence[bm.Benchmark],
    params: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    coh = coherence(c.members, colour, mag, seed) if c.members.size else None
    centre = bm.centre(table, c.members) if c.members.size else None
    if c.stability < MIN_STABILITY:
        reasons.append(f"unstable across the grid: stability {c.stability:.2f} < {MIN_STABILITY}")
    if c.members.size < MIN_MEMBERS:
        reasons.append(f"{c.members.size} members at membership >= 0.5, fewer than {MIN_MEMBERS}")
    if coh is not None and coh < MIN_COHERENCE:
        reasons.append(f"colour-magnitude sequence no tighter than the field: coherence {coh:.2f} < {MIN_COHERENCE}")
    crossmatch = []
    if reasons:
        status = "rejected"
    elif coh is None:
        status = "inconclusive"
        reasons.append("too few members with a colour for the CMD check")
    else:
        assert centre is not None
        crossmatch = [
            bm.cross_match(centre, b, float(params["crossmatch_radius_deg"]), float(params["pm_window_masyr"]))
            for b in benchmarks
        ]
        known = sorted({m["name"] for x in crossmatch for m in x["matches"]})
        if known:
            status = "known"
            reasons.append("matches " + ", ".join(known))
        elif len(crossmatch) >= 2:
            status = "potentially_new"
            reasons.append(
                "passed every check and matches no cluster in " + " or ".join(x["benchmark"] for x in crossmatch)
            )
        else:
            status = "inconclusive"
            reasons.append("cross-matched against fewer than two benchmarks (STL-005)")
    return {
        "candidate_id": c.candidate_id,
        "status": status,
        "reasons": reasons,
        "n_members": int(c.members.size),
        "reference_size": c.reference_size,
        "mean_membership": c.mean_membership,
        "stability": c.stability,
        "cmd_coherence": coh,
        "centre": centre,
        "crossmatch": crossmatch,
        "member_keys": [table.column("source_key")[int(i)].as_py() for i in c.members],
    }


def _benchmarks(ctx: TaskContext, roles: Sequence[str]) -> list[bm.Benchmark]:
    inputs = ctx.service("inputs")
    return [bm.benchmark_from(role.split(":", 1)[1], inputs[role]) for role in roles if role in inputs]


def candidates(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    prepared = upstream[params["prepare"]]
    labellings = [upstream[t] for t in params["searches"]]
    found = _assess(
        params["target_id"],
        prepared["table"],
        labellings,
        _benchmarks(ctx, params["benchmark_roles"]),
        params,
        ctx.seed,
    )
    return {
        "target_id": params["target_id"],
        "candidates": found,
        "reference_clusters": int(labellings[0]["n_clusters"]) if labellings else 0,
        "labellings": [{k: v for k, v in lab.items() if k not in ("labels", "probabilities")} for lab in labellings],
        "cuts": prepared["cuts"],
    }


def null_test(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    """The whole candidate pipeline on one shuffled field; anything surviving is a false positive."""
    prepared = upstream[params["prepare"]]
    table = null_field(prepared["table"], ctx.seed)
    region = prepared["region"]
    fm = apply_policy(params["feature_space_policy"], table, region["ra_deg"], region["dec_deg"])
    labellings = []
    for config in _configs(params):
        impl, backend, _ = _clusterer(ctx, config.algorithm_id)
        labellings.append(labelling_document(run(impl, fm, config, ctx.seed), config, backend))
    found = _assess(
        params["target_id"] + "-null",
        table,
        labellings,
        [],
        {**params, "crossmatch_radius_deg": 0, "pm_window_masyr": 0},
        ctx.seed,
    )
    # No benchmarks on a null field, so anything not rejected on its own evidence is a false positive.
    survivors = [c["candidate_id"] for c in found if c["status"] != "rejected"]
    return {"target_id": params["target_id"], "seed": ctx.seed, "clusters": len(found), "survivors": survivors}


def injection(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    prepared = upstream[params["prepare"]]
    region = prepared["region"]
    table, mask = inject(
        prepared["table"],
        region,
        int(params["richness"]),
        float(params["distance_pc"]),
        float(params["pm_dispersion_masyr"]),
        ctx.seed,
    )
    fm = apply_policy(params["feature_space_policy"], table, region["ra_deg"], region["dec_deg"])
    config = _configs(params)[0]
    impl, _, _ = _clusterer(ctx, config.algorithm_id)
    labels = run(impl, fm, config, ctx.seed).labels
    return {
        "target_id": params["target_id"],
        "richness": int(params["richness"]),
        "distance_pc": float(params["distance_pc"]),
        "seed": ctx.seed,
        **recovery(np.asarray(labels), mask),
    }


def _interval(hits: np.ndarray, resamples: int, seed: int) -> list[float] | None:
    if hits.size == 0:
        return None
    rng = np.random.default_rng(seed)
    rates = [float(np.mean(rng.choice(hits, hits.size, replace=True))) for _ in range(resamples)]
    return [float(np.quantile(rates, 0.025)), float(np.quantile(rates, 0.975))]


def recovery_rates(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    """Known-cluster recovery per benchmark with bootstrap intervals (UC01-H1, STL-G-03)."""
    benchmarks = _benchmarks(ctx, params["benchmark_roles"])
    regions = {t["target_id"]: t for t in params["regions"]}
    out = {}
    for b in benchmarks:
        hits: dict[str, bool] = {}
        for task in params["candidate_tasks"]:
            value = upstream[task]
            centres = [
                c["centre"]
                for c in value["candidates"]
                if c["centre"] is not None and c["status"] in ("known", "potentially_new")
            ]
            hits.update(
                bm.recovered(
                    b,
                    centres,
                    regions[value["target_id"]],
                    float(params["crossmatch_radius_deg"]),
                    float(params["pm_window_masyr"]),
                )
            )
        arr = np.asarray([1.0 if v else 0.0 for v in hits.values()])
        out[b.benchmark_id] = {
            "release": b.release,
            "clusters_in_regions": int(arr.size),
            "recovered": sorted(k for k, v in hits.items() if v),
            "missed": sorted(k for k, v in hits.items() if not v),
            "rate": float(arr.mean()) if arr.size else None,
            "interval_95": _interval(arr, int(params["bootstrap_resamples"]), ctx.seed),
        }
    return out


def synthesis(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    inputs = ctx.service("inputs")
    benchmark_problems: list[str] = []
    for role, label in zip(params["benchmark_roles"], params["benchmark_labels"], strict=True):
        if role in inputs:
            benchmark_problems += bm.benchmark_from(role.split(":", 1)[1], inputs[role]).problems(label)
        else:
            benchmark_problems.append(f"{role}: not supplied")
    regions_without_reason = [t["target_id"] for t in params["regions"] if not str(t.get("reason", "")).strip()]

    all_candidates = []
    reference_total = 0
    labellings = []
    for task in params["candidate_tasks"]:
        value = upstream[task]
        all_candidates += value["candidates"]
        reference_total += value["reference_clusters"]
        labellings += value["labellings"]
    ranked = ranking.rank(all_candidates)
    dossiers = [dossier.build(c) for c in ranked]
    texts = [d["statement"] for d in dossiers] + [*params.get("report_text", [])]

    agreements = ctx.services.get("gpu_agreement", {})
    gpu_unagreed = [
        lab["config"]
        for lab in labellings
        if lab["backend"] != "cpu" and not (agreements.get(lab["algorithm_id"]) or {}).get("passed")
    ]
    novel_without = [
        c["candidate_id"]
        for c in all_candidates
        if c["status"] == "potentially_new" and len({x["benchmark"] for x in c["crossmatch"]}) < 2
    ]

    nulls = [upstream[t] for t in params["null_tasks"]]
    fp_fields = sum(1 for n in nulls if n["survivors"])
    injections = [upstream[t] for t in params["injection_tasks"]]
    cells = {(i["target_id"], i["richness"], i["distance_pc"]) for i in injections}
    expected_cells = {
        (t["target_id"], int(r), float(d))
        for t in params["regions"]
        for r in params["injection"]["richness"]
        for d in params["injection"]["distance_pc"]
    }
    recovery_value = upstream["recovery"]
    return {
        "regions_without_reason": regions_without_reason,
        "benchmark_problems": benchmark_problems,
        "recovery": recovery_value,
        "recovery_reported": sorted(k for k, v in recovery_value.items() if v["interval_95"] is not None),
        "null_fields": len(nulls),
        "null_fields_with_survivors": fp_fields,
        "null_false_positive_rate": fp_fields / len(nulls) if nulls else None,
        "injection": injections,
        "injection_cells_missing": sorted(f"{a}/{b}/{c}" for a, b, c in expected_cells - cells),
        "candidates": ranked,
        "dossiers": dossiers,
        "dropped_candidates": reference_total - len(all_candidates),
        "novel_without_two_crossmatches": novel_without,
        "gpu_results_without_agreement": gpu_unagreed,
        "gpu_exclusions": sorted({lab["excluded_gpu"] for lab in labellings if lab.get("excluded_gpu")}),
        "claim_violations": dossier.claim_violations(texts),
        "cuts": {upstream[t]["target_id"]: upstream[t]["cuts"] for t in params["candidate_tasks"]},
    }
