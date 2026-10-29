"""The science SPI's small contracts: plan validation, task seeds, the TaskKind registry, inputs and
results, product formats and the Investigate test registry. Each test pins one behaviour a mutant of
``sdp.kernel.science`` changed without any other suite noticing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pytest

from sdp.kernel.domain.errors import InvariantViolation, PlanInvalid
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.investigation import EvidenceBundle, NullFieldResult, TestResult, TestSelection
from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.science.investigation import (
    PRODUCTS_SERVICE,
    InvestigationTestRegistry,
    describe,
    null_task_kind,
    product_bytes,
    task_kind,
)
from sdp.kernel.science.io import ComputeResults, ResolvedInput, ResolvedInputs
from sdp.kernel.science.plan import Backend, ComputePlan, ComputeTask, TaskContext, TaskKindRegistry, task_seed
from sdp.kernel.science.products import lightcurve_csv, parse_lightcurve, parse_positions, positions_csv

pytestmark = pytest.mark.tier("T1")
P = NumericalPolicy()


def context(**services: Any) -> TaskContext:
    return TaskContext("t1", 7, P, Backend.CPU, services)


# Plans


def test_duplicate_task_ids_are_invalid() -> None:
    with pytest.raises(PlanInvalid, match=r"^task ids must be unique$"):
        ComputePlan(tasks=(ComputeTask("a", "k", P), ComputeTask("a", "k", P)))


def test_a_dangling_dependency_names_the_task_and_what_it_lacks() -> None:
    with pytest.raises(PlanInvalid, match=r"^task b depends on unknown tasks \['ghost'\]$"):
        ComputePlan(tasks=(ComputeTask("a", "k", P), ComputeTask("b", "k", P, ("a", "ghost"))))


def test_an_output_must_be_a_task() -> None:
    with pytest.raises(PlanInvalid, match=r"^plan output z is not a task$"):
        ComputePlan(tasks=(ComputeTask("a", "k", P),), outputs=("z",))
    assert ComputePlan(tasks=(ComputeTask("a", "k", P),), outputs=("a",)).outputs == ("a",)


def test_a_cycle_names_every_task_left_in_it() -> None:
    with pytest.raises(PlanInvalid, match=r"^plan has a cycle among \['b', 'c'\]$"):
        ComputePlan(
            tasks=(ComputeTask("a", "k", P), ComputeTask("b", "k", P, ("c",)), ComputeTask("c", "k", P, ("b",)))
        )


def test_a_valid_plan_orders_independent_tasks_by_declaration() -> None:
    plan = ComputePlan(tasks=(ComputeTask("z", "k", P), ComputeTask("a", "k", P), ComputeTask("m", "k", P, ("z",))))
    assert [t.task_id for t in plan.topological_order()] == ["z", "a", "m"]


def test_task_finds_by_id_and_raises_with_the_id() -> None:
    plan = ComputePlan(tasks=(ComputeTask("a", "k1", P), ComputeTask("b", "k2", P)))
    assert plan.task("b").kind == "k2"
    assert plan.task("a").kind == "k1"
    with pytest.raises(KeyError) as missing:
        plan.task("c")
    assert missing.value.args == ("c",)


def test_task_seeds_are_pinned_so_recorded_manifests_replay() -> None:
    """The derivation is part of every manifest's reproducibility: these values may never change."""
    tasks = (ComputeTask("a", "k", P), ComputeTask("b", "k", P, seed_offset=5))
    seeds = [task_seed(seed, t, tasks) for seed in (42, 10**6, 2**40) for t in tasks]
    assert seeds == [42000126, 42008050, 1423104145, 1423112069, 512001536, 512009460]


# Task contexts and the TaskKind registry


def test_a_missing_service_names_the_task_and_the_service() -> None:
    assert context(products={"x": b""}).service("products") == {"x": b""}
    with pytest.raises(PlanInvalid, match=r"^task t1 needs service 'clusters', which the executor does not provide$"):
        context().service("clusters")


def test_registering_a_backend_twice_is_refused() -> None:
    registry = TaskKindRegistry()
    registry.register("k", lambda p, u, c: 1)
    registry.register("k", lambda p, u, c: 2, Backend.CUDA)
    with pytest.raises(InvariantViolation, match=r"^k already has a cuda implementation$"):
        registry.register("k", lambda p, u, c: 3, Backend.CUDA)


