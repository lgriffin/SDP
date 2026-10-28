"""Fixtures for UC-02: synthetic bundles with planted deflections, and the module on the in-memory kernel."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from sdp.kernel.domain.experiment import Experiment
from sdp.kernel.domain.verdict import ValidationReport
from sdp.kernel.ports.driving import DefineExperimentCommand, FreezePredeclarationCommand
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel
from sdp_relativity.fitter import fit_set
from sdp_relativity.observations import parse_bundle
from sdp_relativity.protocol import Einstein1919Protocol
from sdp_relativity.reduction import fit_scenarios
from sdp_relativity.synthetic import dumps, make_bundle


def independent_fit(bundle: dict[str, Any], perturb: float = 0.0) -> dict[str, Any]:
    """Stands in for the TypeScript fitter in tier T4: the reference fitter's alphas, optionally shifted.
    The real independent implementation is compared in tests/relativity/test_independent.py."""
    parsed = parse_bundle(bundle)
    return {
        "implementation": {"name": "reference-echo", "language": "Python"},
        "fits": [
            {"instrument": o.instrument, "scenario": s.to_document(), "alpha_arcsec": fit_set(o, s).alpha + perturb}
            for o in parsed.sets
            for s in fit_scenarios()
        ],
    }


@dataclass
class Run:
    k: InMemoryKernel
    experiment: Experiment
    report: ValidationReport

    @property
    def manifest(self) -> dict[str, Any]:
        manifest = self.k.kernel.query_evidence.manifest(self.experiment.experiment_id)
        assert manifest is not None
        return manifest

    def finding(self, name: str) -> Any:
        return next(f["value"] for f in self.manifest["findings"] if f["name"] == name)

    def outcome(self, gate_id: str) -> str:
        return next(c.outcome.value for c in self.report.checks if c.gate_id == gate_id)


RunFactory = Callable[..., Run]


@pytest.fixture
def run_uc02() -> RunFactory:
    def run(
        bundle: dict[str, Any] | None = None, independent: dict[str, Any] | None = None, resamples: int = 50
    ) -> Run:
        bundle = bundle if bundle is not None else make_bundle()
        protocol = Einstein1919Protocol()
        k = build_in_memory_kernel(protocol)
        frozen, _ = k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(protocol.predeclare(), "leigh"))
        observations = k.store.put(dumps(bundle), "application/json")
        fit = k.store.put(
            json.dumps(independent if independent is not None else independent_fit(bundle)).encode(), "application/json"
        )
        experiment = k.kernel.define_experiment.execute(
            DefineExperimentCommand(
                "uc02-einstein-1919",
                frozen.hash,
                {"observations": observations, "independent_fit": fit},
                {"bootstrap_resamples": resamples},
                seed=1919,
            )
        )
        experiment = k.kernel.run_experiment.execute(experiment.experiment_id)
        report = k.kernel.validate_experiment.execute(experiment.experiment_id)
        return Run(k, experiment, report)

    return run
