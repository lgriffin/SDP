"""The in-process reference execution loop shared by the CPU executor and the test executor (ADR-012).

It runs a plan in topological order, resumes from the sink's checkpoints, honours each task's
NumericalPolicy, raises on floating-point exceptions and wraps any task error in TaskFailed.
"""

from __future__ import annotations

import sys
import time
import traceback
from collections.abc import Mapping
from typing import Any

import numpy as np

from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.ports.compute import CheckpointSink, TaskFailed
from sdp.kernel.science.plan import Backend, ComputePlan, TaskContext, TaskKindRegistry, task_seed


def process_usage() -> tuple[float, float, float]:
    """This process's (user CPU seconds, system CPU seconds, peak resident memory in KiB).

    ``resource`` is POSIX-only; on Windows the CPU times come from ``os.times`` and the peak from
    the process's peak working set, so the desktop build records the same fields."""
    if sys.platform == "win32":
        import ctypes
        import os
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
        times = os.times()
        return times.user, times.system, counters.PeakWorkingSetSize / 1024 if ok else 0.0
    import resource

    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime, usage.ru_stime, float(usage.ru_maxrss)


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
        user_before, system_before, _ = process_usage()
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
        user_after, system_after, maxrss_kb = process_usage()
        resources = {
            "cpu_user_s": user_after - user_before,
            "cpu_system_s": system_after - system_before,
            "maxrss_kb": maxrss_kb,
        }
        sink.write(task, value, duration, resources)
        values[task.task_id] = value
    return values
