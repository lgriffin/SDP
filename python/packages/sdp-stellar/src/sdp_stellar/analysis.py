"""The TaskKinds of the UC-01 plan: pure functions of their params, upstream values and services.

Services: ``inputs`` (role -> decoded artifact), ``clusterers`` (algorithm id -> CPU reference
ClusterSearchAlgorithm) and, optionally, ``accelerators`` (algorithm id -> GPU implementation) with
``gpu_agreement`` (algorithm id -> agreement record). A GPU implementation is used only when its
agreement record for this release passed (STL-004); otherwise the CPU reference runs and the
exclusion is recorded.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True)
class CandidateChecks:
    """The pass marks a candidate must meet before it is cross-matched, from the predeclaration's
    ``parameters.candidate_checks`` (STL-008), so changing one changes the frozen hash."""

    min_members: int
    min_stability: float
    min_cmd_coherence: float

    @classmethod
    def from_parameters(cls, doc: Mapping[str, Any]) -> CandidateChecks:
        """Read the pass marks as written, refusing rather than coercing: a fractional member count
        must not be truncated into a different, unfrozen pass mark."""
        missing = sorted({"min_members", "min_stability", "min_cmd_coherence"} - set(doc))
        if missing:
            raise ValueError(f"candidate_checks lacks {missing}")
        members = doc["min_members"]
        if isinstance(members, bool) or not isinstance(members, int) or members < 1:
            raise ValueError(f"candidate_checks.min_members must be a whole number of at least 1, got {members!r}")
        marks: list[float] = []
        for key in ("min_stability", "min_cmd_coherence"):
            value = doc[key]
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"candidate_checks.{key} must be a finite number of at least 0, got {value!r}")
            marks.append(float(value))
        return cls(members, marks[0], marks[1])


def _configs(params: Mapping[str, Any]) -> list[Configuration]:
    return configurations(params["algorithms"], params["hyperparameter_grid"])


def _features(doc: Mapping[str, Any]) -> FeatureMatrix:
    return FeatureMatrix(np.asarray(doc["values"]), str(doc["policy_id"]), tuple(doc["axes"]), tuple(doc["scales"]))


def _features_doc(fm: FeatureMatrix) -> dict[str, Any]:
    return {"values": fm.values, "policy_id": fm.policy_id, "axes": list(fm.axes), "scales": list(fm.scales)}


def gpu_exclusion(
    record: Mapping[str, Any] | None, implementation_id: str, reference_id: str, release: Any
) -> str | None:
    """Why a GPU implementation may not stand in for ``reference_id`` in this run, or None when its
    agreement record binds it: passed, for this implementation, against this CPU reference, in this
    release (STL-004)."""
    if not record:
        return "no agreement record"
    if not record.get("passed"):
        return "its agreement test did not pass"
    if record.get("algorithm_id") != implementation_id or record.get("reference_id") != reference_id:
        return f"the agreement record is for {record.get('algorithm_id')} against {record.get('reference_id')}"
    if release is None or record.get("release") != release:
        return f"the agreement record is from release {record.get('release')!r}, not this release {release!r}"
    return None


def _clusterer(ctx: TaskContext, algorithm_id: str) -> tuple[Any, str, str | None]:
    """(implementation, backend, exclusion note). Accelerators are keyed by the CPU reference they replace."""
    accelerators = ctx.services.get("accelerators", {})
    note = None
    if algorithm_id in accelerators:
        gpu = accelerators[algorithm_id]
        record = ctx.services.get("gpu_agreement", {}).get(algorithm_id)
        why = gpu_exclusion(record, str(gpu.algorithm_id), algorithm_id, ctx.services.get("release"))
        if why is None:
            return gpu, str(getattr(gpu, "backend", "cuda")), None
        note = f"{algorithm_id}: GPU result ({gpu.algorithm_id}) excluded: {why}"
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
    result = run(impl, _features(prepared["features"]), config, ctx.seed, replaces=config.algorithm_id)
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
    checks = CandidateChecks.from_parameters(params["candidate_checks"])
    reasons: list[str] = []
    coh = coherence(c.members, colour, mag, seed) if c.members.size else None
    centre = bm.centre(table, c.members) if c.members.size else None
    if c.stability < checks.min_stability:
        reasons.append(f"unstable across the grid: stability {c.stability:.2f} < {checks.min_stability}")
    if c.members.size < checks.min_members:
        reasons.append(f"{c.members.size} members at membership >= 0.5, fewer than {checks.min_members}")
    if coh is not None and coh < checks.min_cmd_coherence:
        reasons.append(
            f"colour-magnitude sequence no tighter than the field: coherence {coh:.2f} < {checks.min_cmd_coherence}"
        )
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
    """Supplied benchmarks whose tables can be cross-matched; the others fail STL-G-02 in synthesis."""
    inputs = ctx.service("inputs")
    supplied = [bm.benchmark_from(role.split(":", 1)[1], inputs[role]) for role in roles if role in inputs]
    return [b for b in supplied if not b.missing_columns()]


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
        labellings.append(
            labelling_document(run(impl, fm, config, ctx.seed, replaces=config.algorithm_id), config, backend)
        )
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
    labels = run(impl, fm, config, ctx.seed, replaces=config.algorithm_id).labels
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
    nulls = [upstream[t] for t in params["null_tasks"]]
    injections = [upstream[t] for t in params["injection_tasks"]]
    all_candidates = [_with_region_evidence(c, nulls, injections) for c in all_candidates]
    ranked = ranking.rank(all_candidates)
    dossiers = [dossier.build(c) for c in ranked]
    # the lint reads everything a reader of the report sees about a candidate
    texts = [d["statement"] for d in dossiers] + [r for d in dossiers for r in d["reasons"]]

    agreements = ctx.services.get("gpu_agreement", {})
    release = ctx.services.get("release")
    gpu_unagreed = [
        lab["config"]
        for lab in labellings
        if lab["backend"] != "cpu"
        and gpu_exclusion(agreements.get(lab["algorithm_id"]), lab["implementation_id"], lab["algorithm_id"], release)
    ]
    novel_without = [
        c["candidate_id"]
        for c in all_candidates
        if c["status"] == "potentially_new" and len({x["benchmark"] for x in c["crossmatch"]}) < 2
    ]

    fp_fields = sum(1 for n in nulls if n["survivors"])
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


def _with_region_evidence(
    c: Mapping[str, Any], nulls: Sequence[Mapping[str, Any]], injections: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """A candidate's final label carries its region's null and injection evidence. A potentially new
    candidate from a region whose null fields produced survivors is only inconclusive: the pipeline
    finds clusters there that are not real."""
    region = str(c["candidate_id"]).rsplit("-c", 1)[0]
    region_nulls = [n for n in nulls if n["target_id"] == region]
    region_injections = [i for i in injections if i["target_id"] == region]
    evidence = {
        "null_fields": len(region_nulls),
        "null_fields_with_survivors": sum(1 for n in region_nulls if n["survivors"]),
        "injections": len(region_injections),
        "injections_recovered": sum(1 for i in region_injections if i["recovered"]),
    }
    out = {**c, "evidence": evidence, "reasons": list(c["reasons"])}
    if c["status"] == "potentially_new":
        if not region_nulls:
            out["status"] = "inconclusive"
            out["reasons"].append("no null field was run for this region")
        elif evidence["null_fields_with_survivors"]:
            out["status"] = "inconclusive"
            out["reasons"].append(
                f"{evidence['null_fields_with_survivors']} of {len(region_nulls)} null fields of this region produced surviving candidates"
            )
    return out
