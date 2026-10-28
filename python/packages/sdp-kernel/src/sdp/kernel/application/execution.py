"""The in-process reference execution loop shared by the CPU executor and the test executor (ADR-012).

It runs a plan in topological order, resumes from the sink's checkpoints, honours each task's
NumericalPolicy, raises on floating-point exceptions and wraps any task error in TaskFailed.
"""

from __future__ import annotations

import resource
import time
import traceback
from collections.abc import Mapping
from typing import Any

import numpy as np

from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.ports.compute import CheckpointSink, TaskFailed
from sdp.kernel.science.plan import Backend, ComputePlan, TaskContext, TaskKindRegistry, task_seed


def apply_policy(value: Any, policy: NumericalPolicy) -> Any:
    """Honour a policy's dtype on floating-point array results."""
    if isinstance(value, np.ndarray) and value.dtype.kind == "f" and policy.dtype in ("float32", "float64"):
        return value.astype(policy.dtype, copy=False)
    return value


def run_plan(
    plan: ComputePlan,
    registry: TaskKindRegistry,
    *,
    backend: Backend,
    seed: int,
    sink: CheckpointSink,
    services: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Execute a plan in topological order in this process, resuming from the sink's checkpoints."""
    values: dict[str, Any] = {}
    for task in plan.topological_order():
        done, value = sink.completed(task.task_id)
        if done:
            values[task.task_id] = value
            continue
        implementation, used = registry.implementation(task.kind, backend)
        context = TaskContext(
            task_id=task.task_id,
            seed=task_seed(seed, task, plan.tasks),
            policy=task.policy,
            backend=used,
            services=dict(services or {}),
        )
        upstream = {d: values[d] for d in task.depends_on}
        before = resource.getrusage(resource.RUSAGE_SELF)
        started = time.perf_counter()
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                value = apply_policy(implementation(task.params, upstream, context), task.policy)
        except Exception as exc:
            raise TaskFailed(
                task.task_id,
                exc,
                {
                    "traceback": traceback.format_exc(limit=8),
                    "completed_tasks": sorted(values),
                    "task_kind": task.kind,
                },
            ) from exc
        duration = time.perf_counter() - started
        after = resource.getrusage(resource.RUSAGE_SELF)
        resources = {
            "cpu_user_s": after.ru_utime - before.ru_utime,
            "cpu_system_s": after.ru_stime - before.ru_stime,
            "maxrss_kb": float(after.ru_maxrss),
        }
        sink.write(task, value, duration, resources)
        values[task.task_id] = value
    return values
