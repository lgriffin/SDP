"""StellarClusterProtocol: the UC-01 ScientificProtocol (§10).

Inputs are one curated Gaia DR3 cone per predeclared region (``field:<target_id>``, Parquet in the
canonical astrometric schema) and the registered benchmark catalogues (``benchmark:<id>``, Parquet
whose metadata carries release, licence and citation). Benchmarks are optional at definition time
so that a run without them still happens and STL-G-02 names what is missing instead of the
experiment being refused.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from sdp.kernel.domain.errors import PlanInvalid
from sdp.kernel.domain.findings import Finding, Findings
from sdp.kernel.domain.gates import evaluate_gates
from sdp.kernel.domain.ids import ProtocolId
from sdp.kernel.domain.policy import DeterminismClass, NumericalPolicy, SeedHandling
from sdp.kernel.domain.predeclaration import Predeclaration
from sdp.kernel.domain.verdict import ValidationReport
from sdp.kernel.science.io import ComputeResults, InputSpec, Params, ResolvedInputs
from sdp.kernel.science.plan import ComputePlan, ComputeTask, TaskKindRegistry
from sdp_stellar import analysis
from sdp_stellar.search import configurations

PROTOCOL = ProtocolId("uc01-stellar-clusters", "1.0.0")
PARQUET = "application/vnd.apache.parquet"

EXACT_POLICY = NumericalPolicy(rtol=0.0, atol=0.0)
SEEDED_POLICY = NumericalPolicy(
    rtol=0.0, atol=1e-12, determinism=DeterminismClass.TOLERANCE, seed_handling=SeedHandling.DERIVED
)

#: Experiment parameters that may override the predeclared ones, and why.
OVERRIDABLE = {
    "null_fields_per_region": "fewer null fields for quick checks; the count is recorded and STL-G-04 uses it",
    "bootstrap_resamples": "fewer resamples for quick checks; the count is recorded in the manifest",
}


def load_predeclaration() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        resources.files("sdp_stellar").joinpath("predeclaration.json").read_text("utf-8")
    )
    return document


def _benchmark_id(label: str) -> str:
    return label.split(":", 1)[0]


class StellarClusterProtocol:
    protocol_id = PROTOCOL

    def predeclare(self) -> Predeclaration:
        return Predeclaration.from_document(load_predeclaration())

    def regions(self) -> list[dict[str, Any]]:
        out = []
        for t in self.predeclare().targets:
            spec = t.spec
            out.append(
                {
                    "target_id": t.target_id,
                    "reason": t.reason,
                    "ra_deg": float(spec["ra_deg"]),
                    "dec_deg": float(spec["dec_deg"]),
                    "radius_deg": float(spec["radius_deg"]),
                }
            )
        return out

    def benchmark_roles(self) -> list[str]:
        return [f"benchmark:{_benchmark_id(label)}" for label in self.predeclare().parameters["benchmarks"]]

    def required_inputs(self) -> tuple[InputSpec, ...]:
        fields = tuple(
            InputSpec(f"field:{r['target_id']}", PARQUET, f"curated Gaia DR3 cone for {r['target_id']} (STL-001)")
            for r in self.regions()
        )
        benchmarks = tuple(
            InputSpec(role, PARQUET, "benchmark cluster catalogue with release, licence and citation", required=False)
            for role in self.benchmark_roles()
        )
        return fields + benchmarks

    def task_kinds(self) -> TaskKindRegistry:
        registry = TaskKindRegistry()
        registry.register("stellar.prepare", analysis.prepare)
        registry.register("stellar.search", analysis.search)
        registry.register("stellar.candidates", analysis.candidates)
        registry.register("stellar.null", analysis.null_test)
        registry.register("stellar.injection", analysis.injection)
        registry.register("stellar.recovery", analysis.recovery_rates)
        registry.register("stellar.synthesis", analysis.synthesis)
        return registry

    def plan(self, inputs: ResolvedInputs, params: Params, seed: int) -> ComputePlan:
        declared = dict(self.predeclare().parameters)
        unknown = set(params) - set(OVERRIDABLE)
        if unknown:
            raise PlanInvalid(f"parameters {sorted(unknown)} are predeclared and cannot be overridden")
        p = {**declared, **{k: params[k] for k in OVERRIDABLE if k in params}}
        for key, least in (("null_fields_per_region", 0), ("bootstrap_resamples", 1)):
            value = p[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < least:
                raise PlanInvalid(f"{key} must be an integer of at least {least}, got {value!r}")
        roles = self.benchmark_roles()
        common: dict[str, Any] = {
            "feature_space_policy": p["feature_space_policy"],
            "algorithms": list(p["algorithms"]),
            "hyperparameter_grid": p["hyperparameter_grid"],
            "crossmatch_radius_deg": float(p["crossmatch_radius_deg"]),
            "pm_window_masyr": float(p["pm_window_masyr"]),
            "benchmark_roles": roles,
        }
        n_configs = len(configurations(p["algorithms"], p["hyperparameter_grid"]))
        injection = p["injection"]
        regions = self.regions()
        tasks: list[ComputeTask] = []
        candidate_ids, null_ids, injection_ids = [], [], []
        for region in regions:
            tid = region["target_id"]
            prep = f"prepare:{tid}"
            tasks.append(
                ComputeTask(
                    prep,
                    "stellar.prepare",
                    EXACT_POLICY,
                    (),
                    {
                        "role": f"field:{tid}",
                        "region": {k: region[k] for k in ("ra_deg", "dec_deg", "radius_deg")},
                        "quality_cuts": p["quality_cuts"],
                        "feature_space_policy": p["feature_space_policy"],
                    },
                )
            )
            searches = []
            for i in range(n_configs):
                sid = f"search:{tid}:{i}"
                searches.append(sid)
                tasks.append(
                    ComputeTask(
                        sid, "stellar.search", EXACT_POLICY, (prep,), {**common, "prepare": prep, "config_index": i}
                    )
                )
            cid = f"candidates:{tid}"
            candidate_ids.append(cid)
            tasks.append(
                ComputeTask(
                    cid,
                    "stellar.candidates",
                    EXACT_POLICY,
                    (prep, *searches),
                    {**common, "prepare": prep, "searches": searches, "target_id": tid},
                )
            )
            for k in range(int(p["null_fields_per_region"])):
                nid = f"null:{tid}:{k}"
                null_ids.append(nid)
                tasks.append(
                    ComputeTask(
                        nid,
                        "stellar.null",
                        SEEDED_POLICY,
                        (prep,),
                        {**common, "prepare": prep, "target_id": tid},
                        seed_offset=k,
                    )
                )
            for richness in injection["richness"]:
                for distance in injection["distance_pc"]:
                    iid = f"inject:{tid}:{richness}:{distance}"
                    injection_ids.append(iid)
                    tasks.append(
                        ComputeTask(
                            iid,
                            "stellar.injection",
                            SEEDED_POLICY,
                            (prep,),
                            {
                                **common,
                                "prepare": prep,
                                "target_id": tid,
                                "richness": int(richness),
                                "distance_pc": float(distance),
                                "pm_dispersion_masyr": float(injection["pm_dispersion_masyr"]),
                            },
                        )
                    )
        tasks.append(
            ComputeTask(
                "recovery",
                "stellar.recovery",
                SEEDED_POLICY,
                tuple(candidate_ids),
                {
                    **common,
                    "regions": regions,
                    "candidate_tasks": candidate_ids,
                    "bootstrap_resamples": int(p["bootstrap_resamples"]),
                },
            )
        )
        tasks.append(
            ComputeTask(
                "synthesis",
                "stellar.synthesis",
                EXACT_POLICY,
                (*candidate_ids, *null_ids, *injection_ids, "recovery"),
                {
                    **common,
                    "regions": regions,
                    "benchmark_labels": list(p["benchmarks"]),
                    "candidate_tasks": candidate_ids,
                    "null_tasks": null_ids,
                    "injection_tasks": injection_ids,
                    "injection": injection,
                },
            )
        )
        return ComputePlan(tasks=tuple(tasks), outputs=("recovery", "synthesis"))

    def interpret(self, results: ComputeResults) -> Findings:
        s = results["synthesis"]
        findings = [
            Finding("regions_without_reason", len(s["regions_without_reason"])),
            Finding("benchmarks_incomplete", len(s["benchmark_problems"])),
            Finding("recovery_reported_benchmarks", len(s["recovery_reported"])),
            Finding("injection_curve_cells_missing", len(s["injection_cells_missing"])),
            Finding("novel_without_two_crossmatches", len(s["novel_without_two_crossmatches"])),
            Finding("dropped_candidates", int(s["dropped_candidates"])),
            Finding("gpu_results_without_agreement", len(s["gpu_results_without_agreement"])),
            Finding("report_claim_violations", len(s["claim_violations"])),
            Finding("candidate_count", len(s["candidates"])),
            Finding("potentially_new_count", sum(1 for c in s["candidates"] if c["status"] == "potentially_new")),
        ]
        # No null fields means no false-positive rate: the finding is absent and STL-G-04 cannot pass.
        if s["null_false_positive_rate"] is not None:
            findings.append(Finding("null_false_positive_rate", float(s["null_false_positive_rate"])))
        for benchmark_id, r in sorted(s["recovery"].items()):
            if r["rate"] is not None:
                low, high = r["interval_95"]
                findings.append(Finding(f"recovery_rate_{benchmark_id}", r["rate"], (high - low) / 2))
        return Findings.of(
            *findings,
            narrative={
                "regions_without_reason": s["regions_without_reason"],
                "benchmark_problems": s["benchmark_problems"],
                "recovery": s["recovery"],
                "null_fields": s["null_fields"],
                "null_fields_with_survivors": s["null_fields_with_survivors"],
                "injection": s["injection"],
                "injection_cells_missing": s["injection_cells_missing"],
                "dossiers": s["dossiers"],
                "novel_without_two_crossmatches": s["novel_without_two_crossmatches"],
                "gpu_exclusions": s["gpu_exclusions"],
                "gpu_results_without_agreement": s["gpu_results_without_agreement"],
                "claim_violations": s["claim_violations"],
                "quality_cuts": s["cuts"],
            },
        )

    def validate(self, findings: Findings, predeclaration: Predeclaration, *, experiment_id: str) -> ValidationReport:
        return ValidationReport.from_checks(
            experiment_id=experiment_id,
            protocol_id=PROTOCOL.name,
            protocol_version=PROTOCOL.version,
            predeclaration_hash=predeclaration.compute_hash(),
            checks=evaluate_gates(predeclaration.gates, findings),
        )
