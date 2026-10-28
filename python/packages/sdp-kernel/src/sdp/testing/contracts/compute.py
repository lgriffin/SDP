"""ComputeExecutor and ClusterSearchAlgorithm contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pytest

from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.ports.compute import ClusterSearchAlgorithm, ComputeExecutor, TaskFailed
from sdp.kernel.science.plan import ComputePlan, ComputeTask, TaskKindRegistry

POLICY = NumericalPolicy()


class RecordingSink:
    def __init__(self, preloaded: Mapping[str, Any] | None = None) -> None:
        self.preloaded = dict(preloaded or {})
        self.written: dict[str, Any] = {}

    def completed(self, task_id: str) -> tuple[bool, Any]:
        if task_id in self.preloaded:
            return True, self.preloaded[task_id]
        return False, None

    def write(self, task: ComputeTask, value: Any, duration_s: float, resources: Mapping[str, float]) -> None:
        assert duration_s >= 0
        self.written[task.task_id] = value


def _registry() -> TaskKindRegistry:
    r = TaskKindRegistry()
    r.register("k.const", lambda p, u, c: float(p["v"]))
    r.register("k.add", lambda p, u, c: sum(u.values()))
    r.register("k.seed", lambda p, u, c: c.seed)
    r.register("k.boom", lambda p, u, c: 1 / 0)
    r.register("k.array", lambda p, u, c: np.arange(3, dtype=np.float32))
    return r


DIAMOND = ComputePlan(
    tasks=(
        ComputeTask("a", "k.const", POLICY, (), {"v": 1}),
        ComputeTask("b", "k.const", POLICY, ("a",), {"v": 2}),
        ComputeTask("c", "k.add", POLICY, ("a", "b")),
    ),
    outputs=("c",),
)


class ComputeExecutorContract:
    @pytest.mark.req("KRN-005")
    def test_runs_every_task_and_checkpoints_each(self, executor: ComputeExecutor) -> None:
        sink = RecordingSink()
        values = executor.execute(DIAMOND, _registry(), seed=1, sink=sink)
        assert values["c"] == 3.0
        assert set(sink.written) == {"a", "b", "c"}

    @pytest.mark.req("KRN-006")
    def test_skips_tasks_the_sink_already_holds(self, executor: ComputeExecutor) -> None:
        sink = RecordingSink(preloaded={"a": 10.0, "b": 20.0})
        values = executor.execute(DIAMOND, _registry(), seed=1, sink=sink)
        assert values["c"] == 30.0
        assert set(sink.written) == {"c"}

    def test_seeds_are_deterministic_and_distinct_per_task(self, executor: ComputeExecutor) -> None:
        plan = ComputePlan(tasks=(ComputeTask("s1", "k.seed", POLICY), ComputeTask("s2", "k.seed", POLICY)))
        v1 = executor.execute(plan, _registry(), seed=5, sink=RecordingSink())
        v2 = executor.execute(plan, _registry(), seed=5, sink=RecordingSink())
        assert v1 == v2 and v1["s1"] != v1["s2"]

    @pytest.mark.req("KRN-007")
    def test_task_errors_surface_as_task_failed_with_diagnostics(self, executor: ComputeExecutor) -> None:
        plan = ComputePlan(
            tasks=(ComputeTask("ok", "k.const", POLICY, (), {"v": 1}), ComputeTask("bad", "k.boom", POLICY, ("ok",)))
        )
        sink = RecordingSink()
        with pytest.raises(TaskFailed) as info:
            executor.execute(plan, _registry(), seed=1, sink=sink)
        assert info.value.task_id == "bad"
        assert "ok" in sink.written
        assert info.value.diagnostics

    def test_policy_dtype_is_honoured(self, executor: ComputeExecutor) -> None:
        plan = ComputePlan(tasks=(ComputeTask("arr", "k.array", NumericalPolicy(dtype="float64")),))
        values = executor.execute(plan, _registry(), seed=1, sink=RecordingSink())
        assert values["arr"].dtype == np.float64

    def test_services_reach_tasks(self, executor: ComputeExecutor) -> None:
        r = TaskKindRegistry()
        r.register("k.svc", lambda p, u, c: c.service("answer"))
        plan = ComputePlan(tasks=(ComputeTask("q", "k.svc", POLICY),))
        assert executor.execute(plan, r, seed=0, sink=RecordingSink(), services={"answer": 42})["q"] == 42


def blobs(seed: int = 3) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    centres = np.array([[0.0, 0.0, 0.0], [10.0, 10.0, 0.0], [-10.0, 5.0, 5.0]])
    x = np.vstack([c + rng.normal(0, 0.2, (40, 3)) for c in centres] + [rng.uniform(-30, 30, (10, 3))])
    truth = np.concatenate([np.full(40, i) for i in range(3)] + [np.full(10, -1)])
    return x, truth


def agreement(a: np.ndarray, b: np.ndarray) -> float:
    """Pair-counting Rand index; invariant to relabelling."""
    same_a = a[:, None] == a[None, :]
    same_b = b[:, None] == b[None, :]
    return float(np.mean(same_a == same_b))


class ClusterSearchContract:
    """Run with ``cluster_params``: hyperparameters that separate the three-blob fixture."""

    def test_labels_and_probabilities_have_one_value_per_row(
        self, clusterer: ClusterSearchAlgorithm, cluster_params: Mapping[str, Any]
    ) -> None:
        x, _ = blobs()
        result = clusterer.search(x, cluster_params, seed=1)
        assert result.labels.shape == (x.shape[0],)
        assert result.probabilities.shape == (x.shape[0],)
        assert np.all((result.probabilities >= 0) & (result.probabilities <= 1))
        assert np.all(result.probabilities[result.labels == -1] == 0)

    def test_recovers_well_separated_blobs(
        self, clusterer: ClusterSearchAlgorithm, cluster_params: Mapping[str, Any]
    ) -> None:
        x, truth = blobs()
        result = clusterer.search(x, cluster_params, seed=1)
        assert result.n_clusters == 3
        core = truth >= 0
        assert agreement(result.labels[core], truth[core]) > 0.99

    def test_deterministic_for_a_seed(
        self, clusterer: ClusterSearchAlgorithm, cluster_params: Mapping[str, Any]
    ) -> None:
        x, _ = blobs()
        a = clusterer.search(x, cluster_params, seed=7)
        b = clusterer.search(x, cluster_params, seed=7)
        assert np.array_equal(a.labels, b.labels)

    @pytest.mark.req("STL-006")
    def test_row_permutation_leaves_labels_invariant_up_to_relabelling(
        self, clusterer: ClusterSearchAlgorithm, cluster_params: Mapping[str, Any]
    ) -> None:
        x, truth = blobs()
        perm = np.random.default_rng(11).permutation(x.shape[0])
        a = clusterer.search(x, cluster_params, seed=1).labels
        b = clusterer.search(x[perm], cluster_params, seed=1).labels
        inverse = np.argsort(perm)
        core = truth >= 0
        assert agreement(a[core], b[inverse][core]) > 0.99
