"""ComputeExecutor, ClusterSearchAlgorithm and EnvironmentProbe (§6)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from sdp.kernel.domain.experiment import EnvironmentRecord
from sdp.kernel.science.plan import Backend, ComputePlan, ComputeTask, TaskKindRegistry


class CheckpointSink(Protocol):
    """How the executor hands completed work back to the kernel-owned runner (ADR-012)."""

    def completed(self, task_id: str) -> tuple[bool, Any]:
        """Whether the task already has a checkpoint, and its value if so (resume)."""
        ...

    def write(self, task: ComputeTask, value: Any, duration_s: float, resources: Mapping[str, float]) -> None:
        """Persist a checkpoint and emit CheckpointWritten (KRN-005)."""
        ...


class TaskFailed(Exception):
    """Raised by an executor when a task raises; carries what the runner needs to preserve."""

    def __init__(self, task_id: str, cause: BaseException, diagnostics: Mapping[str, Any]) -> None:
        super().__init__(f"task {task_id} failed: {type(cause).__name__}: {cause}")
        self.task_id = task_id
        self.cause = cause
        self.diagnostics = dict(diagnostics)


class ComputeExecutor(Protocol):
    backend: Backend

    def execute(
        self,
        plan: ComputePlan,
        registry: TaskKindRegistry,
        *,
        seed: int,
        sink: CheckpointSink,
        services: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run every task in topological order, honouring each task's NumericalPolicy, skipping
        tasks the sink reports as completed. Returns task id -> value. Raises TaskFailed."""
        ...


@dataclass(frozen=True)
class ClusterLabels:
    """Labels (-1 for noise), membership probabilities, and the configuration that produced them."""

    labels: np.ndarray
    probabilities: np.ndarray
    algorithm_id: str
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def n_clusters(self) -> int:
        return len({int(x) for x in self.labels} - {-1})


class ClusterSearchAlgorithm(Protocol):
    algorithm_id: str
    backend: Backend

    def search(self, features: np.ndarray, params: Mapping[str, Any], seed: int) -> ClusterLabels: ...


class EnvironmentProbe(Protocol):
    """Code revision, lock digest, image digest and backend versions for the manifest (KRN-004)."""

    def record(self, backend: Backend) -> EnvironmentRecord: ...