def test_merge_copies_every_backend_and_the_nondeterminism_notes() -> None:
    cpu, cuda = (lambda p, u, c: "cpu"), (lambda p, u, c: "cuda")
    other = TaskKindRegistry()
    other.register("k", cpu, nondeterminism="atomic adds")
    other.register("k", cuda, Backend.CUDA)
    merged = TaskKindRegistry()
    merged.merge(other)
    assert merged.implementation("k", Backend.CPU) == (cpu, Backend.CPU)
    assert merged.implementation("k", Backend.CUDA) == (cuda, Backend.CUDA)
    assert merged.nondeterminism("k") == "atomic adds"


def test_kinds_are_sorted_and_cpu_reference_is_reported_per_kind() -> None:
    registry = TaskKindRegistry()
    registry.register("z", lambda p, u, c: 1)
    registry.register("a", lambda p, u, c: 1, Backend.CUDA)
    assert registry.kinds() == ("a", "z")
    assert registry.has_cpu_reference("z") and not registry.has_cpu_reference("a")
    assert not registry.has_cpu_reference("never-registered")


@pytest.mark.req("KRN-012")
def test_a_kind_with_only_a_gpu_implementation_has_no_implementation() -> None:
    registry = TaskKindRegistry()
    registry.register("gpu.only", lambda p, u, c: 1, Backend.CUDA)
    with pytest.raises(PlanInvalid, match=r"^TaskKind 'gpu\.only' has no CPU reference implementation \(KRN-012\)$"):
        registry.implementation("gpu.only", Backend.CUDA)
    with pytest.raises(PlanInvalid, match="no CPU reference"):
        registry.implementation("unknown", Backend.CPU)


# Inputs and results


def test_resolved_inputs_answer_by_role_and_name_a_missing_one() -> None:
    source = SourceRef(SourceKind.SYNTHETIC, "sdp.synthetic.linear", "Apache-2.0", "SDP synthetic generator")
    item = ResolvedInput("series", ArtifactId.of_bytes(b"[1]"), "application/json", source, [1])
    inputs = ResolvedInputs({"series": item})
    assert inputs["series"] == [1] and inputs.artifact("series") is item
    assert "series" in inputs and "other" not in inputs
    with pytest.raises(InvariantViolation, match=r"^no input bound to role 'other'$"):
        inputs["other"]


def test_compute_results_answer_by_task_id() -> None:
    results = ComputeResults({"fit": 1.5})
    assert results["fit"] == 1.5
    assert "fit" in results and "residuals" not in results


# Product formats


def test_product_parsing_skips_comment_lines_and_non_finite_rows() -> None:
    data = b"# curated by dialect x from raw y\nmjd,mag,mag_err\n2.0,11.0,0.1\n1.0,10.0,0.1\n3.0,nan,0.1\n"
    curve = parse_lightcurve(data)
    assert curve.mjd.tolist() == [1.0, 2.0] and curve.mag.tolist() == [10.0, 11.0]


def test_writers_refuse_columns_of_different_lengths() -> None:
    with pytest.raises(ValueError):
        lightcurve_csv(np.array([1.0, 2.0]), np.array([10.0]), np.array([0.1, 0.1]))
    with pytest.raises(ValueError):
        positions_csv(np.array([2000.0]), np.array([1.0, 2.0]), np.array([3.0]), np.array([4.0]))


def test_positions_round_trip_through_csv() -> None:
    data = positions_csv(
        np.array([2016.0, 2000.0]), np.array([10.0, 10.1]), np.array([-5.0, -5.1]), np.array([1.0, 2.0])
    )
    history = parse_positions(data)
    assert history.epoch_jyear.tolist() == [2000.0, 2016.0] and history.ra_deg.tolist() == [10.1, 10.0]


# The Investigate test registry


