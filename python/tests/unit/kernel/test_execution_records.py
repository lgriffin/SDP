"""Records for the in-process execution loop: process usage on both platforms, the policy cast,
the task context, floating-point errors, failure diagnostics and resource deltas (ADR-012)."""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Iterator, Mapping
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from sdp.kernel.application import execution
from sdp.kernel.application.execution import apply_policy, process_usage, run_plan
from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.ports.compute import TaskFailed
from sdp.kernel.science.plan import Backend, ComputePlan, ComputeTask, TaskContext, TaskKindRegistry

pytestmark = pytest.mark.tier("T1")

COUNTER_FIELDS = [
    "cb",
    "PageFaultCount",
    "PeakWorkingSetSize",
    "WorkingSetSize",
    "QuotaPeakPagedPoolUsage",
    "QuotaPagedPoolUsage",
    "QuotaPeakNonPagedPoolUsage",
    "QuotaNonPagedPoolUsage",
    "PagefileUsage",
    "PeakPagefileUsage",
]


class FakeWindll:
    """kernel32 and psapi as the Windows branch calls them; psapi checks what it is handed."""

    PROCESS = object()

    def __init__(self, ok: int) -> None:
        self.ok = ok
        self.calls: list[tuple[str, int]] = []
        self.kernel32 = self
        self.psapi = self

    def GetCurrentProcess(self) -> object:  # noqa: N802 - the Win32 name
        return self.PROCESS

    def GetProcessMemoryInfo(self, process: object, ref: Any, cb: int) -> int:  # noqa: N802
        counters = ref._obj
        assert process is self.PROCESS
        assert [name for name, _ in type(counters)._fields_] == COUNTER_FIELDS
        assert cb == counters.cb == ctypes.sizeof(counters)
        counters.PeakWorkingSetSize = 3 * 1024 * 1024
        self.calls.append(("GetProcessMemoryInfo", cb))
        return self.ok


def on_windows(monkeypatch: pytest.MonkeyPatch, ok: int) -> FakeWindll:
    windll = FakeWindll(ok)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)
    monkeypatch.setattr(os, "times", lambda: os.times_result((1.5, 0.25, 0.0, 0.0, 0.0)))
    return windll


def test_windows_usage_reads_the_peak_working_set_in_kib(monkeypatch: pytest.MonkeyPatch) -> None:
    windll = on_windows(monkeypatch, ok=1)
    assert process_usage() == (1.5, 0.25, 3072.0)
    assert [name for name, _ in windll.calls] == ["GetProcessMemoryInfo"]


def test_windows_usage_reports_no_peak_when_the_call_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    on_windows(monkeypatch, ok=0)
    assert process_usage() == (1.5, 0.25, 0.0)


def test_posix_usage_reads_rusage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    user, system, peak = process_usage()
    assert user >= 0.0 and system >= 0.0 and peak > 0.0
    assert isinstance(peak, float)


def test_a_float32_policy_casts_a_float_array() -> None:
    value = apply_policy(np.ones(3), NumericalPolicy(dtype="float32"))
    assert value.dtype == np.float32


def test_a_matching_policy_returns_the_array_itself() -> None:
    array = np.ones(3, dtype=np.float32)
    assert apply_policy(array, NumericalPolicy(dtype="float32")) is array


def test_a_policy_leaves_integer_arrays_alone() -> None:
    array = np.arange(3)
    assert apply_policy(array, NumericalPolicy(dtype="float32")) is array


class Sink:
    def __init__(self, done: Mapping[str, Any] | None = None) -> None:
        self.done = dict(done or {})
        self.writes: list[tuple[str, Any, float, dict[str, float]]] = []

    def completed(self, task_id: str) -> tuple[bool, Any]:
        return (task_id in self.done, self.done.get(task_id))

    def write(self, task: ComputeTask, value: Any, duration_s: float, resources: Mapping[str, float]) -> None:
        self.writes.append((task.task_id, value, duration_s, dict(resources)))


POLICY = NumericalPolicy(dtype="float64", rtol=1e-6)


def run(
    registry: TaskKindRegistry, *tasks: ComputeTask, sink: Sink | None = None, backend: Backend = Backend.CPU
) -> Any:
    return run_plan(ComputePlan(tasks), registry, backend=backend, seed=7, sink=sink or Sink(), services={"s": 1})


def test_the_context_names_the_task_policy_and_backend_used() -> None:
    seen: list[tuple[str, TaskContext]] = []
    registry = TaskKindRegistry()
    registry.register("k", lambda p, u, c: seen.append(("cpu", c)))
    registry.register("k", lambda p, u, c: seen.append(("cuda", c)), Backend.CUDA)
    run(registry, ComputeTask("t", "k", POLICY), backend=Backend.CUDA)
    ((where, context),) = seen
    assert where == "cuda"
    assert (context.task_id, context.policy, context.backend, dict(context.services)) == (
        "t",
        POLICY,
        Backend.CUDA,
        {"s": 1},
    )


@pytest.mark.parametrize(
    "compute",
    [
        lambda: np.float64(1e308) * np.float64(10.0),
        lambda: np.sqrt(np.float64(-1.0)),
        lambda: np.float64(1.0) / np.float64(0.0),
    ],
    ids=["overflow", "invalid", "divide"],
)
def test_floating_point_errors_raise(compute: Any) -> None:
    registry = TaskKindRegistry()
    registry.register("k", lambda p, u, c: compute())
    with pytest.raises(TaskFailed) as failed:
        run(registry, ComputeTask("t", "k", POLICY))
    assert type(failed.value.cause) is FloatingPointError


def deep(n: int) -> None:
    if n == 0:
        raise ValueError("bottom")
    deeper(n - 1)


def deeper(n: int) -> None:
    deep(n)


def test_a_failure_carries_a_bounded_traceback_the_completed_tasks_and_the_kind() -> None:
    registry = TaskKindRegistry()
    registry.register("ok", lambda p, u, c: 1)
    registry.register("bad", lambda p, u, c: deep(12))
    with pytest.raises(TaskFailed) as failed:
        run(registry, ComputeTask("a", "ok", POLICY), ComputeTask("b", "bad", POLICY, depends_on=("a",)))
    diagnostics = failed.value.diagnostics
    assert set(diagnostics) == {"traceback", "completed_tasks", "task_kind"}
    assert diagnostics["traceback"].count('  File "') == 8
    assert diagnostics["completed_tasks"] == ["a"]
    assert diagnostics["task_kind"] == "bad"


def ticks(*values: Any) -> Iterator[Any]:
    yield from values


def test_a_checkpoint_records_the_duration_and_resource_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = ticks(10.0, 12.5)
    usage = ticks((1.0, 0.5, 100.0), (1.75, 0.75, 200.0))
    monkeypatch.setattr(execution, "time", SimpleNamespace(perf_counter=lambda: next(clock)))
    monkeypatch.setattr(execution, "process_usage", lambda: next(usage))
    registry = TaskKindRegistry()
    registry.register("k", lambda p, u, c: 3)
    sink = Sink()
    run(registry, ComputeTask("t", "k", POLICY), sink=sink)
    assert sink.writes == [("t", 3, 2.5, {"cpu_user_s": 0.75, "cpu_system_s": 0.25, "maxrss_kb": 200.0})]
