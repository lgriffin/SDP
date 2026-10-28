"""T4 fixtures: the kernel wired to fakes, plus helpers that drive the synthetic protocol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from sdp.kernel.domain.experiment import Experiment
from sdp.kernel.domain.predeclaration import FrozenPredeclaration
from sdp.kernel.ports.driving import DefineExperimentCommand, FreezePredeclarationCommand
from sdp.kernel.science.protocol import ScientificProtocol
from sdp.testing.synthetic import SyntheticLinearProtocol, make_series
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel


@dataclass
class Harness:
    k: InMemoryKernel
    protocol: ScientificProtocol

    def freeze(self) -> FrozenPredeclaration:
        frozen, _ = self.k.kernel.freeze_predeclaration.execute(
            FreezePredeclarationCommand(self.protocol.predeclare(), "leigh")
        )
        return frozen

    def define(
        self, predeclaration_hash: str | None = None, parameters: Mapping[str, object] | None = None
    ) -> Experiment:
        if predeclaration_hash is None:
            predeclaration_hash = self.freeze().hash
        series = self.k.store.put(make_series(), "application/json")
        return self.k.kernel.define_experiment.execute(
            DefineExperimentCommand(
                self.protocol.protocol_id.name, predeclaration_hash, {"series": series}, dict(parameters or {}), seed=42
            )
        )

    def run(self, **parameters: object) -> Experiment:
        experiment = self.define(parameters=parameters)
        return self.k.kernel.run_experiment.execute(experiment.experiment_id)

    def event_names(self) -> list[str]:
        return [stored.event.name for stored in self.k.kernel.query_evidence.events()]


def harness_for(protocol: ScientificProtocol, **kwargs: object) -> Harness:
    return Harness(build_in_memory_kernel(protocol, **kwargs), protocol)  # type: ignore[arg-type]


@pytest.fixture
def harness() -> Harness:
    return harness_for(SyntheticLinearProtocol())
