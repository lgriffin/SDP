"""Fixtures for UC-01: synthetic fields with planted clusters, and the module on the in-memory kernel."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any

import pyarrow as pa
import pytest

from sdp.adapters.bootstrap import clusterers
from sdp.kernel.domain.experiment import Experiment
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.domain.verdict import ValidationReport
from sdp.kernel.ports.driving import DefineExperimentCommand, FreezePredeclarationCommand
from sdp.kernel.science.io import ResolvedInput, ResolvedInputs
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel
from sdp_stellar.protocol import PARQUET, StellarClusterProtocol, load_predeclaration
from sdp_stellar.synthetic import make_benchmark, make_field, parquet

SEED = 7
QUICK = {"null_fields_per_region": 3, "bootstrap_resamples": 100}


def targets() -> list[dict[str, Any]]:
    return list(load_predeclaration()["targets"])


@cache
def field(target_id: str) -> pa.Table:
    return make_field(next(t for t in targets() if t["target_id"] == target_id), SEED)


@cache
def benchmark(benchmark_id: str) -> pa.Table:
    label = next(b for b in load_predeclaration()["parameters"]["benchmarks"] if b.startswith(benchmark_id + ":"))
    return make_benchmark(benchmark_id, targets(), label)


def tables(benchmarks: tuple[str, ...] = ("hr23", "cga20")) -> dict[str, pa.Table]:
    out = {f"field:{t['target_id']}": field(t["target_id"]) for t in targets()}
    out.update({f"benchmark:{b}": benchmark(b) for b in benchmarks})
    return out


def resolved(data: Mapping[str, pa.Table]) -> ResolvedInputs:
    source = SourceRef(SourceKind.SYNTHETIC, "sdp_stellar.synthetic", "CC0-1.0", "synthetic recovery fixture")
    return ResolvedInputs(
        {role: ResolvedInput(role, ArtifactId.of_bytes(parquet(t)), PARQUET, source, t) for role, t in data.items()}
    )


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

    @property
    def narrative(self) -> dict[str, Any]:
        assert self.experiment.findings is not None
        return dict(self.experiment.findings.narrative)


RunFactory = Callable[..., Run]


def run_uc01(
    data: Mapping[str, pa.Table] | None = None,
    params: Mapping[str, Any] | None = None,
    services: Mapping[str, Any] | None = None,
    seed: int = 11,
) -> Run:
    protocol = StellarClusterProtocol()
    k = build_in_memory_kernel(protocol, services={"clusterers": clusterers(), **(services or {})})
    frozen, _ = k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(protocol.predeclare(), "leigh"))
    artifacts = {role: k.store.put(parquet(t), PARQUET) for role, t in (data if data is not None else tables()).items()}
    experiment = k.kernel.define_experiment.execute(
        DefineExperimentCommand(protocol.protocol_id.name, frozen.hash, artifacts, dict(params or QUICK), seed=seed)
    )
    experiment = k.kernel.run_experiment.execute(experiment.experiment_id)
    report = k.kernel.validate_experiment.execute(experiment.experiment_id)
    return Run(k, experiment, report)


@pytest.fixture(scope="session")
def clean_run() -> Run:
    return run_uc01()
