from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import pytest

from sdp.kernel.domain.errors import PredeclarationNotFrozen
from sdp.kernel.domain.experiment import ExperimentState
from sdp.kernel.science.plan import TaskContext, TaskKindRegistry
from sdp.testing.synthetic import SyntheticLinearProtocol, fit_line, residuals

from .conftest import Harness, harness_for

pytestmark = pytest.mark.tier("T4")


@pytest.mark.req("KRN-003")
def test_refuses_unfrozen_predeclaration(harness: Harness) -> None:
    unfrozen = harness.protocol.predeclare().compute_hash()
    experiment = harness.define(predeclaration_hash=unfrozen)

    with pytest.raises(PredeclarationNotFrozen):
        harness.k.kernel.run_experiment.execute(experiment.experiment_id)

    stored = harness.k.kernel.query_evidence.experiment(experiment.experiment_id)
    assert stored is not None and stored.state is ExperimentState.REFUSED
    assert stored.refusal and unfrozen in stored.refusal
    assert "ExperimentRefused" in harness.event_names()
    assert not harness.k.executor.executed
    manifest = harness.k.kernel.query_evidence.manifest(experiment.experiment_id)
    assert manifest is not None and manifest["execution"]["state"] == "refused"


@pytest.mark.req("KRN-005")
def test_every_completed_task_writes_a_checkpoint(harness: Harness) -> None:
    experiment = harness.run()

    assert experiment.state is ExperimentState.COMPLETED
    checkpoints = experiment.completed_checkpoints()
    assert set(checkpoints) == {"fit", "residuals"}
    assert all(harness.k.store.exists(a) for a in checkpoints.values())
    assert harness.event_names().count("CheckpointWritten") == 2


class Interrupted(BaseException):
    """Stands in for SIGTERM or a lost node: not an Exception, so no task failure is recorded."""


class InterruptOnce(SyntheticLinearProtocol):
    def __init__(self) -> None:
        super().__init__()
        self.calls: dict[str, int] = {"fit": 0, "residuals": 0}
        self.interrupt = True

    def task_kinds(self) -> TaskKindRegistry:
        def counted_fit(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> Any:
            self.calls["fit"] += 1
            return fit_line(params, upstream, ctx)

        def interrupting_residuals(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> Any:
            self.calls["residuals"] += 1
            if self.interrupt:
                self.interrupt = False
                raise Interrupted
            return residuals(params, upstream, ctx)

        registry = TaskKindRegistry()
        registry.register("synthetic.fit_line", counted_fit)
        registry.register("synthetic.residuals", interrupting_residuals)
        return registry


@pytest.mark.tier("T5")
@pytest.mark.req("KRN-006")
def test_resume_continues_from_last_checkpoint() -> None:
    protocol = InterruptOnce()
    harness = harness_for(protocol)
    experiment = harness.define()

    with pytest.raises(Interrupted):
        harness.k.kernel.run_experiment.execute(experiment.experiment_id)
    interrupted = harness.k.kernel.query_evidence.experiment(experiment.experiment_id)
    assert interrupted is not None and interrupted.state is ExperimentState.RUNNING
    assert set(interrupted.completed_checkpoints()) == {"fit"}

    resumed = harness.k.kernel.run_experiment.execute(experiment.experiment_id)

    assert resumed.state is ExperimentState.COMPLETED
    assert protocol.calls == {"fit": 1, "residuals": 2}
    assert resumed.completed_checkpoints()["fit"] == interrupted.completed_checkpoints()["fit"]


@pytest.mark.req("KRN-007")
def test_task_failure_preserves_manifest_diagnostics_and_partial_outputs(harness: Harness) -> None:
    experiment = harness.run(inject_failure="residuals")

    assert experiment.state is ExperimentState.FAILED
    assert experiment.failure is not None and experiment.failure["task_id"] == "residuals"
    assert "InjectedFailure" in experiment.failure["reason"]
    assert "traceback" in experiment.failure["diagnostics"]
    assert set(experiment.completed_checkpoints()) == {"fit"}
    assert harness.k.store.exists(experiment.completed_checkpoints()["fit"])
    manifest = harness.k.kernel.query_evidence.manifest(experiment.experiment_id)
    assert manifest is not None
    assert manifest["execution"]["state"] == "failed"
    assert manifest["execution"]["failure"]["task_id"] == "residuals"
    assert "ExperimentFailed" in harness.event_names()


@pytest.mark.req("NFR-005")
def test_logs_carry_experiment_task_duration_resources_and_failure(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="sdp.kernel.runner")
    experiment = harness.run(inject_failure="residuals")

    completed = [r for r in caplog.records if r.getMessage() == "task completed"]
    assert completed and completed[0].experiment_id == experiment.experiment_id  # type: ignore[attr-defined]
    assert completed[0].task_id == "fit" and completed[0].duration_s >= 0  # type: ignore[attr-defined]
    assert hasattr(completed[0], "resource_maxrss_kb")
    failed = [r for r in caplog.records if r.getMessage() == "experiment failed"]
    assert failed and "InjectedFailure" in failed[0].failure_reason  # type: ignore[attr-defined]


@pytest.mark.req("KRN-010")
def test_events_commit_with_the_state_they_describe(harness: Harness) -> None:
    experiment = harness.run()
    stored = harness.k.kernel.query_evidence.experiment(experiment.experiment_id)
    assert stored is not None and stored.state is ExperimentState.COMPLETED
    names = harness.event_names()
    assert names.index("ExperimentDefined") < names.index("ExperimentStarted") < names.index("ExperimentCompleted")
