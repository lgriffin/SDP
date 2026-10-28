"""Einstein1919Protocol: the UC-02 ScientificProtocol (§9).

Inputs are an observation bundle exported from the historical-sources store and the independent
fitter's output on the same bundle. Until the 1920 tables are transcribed and verified, runs use
synthetic bundles, and the historical oracles stay unverified, so the verdict is INCONCLUSIVE by
design: the report names the transcription gap rather than hiding it.
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
from sdp_relativity import analysis
from sdp_relativity.oracles import load_registry
from sdp_relativity.reduction import BASELINE, axes_from

PROTOCOL = ProtocolId("uc02-einstein-1919", "1.0.0")
OBSERVATIONS_MEDIA_TYPE = "application/json"

FIT_POLICY = NumericalPolicy(rtol=1e-9, atol=1e-12)
BOOTSTRAP_POLICY = NumericalPolicy(
    rtol=0.0, atol=1e-12, determinism=DeterminismClass.TOLERANCE, seed_handling=SeedHandling.DERIVED
)
EXACT_POLICY = NumericalPolicy(rtol=1e-12, atol=0.0)

#: Experiment parameters that may override the predeclared ones, and why.
OVERRIDABLE = {"bootstrap_resamples": "fewer resamples for quick checks; the count is recorded in the manifest"}


def _slug(instrument: str) -> str:
    return instrument.replace("-", "_")


class Einstein1919Protocol:
    protocol_id = PROTOCOL

    def predeclare(self) -> Predeclaration:
        text = resources.files("sdp_relativity").joinpath("predeclaration.json").read_text("utf-8")
        return Predeclaration.from_document(json.loads(text))

    def required_inputs(self) -> tuple[InputSpec, ...]:
        return (
            InputSpec("observations", OBSERVATIONS_MEDIA_TYPE, "observation bundle (sdp-relativity/observations@1)"),
            InputSpec("independent_fit", "application/json", "relativity-fit output on the same bundle (REL-007)"),
        )

    def task_kinds(self) -> TaskKindRegistry:
        registry = TaskKindRegistry()
        registry.register("relativity.limb_models", analysis.limb_models)
        registry.register("relativity.transcription_audit", analysis.transcription_audit)
        registry.register("relativity.instrument_sweep", analysis.instrument_sweep)
        registry.register("relativity.pooled", analysis.pooled)
        registry.register("relativity.independent_agreement", analysis.independent_agreement)
        registry.register("relativity.synthesis", analysis.synthesis)
        return registry

    def instruments(self) -> list[str]:
        return [str(t.spec["instrument"]) for t in self.predeclare().targets]

    def plan(self, inputs: ResolvedInputs, params: Params, seed: int) -> ComputePlan:
        pre = self.predeclare()
        declared = dict(pre.parameters)
        unknown = set(params) - set(OVERRIDABLE)
        if unknown:
            raise PlanInvalid(f"parameters {sorted(unknown)} are predeclared and cannot be overridden")
        axes = {k: list(v) for k, v in axes_from(declared).items()}
        common: dict[str, Any] = {
            "axes": axes,
            "prior_widths": axes["prior_width_arcsec"],
            "nu": declared.get("student_t_nu", 4),
            "grid": declared["alpha_grid_arcsec"],
        }
        instruments = self.instruments()
        fit_tasks = [
            ComputeTask(
                f"fit:{name}",
                "relativity.instrument_sweep",
                BOOTSTRAP_POLICY,
                (),
                {
                    **common,
                    "instrument": name,
                    "bootstrap_resamples": int(
                        params.get("bootstrap_resamples", declared.get("bootstrap_resamples", 0))
                    ),
                },
            )
            for name in instruments
        ]
        fit_ids = tuple(t.task_id for t in fit_tasks)
        tasks = (
            ComputeTask("limb", "relativity.limb_models", EXACT_POLICY, (), {}),
            ComputeTask("audit", "relativity.transcription_audit", EXACT_POLICY, (), {"instruments": instruments}),
            *fit_tasks,
            ComputeTask("pooled", "relativity.pooled", FIT_POLICY, (), common),
            ComputeTask("independent", "relativity.independent_agreement", FIT_POLICY, fit_ids, {}),
            ComputeTask(
                "synthesis",
                "relativity.synthesis",
                FIT_POLICY,
                (*fit_ids, "pooled"),
                {
                    **common,
                    "instruments": instruments,
                    "oracle_tolerances": [t.to_document() for t in pre.oracle_tolerances],
                },
            ),
        )
        return ComputePlan(tasks=tasks, outputs=tuple(t.task_id for t in tasks))

    def interpret(self, results: ComputeResults) -> Findings:
        limb, audit, synthesis, independent = (
            results["limb"],
            results["audit"],
            results["synthesis"],
            results["independent"],
        )
        findings = [
            Finding("unlinked_measurement_count", len(audit["unlinked"])),
            Finding("unverified_measurement_count", len(audit["unverified"])),
            Finding("limb_model_max_rel_error", limb["max_rel_error"]),
            Finding("gr_limb_arcsec", limb["gr_limb_arcsec"], unit="arcsec"),
            Finding("half_limb_arcsec", limb["half_limb_arcsec"], unit="arcsec"),
            Finding(
                "hypotheses_missing_count",
                synthesis["hypotheses_expected"]
                - synthesis["hypotheses_evaluated"]
                + len(audit["missing_instruments"]),
            ),
            Finding("sensitivity_scenarios_missing", synthesis["scenarios_expected"] - synthesis["scenarios_complete"]),
            Finding("reanalysis_comparison_present", bool(synthesis["reanalysis_comparison"])),
            Finding("dossier_claim_violations", len(synthesis["claim_violations"])),
        ]
        if synthesis["historical_reproduction_max_z"] is not None:
            findings.append(Finding("historical_reproduction_max_z", synthesis["historical_reproduction_max_z"]))
        if independent["max_abs_diff_arcsec"] is not None and not independent["missing"]:
            findings.append(
                Finding("independent_fitter_max_abs_diff_arcsec", independent["max_abs_diff_arcsec"], unit="arcsec")
            )
        for name in self.instruments():
            fits = results[f"fit:{name}"]
            baseline = fits["scenarios"].get(BASELINE.key)
            if baseline:
                free = next(h for h in baseline["hypotheses"] if h["hypothesis_id"] == "UC02-H3")
                findings.append(
                    Finding(f"alpha_{_slug(name)}_arcsec", free["alpha_arcsec"], free["sigma_alpha_arcsec"], "arcsec")
                )
        registry = load_registry()
        gate_oracles = {o for g in self.predeclare().gates for o in g.oracle_ids}
        return Findings.of(
            *findings,
            oracles={o: registry[o].status_record() for o in sorted(gate_oracles) if o in registry},
            narrative={
                "constants_version": limb["constants_version"],
                "input_provenance": audit["provenance"],
                "unlinked_measurements": audit["unlinked"],
                "unverified_measurements": audit["unverified"],
                "historical_reproduction": synthesis["historical_reproduction"],
                "reanalysis_comparison": synthesis["reanalysis_comparison"],
                "surviving_conclusions": synthesis["surviving_conclusions"],
                "independent_fitter": {k: independent[k] for k in ("compared", "missing", "implementation")},
                "limitations": synthesis["limitations"],
                "claim_violations": synthesis["claim_violations"],
                "bootstrap": {name: results[f"fit:{name}"].get("bootstrap") for name in self.instruments()},
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
