"""ComputePlan: a DAG of ComputeTasks, each naming a TaskKind and a NumericalPolicy (§4)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from sdp.kernel.domain.errors import InvariantViolation, PlanInvalid
from sdp.kernel.domain.policy import NumericalPolicy


class Backend(StrEnum):
    CPU = "cpu"
    CUDA = "cuda"


@dataclass(frozen=True)
class TaskContext:
    """What a task implementation may know: its seed, policy, backend and injected services.

    Services are kernel ports handed in by the executor (for example the ClusterSearchAlgorithm
    adapters or the CrossMatchService), so a pure implementation never reaches for infrastructure.
    """

    task_id: str
    seed: int
    policy: NumericalPolicy
    backend: Backend
    services: Mapping[str, Any] = field(default_factory=dict)

    def service(self, name: str) -> Any:
        try:
            return self.services[name]
        except KeyError:
            raise PlanInvalid(
                f"task {self.task_id} needs service {name!r}, which the executor does not provide"
            ) from None


class TaskImplementation(Protocol):
    """``(params, upstream results by task id, context) -> result``; pure."""

    def __call__(self, params: Mapping[str, Any], upstream: Mapping[str, Any], context: TaskContext) -> Any: ...


@dataclass(frozen=True)
class ComputeTask:
    task_id: str
    kind: str
    policy: NumericalPolicy
    depends_on: tuple[str, ...] = ()
    params: Mapping[str, Any] = field(default_factory=dict)
    seed_offset: int = 0


@dataclass(frozen=True)
class ComputePlan:
    tasks: tuple[ComputeTask, ...]
    outputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ids = [t.task_id for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise PlanInvalid("task ids must be unique")
        known = set(ids)
        for task in self.tasks:
            missing = [d for d in task.depends_on if d not in known]
            if missing:
                raise PlanInvalid(f"task {task.task_id} depends on unknown tasks {missing}")
        for output in self.outputs:
            if output not in known:
                raise PlanInvalid(f"plan output {output} is not a task")
        self.topological_order()

    def task(self, task_id: str) -> ComputeTask:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        raise KeyError(task_id)

    def topological_order(self) -> tuple[ComputeTask, ...]:
        """Kahn's algorithm, ties broken by declaration order so execution order is deterministic."""
        remaining = {t.task_id: set(t.depends_on) for t in self.tasks}
        order: list[ComputeTask] = []
        while remaining:
            ready = [t for t in self.tasks if t.task_id in remaining and not remaining[t.task_id]]
            if not ready:
                raise PlanInvalid(f"plan has a cycle among {sorted(remaining)}")
            for t in ready:
                order.append(t)
                del remaining[t.task_id]
            for deps in remaining.values():
                deps.difference_update(t.task_id for t in ready)
        return tuple(order)


@dataclass
class TaskKindRegistry:
    """TaskKind -> implementations by backend. A kind without a CPU reference is invalid (ADR-007)."""

    _impls: dict[str, dict[Backend, TaskImplementation]] = field(default_factory=dict)
    _nondeterminism: dict[str, str] = field(default_factory=dict)

    def register(
        self,
        kind: str,
        implementation: TaskImplementation | Callable[..., Any],
        backend: Backend = Backend.CPU,
        *,
        nondeterminism: str | None = None,
    ) -> None:
        slot = self._impls.setdefault(kind, {})
        if backend in slot:
            raise InvariantViolation(f"{kind} already has a {backend} implementation")
        slot[backend] = implementation
        if nondeterminism:
            self._nondeterminism[kind] = nondeterminism

    def merge(self, other: TaskKindRegistry) -> None:
        for kind, impls in other._impls.items():
            for backend, impl in impls.items():
                self.register(kind, impl, backend)
        self._nondeterminism.update(other._nondeterminism)

    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._impls))

    def has_cpu_reference(self, kind: str) -> bool:
        return Backend.CPU in self._impls.get(kind, {})

    def implementation(self, kind: str, backend: Backend) -> tuple[TaskImplementation, Backend]:
        """The implementation for ``backend``, falling back to the CPU reference."""
        impls = self._impls.get(kind)
        if not impls or Backend.CPU not in impls:
            raise PlanInvalid(f"TaskKind {kind!r} has no CPU reference implementation (KRN-012)")
        if backend in impls:
            return impls[backend], backend
        return impls[Backend.CPU], Backend.CPU

    def nondeterminism(self, kind: str) -> str | None:
        """Documented nondeterminism for a TaskKind (NFR-008)."""
        return self._nondeterminism.get(kind)


def validate_plan(plan: ComputePlan, registry: TaskKindRegistry) -> None:
    """Reject a plan that names a TaskKind without a CPU reference implementation (KRN-012)."""
    missing = sorted({t.kind for t in plan.tasks if not registry.has_cpu_reference(t.kind)})
    if missing:
        raise PlanInvalid(f"TaskKinds without a CPU reference implementation: {missing}")


def task_seed(seed: int, task: ComputeTask, all_tasks: Iterable[ComputeTask]) -> int:
    """A per-task seed derived deterministically from the experiment seed and the task's position."""
    index = [t.task_id for t in all_tasks].index(task.task_id)
    return (seed * 1_000_003 + index * 7919 + task.seed_offset) % (2**31 - 1)
