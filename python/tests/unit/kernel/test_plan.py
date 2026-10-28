from __future__ import annotations

import pytest

from sdp.kernel.domain.errors import PlanInvalid
from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.science.plan import Backend, ComputePlan, ComputeTask, TaskKindRegistry, validate_plan

pytestmark = pytest.mark.tier("T1")
P = NumericalPolicy()


def test_topological_order_respects_dependencies_and_declaration_order() -> None:
    plan = ComputePlan(
        tasks=(ComputeTask("c", "k", P, ("a", "b")), ComputeTask("a", "k", P), ComputeTask("b", "k", P, ("a",)))
    )
    assert [t.task_id for t in plan.topological_order()] == ["a", "b", "c"]


def test_cycles_and_dangling_dependencies_are_invalid() -> None:
    with pytest.raises(PlanInvalid):
        ComputePlan(tasks=(ComputeTask("a", "k", P, ("b",)), ComputeTask("b", "k", P, ("a",))))
    with pytest.raises(PlanInvalid):
        ComputePlan(tasks=(ComputeTask("a", "k", P, ("ghost",)),))


@pytest.mark.req("KRN-012")
def test_plan_with_task_kind_lacking_cpu_reference_is_rejected() -> None:
    registry = TaskKindRegistry()
    registry.register("gpu.only", lambda p, u, c: 1, Backend.CUDA)
    registry.register("cpu.ok", lambda p, u, c: 1)
    plan = ComputePlan(tasks=(ComputeTask("a", "cpu.ok", P), ComputeTask("b", "gpu.only", P)))
    with pytest.raises(PlanInvalid, match=r"gpu\.only"):
        validate_plan(plan, registry)


def test_gpu_falls_back_to_cpu_reference() -> None:
    registry = TaskKindRegistry()
    registry.register("k", lambda p, u, c: "cpu")
    _impl, used = registry.implementation("k", Backend.CUDA)
    assert used is Backend.CPU


@pytest.mark.req("NFR-008")
def test_nondeterminism_is_documented_per_task_kind() -> None:
    registry = TaskKindRegistry()
    registry.register("k", lambda p, u, c: 1, Backend.CUDA, nondeterminism="atomic float reductions")
    registry.register("k", lambda p, u, c: 1)
    assert registry.nondeterminism("k") == "atomic float reductions"
