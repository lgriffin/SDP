"""The Investigate test registry (Investigate §5, INV-006).

A test is a registered TaskKind: a CPU reference implementation, a NumericalPolicy, declared
inputs from the evidence bundle, named outputs and a null model. A profile selects tests by name
and version, and nothing outside the registry can run. Because each test is also registered in a
TaskKindRegistry, a test written for Investigate runs unchanged in a predeclared experiment.

Tests read evidence bundles only; they never receive a gateway, so a result is reproducible from
the registered artifacts alone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sdp.kernel.domain.errors import InvariantViolation, PlanInvalid
from sdp.kernel.domain.investigation import EvidenceBundle, NullFieldResult, TestResult, TestSelection
from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.science.plan import Backend, TaskContext, TaskKindRegistry


class InvestigationTest(Protocol):
    """One registered test. ``run`` and ``null`` are pure CPU reference implementations."""

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def question(self) -> str: ...

    @property
    def policy(self) -> NumericalPolicy: ...

    @property
    def null_model(self) -> str: ...

    @property
    def inputs(self) -> tuple[str, ...]:
        """Bundle fields read, e.g. ``catalogue:gaia-dr3`` or ``observations:dasch``."""
        ...

    @property
    def needs_products(self) -> tuple[str, ...]:
        """Product types the test needs fetched; empty means the observation index suffices."""
        ...

    def run(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Sequence[TestResult]: ...

    def null(
        self,
        bundles: Sequence[EvidenceBundle],
        thresholds: Mapping[str, Any],
        context: TaskContext,
        trials: int,
        alpha: float,
    ) -> NullFieldResult:
        """The same test on shuffled or synthetic versions of the field: an empirical false-positive
        rate for this profile on this field."""
        ...


#: The service through which a test reads the bytes of products its bundles name. The kernel fills
#: it from the artifact store (registered artifacts only), never from an archive.
PRODUCTS_SERVICE = "products"

#: Entry-point group through which test packages register (each entry is a zero-argument callable
#: returning an iterable of tests), the way science modules register through ``sdp.protocols``.
TEST_ENTRY_POINT_GROUP = "sdp.investigation_tests"


def product_bytes(context: TaskContext, artifact_id: str) -> bytes:
    """The bytes of a registered product a bundle names."""
    products: Mapping[str, bytes] = context.service(PRODUCTS_SERVICE)
    try:
        return products[artifact_id]
    except KeyError:
        raise PlanInvalid(f"product {artifact_id} is not among the registered artifacts this task may read") from None


def task_kind(name: str, version: str) -> str:
    return f"investigate.{name}@{version}"


def null_task_kind(name: str, version: str) -> str:
    return f"investigate.{name}@{version}.null"


def describe(test: InvestigationTest) -> dict[str, Any]:
    """What the investigation-set manifest records about a test (INV-002)."""
    return {
        "name": test.name,
        "version": test.version,
        "question": test.question,
        "policy": test.policy.to_document(),
        "null_model": test.null_model,
        "inputs": list(test.inputs),
        "needs_products": list(test.needs_products),
        "task_kind": task_kind(test.name, test.version),
    }


def _bundles(params: Mapping[str, Any]) -> list[EvidenceBundle]:
    return [EvidenceBundle.from_document(b) for b in params["bundles"]]


@dataclass
class InvestigationTestRegistry:
    """name@version -> test; every test is also a TaskKind with a CPU reference."""

    _tests: dict[str, InvestigationTest] = field(default_factory=dict)

    @classmethod
    def of(cls, *tests: InvestigationTest) -> InvestigationTestRegistry:
        registry = cls()
        for t in tests:
            registry.register(t)
        return registry

    def register(self, test: InvestigationTest) -> None:
        key = f"{test.name}@{test.version}"
        if key in self._tests:
            raise InvariantViolation(f"test {key} is already registered")
        if not test.null_model.strip():
            raise InvariantViolation(f"test {key} declares no null model (INV-006)")
        self._tests[key] = test

    def get(self, name: str, version: str) -> InvestigationTest:
        try:
            return self._tests[f"{name}@{version}"]
        except KeyError:
            raise PlanInvalid(f"test {name}@{version} is not registered; nothing outside the registry runs") from None

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._tests))

    def validate(self, selections: Sequence[TestSelection]) -> list[InvestigationTest]:
        """Every selected test is registered with a declared null model (INV-006)."""
        tests = [self.get(s.name, s.version) for s in selections]
        for s, t in zip(selections, tests, strict=True):
            missing = sorted(set(s.needs_products) - set(t.needs_products))
            if missing:
                raise PlanInvalid(f"profile asks {s.key} for products {missing} the test does not declare")
        return tests

    def task_kinds(self) -> TaskKindRegistry:
        """Each test's run and null as CPU TaskKinds over JSON-ready bundles."""
        registry = TaskKindRegistry()
        for test in self._tests.values():
            registry.register(task_kind(test.name, test.version), _RunTask(test), Backend.CPU)
            registry.register(null_task_kind(test.name, test.version), _NullTask(test), Backend.CPU)
        return registry


@dataclass(frozen=True)
class _RunTask:
    test: InvestigationTest

    def __call__(self, params: Mapping[str, Any], upstream: Mapping[str, Any], context: TaskContext) -> Any:
        results = self.test.run(_bundles(params), dict(params.get("thresholds", {})), context)
        return [r.to_document() for r in results]


@dataclass(frozen=True)
class _NullTask:
    test: InvestigationTest

    def __call__(self, params: Mapping[str, Any], upstream: Mapping[str, Any], context: TaskContext) -> Any:
        result = self.test.null(
            _bundles(params),
            dict(params.get("thresholds", {})),
            context,
            int(params["trials"]),
            float(params["alpha"]),
        )
        return result.to_document()
