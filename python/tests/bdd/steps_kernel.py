"""Kernel steps. The seam is the use-case tier's: the kernel wired to the fakes of ``sdp.testing``.

A step drives the kernel only through a driving port (``kernel.<use case>.execute``) and reads back only
through ``query_evidence`` and the fakes' own inspection methods; it never patches a kernel object, so
a scenario fails for a bug anywhere between the port and the fakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sdp.kernel.domain.canonical import canonical_json, sha256_id
from sdp.kernel.domain.errors import PredeclarationNotFrozen
from sdp.kernel.domain.experiment import Experiment, ExperimentState
from sdp.kernel.domain.predeclaration import FrozenPredeclaration, Predeclaration
from sdp.kernel.ports.driving import DefineExperimentCommand, FreezePredeclarationCommand, RegisterHypothesisCommand
from sdp.testing.synthetic import SyntheticLinearProtocol, make_series
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

from .runner import given, then, when


@dataclass
class KernelWorld:
    """What one scenario has done to the kernel so far."""

    protocol: SyntheticLinearProtocol = field(default_factory=SyntheticLinearProtocol)
    k: InMemoryKernel = field(init=False)
    predeclaration: Predeclaration | None = None
    frozen: FrozenPredeclaration | None = None
    experiment: Experiment | None = None
    error: Exception | None = None

    def __post_init__(self) -> None:
        self.k = build_in_memory_kernel(self.protocol)

    def define(self, predeclaration_hash: str, **parameters: Any) -> Experiment:
        series = self.k.store.put(make_series(), "application/json")
        return self.k.kernel.define_experiment.execute(
            DefineExperimentCommand(
                self.protocol.protocol_id.name, predeclaration_hash, {"series": series}, parameters, seed=42
            )
        )

    def events(self) -> list[str]:
        return [stored.event.name for stored in self.k.kernel.query_evidence.events()]

    def manifest(self) -> dict[str, Any]:
        assert self.experiment is not None
        manifest = self.k.kernel.query_evidence.manifest(self.experiment.experiment_id)
        assert manifest is not None
        return manifest


def world(ctx: dict[str, Any]) -> KernelWorld:
    if "kernel" not in ctx:
        ctx["kernel"] = KernelWorld()
    kernel: KernelWorld = ctx["kernel"]
    return kernel


# Given


@given('the hypothesis "{hid}" is registered as "{statement}"')
def _registered(ctx: dict[str, Any], hid: str, statement: str) -> None:
    world(ctx).k.kernel.register_hypothesis.execute(RegisterHypothesisCommand(hid, statement))


@given("a predeclaration for the synthetic linear protocol")
def _predeclaration(ctx: dict[str, Any]) -> None:
    w = world(ctx)
    w.predeclaration = w.protocol.predeclare()


@given("a frozen predeclaration for the synthetic linear protocol")
def _frozen(ctx: dict[str, Any]) -> None:
    w = world(ctx)
    w.frozen, _ = w.k.kernel.freeze_predeclaration.execute(
        FreezePredeclarationCommand(w.protocol.predeclare(), "leigh")
    )


@given("an experiment defined against the predeclaration without freezing it")
def _unfrozen_experiment(ctx: dict[str, Any]) -> None:
    w = world(ctx)
    assert w.predeclaration is not None
    w.experiment = w.define(w.predeclaration.compute_hash())


# When


@when('the researcher edits "{hid}" to "{statement}"')
def _edit(ctx: dict[str, Any], hid: str, statement: str) -> None:
    world(ctx).k.kernel.register_hypothesis.execute(RegisterHypothesisCommand(hid, statement))


@when('"{signer}" freezes the predeclaration')
def _freeze(ctx: dict[str, Any], signer: str) -> None:
    w = world(ctx)
    assert w.predeclaration is not None
    w.frozen, _ = w.k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(w.predeclaration, signer))


@when("the researcher runs the experiment")
def _run(ctx: dict[str, Any]) -> None:
    w = world(ctx)
    assert w.experiment is not None
    try:
        w.experiment = w.k.kernel.run_experiment.execute(w.experiment.experiment_id)
    except PredeclarationNotFrozen as error:
        w.error = error


@when("the researcher defines and runs an experiment")
def _define_and_run(ctx: dict[str, Any]) -> None:
    w = world(ctx)
    assert w.frozen is not None
    experiment = w.define(w.frozen.hash)
    w.experiment = w.k.kernel.run_experiment.execute(experiment.experiment_id)


@when('the researcher defines and runs an experiment in which the task "{task}" fails')
def _define_and_fail(ctx: dict[str, Any], task: str) -> None:
    w = world(ctx)
    assert w.frozen is not None
    experiment = w.define(w.frozen.hash, inject_failure=task)
    w.experiment = w.k.kernel.run_experiment.execute(experiment.experiment_id)


# Then


@then('"{hid}" has versions 1 and 2 in order')
def _versions(ctx: dict[str, Any], hid: str) -> None:
    versions = world(ctx).k.kernel.query_evidence.hypothesis_versions(hid)
    assert [h.version for h in versions] == [1, 2]
    assert versions[0].statement != versions[1].statement


@then('the current version of "{hid}" is {version:d}')
def _current(ctx: dict[str, Any], hid: str, version: int) -> None:
    current = world(ctx).k.kernel.query_evidence.hypotheses()
    assert [h.version for h in current if h.hypothesis_id == hid] == [version]


@then("the frozen hash is the SHA-256 of the predeclaration's canonical JSON")
def _hash(ctx: dict[str, Any]) -> None:
    w = world(ctx)
    assert w.frozen is not None and w.predeclaration is not None
    assert w.frozen.hash == sha256_id(canonical_json(w.predeclaration.to_document()).encode())


@then('the ledger holds one predeclaration entry for that hash signed by "{signer}"')
def _one_entry(ctx: dict[str, Any], signer: str) -> None:
    w = world(ctx)
    assert w.frozen is not None
    entries = w.k.ledger.entries()
    assert len(entries) == 1
    entry = w.k.ledger.find(w.frozen.hash, "predeclaration")
    assert entry == entries[0] and entry.signer == signer and entry.signature
    assert w.events().count("PredeclarationFrozen") == 1


@then("the ledger verifies")
def _verifies(ctx: dict[str, Any]) -> None:
    world(ctx).k.ledger.verify()


@then("the run is refused because the predeclaration is not frozen")
def _refused(ctx: dict[str, Any]) -> None:
    assert isinstance(world(ctx).error, PredeclarationNotFrozen)


@then("the experiment is stored as refused, naming the missing hash")
def _stored_refused(ctx: dict[str, Any]) -> None:
    w = world(ctx)
    assert w.experiment is not None and w.predeclaration is not None
    stored = w.k.kernel.query_evidence.experiment(w.experiment.experiment_id)
    assert stored is not None and stored.state is ExperimentState.REFUSED
    assert stored.refusal and w.predeclaration.compute_hash() in stored.refusal
    assert "ExperimentRefused" in w.events()
    assert w.manifest()["execution"]["state"] == "refused"


@then("no compute task was executed")
def _nothing_ran(ctx: dict[str, Any]) -> None:
    assert not world(ctx).k.executor.executed


@then("the experiment is completed")
def _completed(ctx: dict[str, Any]) -> None:
    experiment = world(ctx).experiment
    assert experiment is not None and experiment.state is ExperimentState.COMPLETED


@then("the tasks {tasks} have stored checkpoints")
def _checkpoints(ctx: dict[str, Any], tasks: str) -> None:
    w = world(ctx)
    assert w.experiment is not None
    expected = {t.strip().strip('"') for t in tasks.replace(" and ", ",").split(",")}
    checkpoints = w.experiment.completed_checkpoints()
    assert set(checkpoints) == expected
    assert all(w.k.store.exists(artifact) for artifact in checkpoints.values())


@then('the event "{name}" was emitted {times:d} times')
def _emitted(ctx: dict[str, Any], name: str, times: int) -> None:
    assert world(ctx).events().count(name) == times


@then('the experiment is failed at the task "{task}"')
def _failed(ctx: dict[str, Any], task: str) -> None:
    w = world(ctx)
    assert w.experiment is not None and w.experiment.state is ExperimentState.FAILED
    assert w.experiment.failure is not None and w.experiment.failure["task_id"] == task
    assert "InjectedFailure" in w.experiment.failure["reason"]
    assert "ExperimentFailed" in w.events()


@then("the failure carries a traceback")
def _traceback(ctx: dict[str, Any]) -> None:
    experiment = world(ctx).experiment
    assert experiment is not None and experiment.failure is not None
    assert "traceback" in experiment.failure["diagnostics"]


@then("the manifest records the failed state and the failing task")
def _manifest_failed(ctx: dict[str, Any]) -> None:
    w = world(ctx)
    assert w.experiment is not None and w.experiment.failure is not None
    execution = w.manifest()["execution"]
    assert execution["state"] == "failed"
    assert execution["failure"]["task_id"] == w.experiment.failure["task_id"]
