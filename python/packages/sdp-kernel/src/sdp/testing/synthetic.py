"""The synthetic ScientificProtocol that drives the walking skeleton (§17 Phase 1).

It recovers the slope of a noisy straight line. It is deliberately trivial science so that every
kernel capability (freeze, define, run, checkpoint, resume, validate, manifest, dossier, bundle)
can be exercised and re-derived by the harness in milliseconds.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

import astropy.units as u
import numpy as np

from sdp.kernel.domain.findings import Finding, Findings, OracleStatus
from sdp.kernel.domain.gates import Comparator, Gate, Severity, evaluate_gates
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.ids import ProtocolId
from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.domain.predeclaration import OracleTolerance, Predeclaration, Target
from sdp.kernel.domain.prediction import Prediction
from sdp.kernel.domain.verdict import ValidationReport
from sdp.kernel.science.io import ComputeResults, InputSpec, Params, ResolvedInputs
from sdp.kernel.science.plan import ComputePlan, ComputeTask, TaskContext, TaskKindRegistry

PROTOCOL = ProtocolId("synthetic-linear", "1.0.0")
SERIES_MEDIA_TYPE = "application/json"
FIT_POLICY = NumericalPolicy(rtol=1e-9, atol=1e-12)

#: What stops each TaskKind from reproducing bitwise (NFR-008; docs/determinism.md).
NONDETERMINISM = {
    "synthetic.fit_line": "least squares in float64; float64 reductions run through NumPy/BLAS, whose summation order can change with the BLAS build, CPU features and thread count, so results agree within the policy tolerance, not bitwise",
    "synthetic.residuals": "residual RMS in float64; float64 reductions run through NumPy/BLAS, whose summation order can change with the BLAS build, CPU features and thread count, so results agree within the policy tolerance, not bitwise",
}


def make_series(n: int = 200, slope: float = 2.0, intercept: float = 0.5, noise: float = 0.1, seed: int = 7) -> bytes:
    rng = np.random.default_rng(seed)
    x = np.linspace(0.0, 10.0, n)
    y = slope * x + intercept + rng.normal(0.0, noise, n)
    return json.dumps({"x": x.tolist(), "y": y.tolist(), "generator": {"slope": slope, "seed": seed}}).encode()


class InjectedFailure(RuntimeError):
    """Raised on purpose by a synthetic task to exercise KRN-007."""


def fit_line(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, float]:
    if params.get("fail"):
        raise InjectedFailure("injected failure in fit_line")
    series = ctx.service("inputs")["series"]
    x = np.asarray(series["x"], dtype=float)
    y = np.asarray(series["y"], dtype=float)
    design = np.vstack([x, np.ones_like(x)]).T
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    residual = y - design @ coef
    dof = max(len(x) - 2, 1)
    sigma2 = float(residual @ residual) / dof
    cov = sigma2 * np.linalg.inv(design.T @ design)
    return {"slope": float(coef[0]), "intercept": float(coef[1]), "slope_err": float(math.sqrt(cov[0, 0]))}


def residuals(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, float]:
    if params.get("fail"):
        raise InjectedFailure("injected failure in residuals")
    series = ctx.service("inputs")["series"]
    fit = upstream["fit"]
    x = np.asarray(series["x"], dtype=float)
    y = np.asarray(series["y"], dtype=float)
    r = y - (fit["slope"] * x + fit["intercept"])
    return {"rms": float(np.sqrt(np.mean(r**2))), "n": len(x)}


class SyntheticLinearProtocol:
    protocol_id = PROTOCOL

    def __init__(self, planted_slope: float = 2.0) -> None:
        self.planted_slope = planted_slope

    def predeclare(self) -> Predeclaration:
        return Predeclaration(
            predeclaration_id="synthetic-linear-v1",
            protocol_id=PROTOCOL.name,
            protocol_version=PROTOCOL.version,
            title="Synthetic linear recovery (walking skeleton)",
            hypotheses=(
                Hypothesis(
                    hypothesis_id="SYN-H1",
                    version=1,
                    statement="The fitted slope of the synthetic series equals the planted slope.",
                    predictions=(Prediction("linear", "1.0.0", {"slope": 2.0}, 2.0 * u.dimensionless_unscaled),),
                    evaluation_criteria=("|slope - 2.0| < 0.05",),
                ),
            ),
            gates=(
                Gate(
                    "SYN-G-01",
                    "Fitted slope agrees with the planted slope",
                    "slope_abs_error",
                    Comparator.LT,
                    0.05,
                    Severity.BLOCKING,
                    ("planted-slope",),
                ),
                Gate(
                    "SYN-G-02",
                    "Residual RMS below the noise ceiling",
                    "residual_rms",
                    Comparator.LE,
                    0.5,
                    Severity.WARNING,
                ),
            ),
            oracle_tolerances=(OracleTolerance("planted-slope", 0.0, 0.05, None),),
            targets=(Target("series-a", "source", "Synthetic series generated from a fixed seed", {"n": 200}),),
            parameters={"noise_sigma": 0.1, "n": 200},
        )

    def required_inputs(self) -> tuple[InputSpec, ...]:
        return (InputSpec("series", SERIES_MEDIA_TYPE, "x/y series as JSON"),)

    def task_kinds(self) -> TaskKindRegistry:
        registry = TaskKindRegistry()
        registry.register("synthetic.fit_line", fit_line, nondeterminism=NONDETERMINISM["synthetic.fit_line"])
        registry.register("synthetic.residuals", residuals, nondeterminism=NONDETERMINISM["synthetic.residuals"])
        return registry

    def plan(self, inputs: ResolvedInputs, params: Params, seed: int) -> ComputePlan:
        fail = params.get("inject_failure")
        return ComputePlan(
            tasks=(
                ComputeTask("fit", "synthetic.fit_line", FIT_POLICY, (), {"fail": fail == "fit"}),
                ComputeTask("residuals", "synthetic.residuals", FIT_POLICY, ("fit",), {"fail": fail == "residuals"}),
            ),
            outputs=("fit", "residuals"),
        )

    def interpret(self, results: ComputeResults) -> Findings:
        fit = results["fit"]
        return Findings.of(
            Finding("slope", fit["slope"], fit["slope_err"]),
            Finding("slope_abs_error", abs(fit["slope"] - self.planted_slope)),
            Finding("residual_rms", results["residuals"]["rms"]),
            oracles={"planted-slope": OracleStatus("planted-slope", True, "planted by the generator")},
        )

    def validate(self, findings: Findings, predeclaration: Predeclaration, *, experiment_id: str) -> ValidationReport:
        return ValidationReport.from_checks(
            experiment_id=experiment_id,
            protocol_id=PROTOCOL.name,
            protocol_version=PROTOCOL.version,
            predeclaration_hash=predeclaration.compute_hash(),
            checks=evaluate_gates(predeclaration.gates, findings),
        )
