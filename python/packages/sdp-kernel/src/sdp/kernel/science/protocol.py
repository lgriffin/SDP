"""ScientificProtocol: the plugin port science modules implement (§4, ADR-001).

Every method is pure: inputs in, values out, no I/O, no clock, no randomness beyond the seed
it is handed. Modules are discovered through the ``sdp.protocols`` entry-point group.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sdp.kernel.domain.findings import Findings
from sdp.kernel.domain.ids import ProtocolId
from sdp.kernel.domain.predeclaration import Predeclaration
from sdp.kernel.domain.verdict import ValidationReport
from sdp.kernel.science.io import ComputeResults, InputSpec, Params, ResolvedInputs
from sdp.kernel.science.plan import ComputePlan, TaskKindRegistry

ENTRY_POINT_GROUP = "sdp.protocols"


@runtime_checkable
class ScientificProtocol(Protocol):
    protocol_id: ProtocolId

    def predeclare(self) -> Predeclaration:
        """Hypotheses, gates, oracle tolerances, target regions or sources.
        Hashed and frozen before any data are touched."""
        ...

    def required_inputs(self) -> tuple[InputSpec, ...]:
        """What artifacts an experiment must supply, by role and media type."""
        ...

    def task_kinds(self) -> TaskKindRegistry:
        """The TaskKinds this module's plans use, each with a CPU reference implementation."""
        ...

    def plan(self, inputs: ResolvedInputs, params: Params, seed: int) -> ComputePlan:
        """A DAG of ComputeTasks; each names a TaskKind, a NumericalPolicy and its inputs."""
        ...

    def interpret(self, results: ComputeResults) -> Findings:
        """Numbers to scientific statements: fitted amplitudes, candidate lists, metrics."""
        ...

    def validate(self, findings: Findings, predeclaration: Predeclaration, *, experiment_id: str) -> ValidationReport:
        """Evaluates every predeclared gate; returns check values, thresholds and a verdict."""
        ...