@dataclass(frozen=True)
class RecordingTest:
    """An InvestigationTest that records what the registry's TaskKinds hand it."""

    __test__ = False

    name: str = "recorder"
    version: str = "1.0.0"
    question: str = "What did the task pass?"
    policy: NumericalPolicy = P
    null_model: str = "nothing planted"
    inputs: tuple[str, ...] = ("catalogue:synthetic-gaia",)
    needs_products: tuple[str, ...] = ("lightcurve",)
    seen: list[Mapping[str, Any]] = field(default_factory=list, compare=False)

    def run(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Sequence[TestResult]:
        self.seen.append({"bundles": len(bundles), "thresholds": dict(thresholds)})
        return []

    def null(
        self,
        bundles: Sequence[EvidenceBundle],
        thresholds: Mapping[str, Any],
        context: TaskContext,
        trials: int,
        alpha: float,
    ) -> NullFieldResult:
        self.seen.append({"thresholds": dict(thresholds), "trials": trials, "alpha": alpha})
        return NullFieldResult(self.name, self.version, trials, 0, 0, alpha, self.null_model)


def test_describe_records_everything_the_manifest_needs() -> None:
    test = RecordingTest()
    assert describe(test) == {
        "name": "recorder",
        "version": "1.0.0",
        "question": "What did the task pass?",
        "policy": P.to_document(),
        "null_model": "nothing planted",
        "inputs": ["catalogue:synthetic-gaia"],
        "needs_products": ["lightcurve"],
        "task_kind": "investigate.recorder@1.0.0",
    }
    assert null_task_kind("recorder", "1.0.0") == "investigate.recorder@1.0.0.null"


def test_a_test_registers_once() -> None:
    registry = InvestigationTestRegistry.of(RecordingTest())
    with pytest.raises(InvariantViolation, match=r"^test recorder@1\.0\.0 is already registered$"):
        registry.register(RecordingTest())


@pytest.mark.req("INV-006")
def test_a_selection_may_not_ask_for_products_the_test_does_not_declare() -> None:
    registry = InvestigationTestRegistry.of(RecordingTest())
    assert registry.validate([TestSelection("recorder", "1.0.0", needs_products=("lightcurve",))]) == [RecordingTest()]
    with pytest.raises(
        PlanInvalid, match=r"^profile asks recorder@1.0.0 for products \['image', 'spectrum'\] the test"
    ):
        registry.validate([TestSelection("recorder", "1.0.0", needs_products=("spectrum", "image", "lightcurve"))])


@pytest.mark.req("INV-006")
def test_each_test_is_a_cpu_task_kind_for_its_run_and_its_null() -> None:
    kinds = InvestigationTestRegistry.of(RecordingTest()).task_kinds()
    assert kinds.kinds() == (task_kind("recorder", "1.0.0"), null_task_kind("recorder", "1.0.0"))
    assert all(kinds.has_cpu_reference(k) for k in kinds.kinds())


def test_the_task_kinds_pass_thresholds_through_and_default_to_none() -> None:
    test = RecordingTest()
    kinds = InvestigationTestRegistry.of(test).task_kinds()
    run, _ = kinds.implementation(task_kind("recorder", "1.0.0"), Backend.CPU)
    null, _ = kinds.implementation(null_task_kind("recorder", "1.0.0"), Backend.CPU)
    assert run({"bundles": []}, {}, context()) == []
    run({"bundles": [], "thresholds": {"score": 3}}, {}, context())
    null({"bundles": [], "trials": "4", "alpha": "0.05"}, {}, context())
    document = null({"bundles": [], "thresholds": {"score": 5}, "trials": 2, "alpha": 0.1}, {}, context())
    assert test.seen == [
        {"bundles": 0, "thresholds": {}},
        {"bundles": 0, "thresholds": {"score": 3}},
        {"thresholds": {}, "trials": 4, "alpha": 0.05},
        {"thresholds": {"score": 5}, "trials": 2, "alpha": 0.1},
    ]
    assert document["trials"] == 2


@pytest.mark.req("INV-005")
def test_a_task_reads_only_registered_products() -> None:
    ctx = context(**{PRODUCTS_SERVICE: {"art-1": b"mjd,mag,mag_err\n"}})
    assert product_bytes(ctx, "art-1") == b"mjd,mag,mag_err\n"
    with pytest.raises(PlanInvalid, match=r"^product art-2 is not among the registered artifacts this task may read$"):
        product_bytes(ctx, "art-2")
