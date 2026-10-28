"""SynchronousExecutor (the in-process CPU reference executor), FakeClusterer, FakeEnvironmentProbe."""

from __future__ import annotations

import platform
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from sdp.kernel.application.execution import run_plan
from sdp.kernel.domain.experiment import EnvironmentRecord
from sdp.kernel.ports.compute import CheckpointSink, ClusterLabels
from sdp.kernel.science.plan import Backend, ComputePlan, TaskKindRegistry


@dataclass
class SynchronousExecutor:
    backend: Backend = Backend.CPU
    executed: list[str] = field(default_factory=list)

    def execute(
        self,
        plan: ComputePlan,
        registry: TaskKindRegistry,
        *,
        seed: int,
        sink: CheckpointSink,
        services: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        executor = self

        class _Recording:
            def completed(self, task_id: str) -> tuple[bool, Any]:
                return sink.completed(task_id)

            def write(self, task: Any, value: Any, duration_s: float, resources: Mapping[str, float]) -> None:
                executor.executed.append(task.task_id)
                sink.write(task, value, duration_s, resources)

        return run_plan(plan, registry, backend=self.backend, seed=seed, sink=_Recording(), services=services)


@dataclass
class FakeClusterer:
    """Planted labels when given; otherwise deterministic single-linkage within ``eps``.

    Good enough to exercise the stellar module's plumbing and to pass the ClusterSearchAlgorithm
    contract suite on well-separated fixtures, which is all a fake is for.
    """

    planted: np.ndarray | None = None
    algorithm_id: str = "fake-planted"
    backend: Backend = Backend.CPU

    def search(self, features: np.ndarray, params: Mapping[str, Any], seed: int) -> ClusterLabels:
        n = features.shape[0]
        if self.planted is not None:
            if len(self.planted) != n:
                raise ValueError("planted labels do not match the feature matrix")
            labels = np.asarray(self.planted, dtype=np.int64)
        else:
            labels = _single_linkage(features, float(params.get("eps", 0.5)), int(params.get("min_cluster_size", 5)))
        probabilities = np.where(labels >= 0, 1.0, 0.0)
        return ClusterLabels(labels, probabilities, self.algorithm_id, dict(params))


def _single_linkage(x: np.ndarray, eps: float, min_size: int) -> np.ndarray:
    n = x.shape[0]
    parent = np.arange(n)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = int(parent[i])
        return i

    for i in range(n):
        d = np.linalg.norm(x[i + 1 :] - x[i], axis=1)
        for j in np.nonzero(d <= eps)[0]:
            a, b = find(i), find(int(i + 1 + j))
            if a != b:
                parent[max(a, b)] = min(a, b)
    roots = np.array([find(i) for i in range(n)])
    labels = np.full(n, -1, dtype=np.int64)
    next_label = 0
    for root in sorted(set(roots.tolist()), key=lambda r: int(np.nonzero(roots == r)[0][0])):
        members = np.nonzero(roots == root)[0]
        if len(members) >= min_size:
            labels[members] = next_label
            next_label += 1
    return labels


@dataclass
class FakeEnvironmentProbe:
    code_revision: str = "0" * 40
    lock_digest: str = "sha256:" + "0" * 64
    constants_version: str = "codata2018"

    def record(self, backend: Backend) -> EnvironmentRecord:
        cuda = backend is Backend.CUDA
        return EnvironmentRecord(
            code_revision=self.code_revision,
            lock_digest=self.lock_digest,
            backend=backend.value,
            python_version=platform.python_version(),
            platform=f"{platform.system().lower()}-{platform.machine()}",
            driver_version="fake-driver" if cuda else None,
            cuda_version="fake-cuda" if cuda else None,
            agreement_test_id="fake-agreement" if cuda else None,
            constants_version=self.constants_version,
        )
