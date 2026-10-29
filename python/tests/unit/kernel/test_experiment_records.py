"""T1: the Experiment aggregate's record: the exact event each lifecycle step emits, the times it
stamps, the refusals it gives and the environment document it keeps (KRN-003, KRN-005, KRN-007).

The behaviour is in test_experiment.py; these pin the details mutation testing showed nothing
else checked (docs/testing.md, "Killing a survivor")."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest

from sdp.kernel.domain.errors import InvalidTransition, InvariantViolation, PredeclarationNotFrozen
from sdp.kernel.domain.experiment import EnvironmentRecord, Experiment, TaskRecord
from sdp.kernel.domain.findings import Finding, Findings
from sdp.kernel.domain.gates import evaluate_gates
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.verdict import ValidationReport
from sdp.testing.contracts.metadata import sample_experiment
from sdp.testing.synthetic import SyntheticLinearProtocol

pytestmark = pytest.mark.tier("T1")

DEFINED_AT = datetime(2026, 10, 28, 12, tzinfo=UTC)
T1 = datetime(2026, 10, 28, 13, tzinfo=UTC)
T2 = datetime(2026, 10, 28, 14, tzinfo=UTC)
T3 = datetime(2026, 10, 28, 15, tzinfo=UTC)
CKPT = ArtifactId("sha256:" + "d" * 64)
BUNDLE = ArtifactId("sha256:" + "e" * 64)
PREDECLARATION = "sha256:" + "a" * 64


def event(name: str, at: datetime, payload: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "aggregate_id": "exp-contract", "occurred_at": at.isoformat(), "payload": payload}


def exactly(error: type[Exception], message: str) -> Any:
    return pytest.raises(error, match="^" + re.escape(message) + "$")


def _report(exp: Experiment, findings: Findings) -> ValidationReport:
    return ValidationReport.from_checks(
        experiment_id=exp.experiment_id,
        protocol_id="synthetic-linear",
        protocol_version="1.0.0",
        predeclaration_hash=exp.definition.predeclaration_hash,
        checks=evaluate_gates(SyntheticLinearProtocol().predeclare().gates, findings),
    )


@pytest.mark.req("KRN-005")
def test_each_lifecycle_step_emits_its_event_with_its_payload() -> None:
    exp = sample_experiment()
    assert [e.to_document() for e in exp.pull_events()] == [
        event(
            "ExperimentDefined",
            DEFINED_AT,
            {
                "protocol_id": "synthetic-linear",
                "protocol_version": "1.0.0",
                "predeclaration_hash": PREDECLARATION,
                "seed": 3,
            },
        )
    ]
    exp.start(ledger_entry_id="led-1", at=T1)
    exp.start(ledger_entry_id="led-1", at=T2)  # a resume keeps Running
    exp.plan_tasks([TaskRecord("t", "fit", (), {"gpu": False})])
    exp.checkpoint("t", CKPT, 0.25, T2)
    findings = Findings.of(Finding("slope_abs_error", 0.001), Finding("residual_rms", 0.1))
    exp.complete(findings, {"fit": CKPT}, T3)
    report = _report(exp, findings)
    exp.validate(report, T3)
    exp.approve(ledger_entry_id="led-2", reviewer="rev", at=T3)
    exp.publish(doi="10.5072/x", bundle=BUNDLE, at=T3)
    assert (exp.started_at, exp.finished_at, exp.ledger_entry_id) == (T1, T3, "led-1")
    assert exp.tasks["t"] == TaskRecord("t", "fit", (), {"gpu": False}, "completed", CKPT, 0.25)
    assert exp.completed_checkpoints() == {"t": CKPT}
    assert exp.approvals == [{"ledger_entry_id": "led-2", "reviewer": "rev", "recorded_at": T3.isoformat()}]
    assert exp.outputs == {"fit": CKPT, "bundle": BUNDLE} and exp.bundle_doi == "10.5072/x"
    assert [e.to_document() for e in exp.pull_events()] == [
        event("ExperimentStarted", T1, {"resumed": False, "ledger_entry_id": "led-1"}),
        event("ExperimentStarted", T2, {"resumed": True}),
        event("CheckpointWritten", T2, {"task_id": "t", "artifact_id": CKPT.value, "duration_s": 0.25}),
        event("ExperimentCompleted", T3, {"findings": sorted(findings.items)}),
        event(
            "VerdictIssued",
            T3,
            {"status": report.verdict.status.value, "gates": {c.gate_id: c.outcome.value for c in report.checks}},
        ),
        event("ApprovalRecorded", T3, {"ledger_entry_id": "led-2", "reviewer": "rev", "recorded_at": T3.isoformat()}),
        event("BundlePublished", T3, {"doi": "10.5072/x", "bundle": BUNDLE.value}),
    ]
    assert report.checks  # the gates payload above is not vacuous


@pytest.mark.req("KRN-003")
def test_a_refusal_is_stamped_and_recorded() -> None:
    exp = sample_experiment()
    exp.pull_events()
    reason = f"predeclaration {PREDECLARATION} is not in the ledger"
    with exactly(PredeclarationNotFrozen, reason):
        exp.start(ledger_entry_id=None, at=T1)
    assert exp.finished_at == T1 and exp.started_at is None
    assert [e.to_document() for e in exp.pull_events()] == [event("ExperimentRefused", T1, {"reason": reason})]
    with exactly(InvalidTransition, "exp-contract: refused -> running is not allowed"):
        exp.start(ledger_entry_id="led-1", at=T2)


@pytest.mark.req("KRN-007")
def test_a_failure_is_stamped_and_recorded() -> None:
    exp = sample_experiment()
    exp.start(ledger_entry_id="led-1", at=T1)
    exp.plan_tasks([TaskRecord("t", "fit", ("s",), {"gpu": True})])
    exp.pull_events()
    exp.fail(task_id="t", reason="ZeroDivisionError", diagnostics={}, at=T2)
    assert exp.tasks["t"] == TaskRecord("t", "fit", ("s",), {"gpu": True}, "failed")
    assert exp.finished_at == T2
    assert [e.to_document() for e in exp.pull_events()] == [
        event("ExperimentFailed", T2, {"task_id": "t", "reason": "ZeroDivisionError"})
    ]
    with exactly(InvalidTransition, "only a Running experiment can fail, not failed"):
        exp.fail(task_id=None, reason="again", diagnostics={}, at=T3)


def test_a_failure_outside_any_task_leaves_the_tasks_alone() -> None:
    exp = sample_experiment()
    exp.start(ledger_entry_id="led-1", at=T1)
    exp.plan_tasks([TaskRecord("t", "fit", (), {})])
    exp.fail(task_id="unknown", reason="OOM", diagnostics={}, at=T2)
    assert exp.tasks == {"t": TaskRecord("t", "fit", (), {})}
    assert exp.failure == {"task_id": "unknown", "reason": "OOM", "diagnostics": {}}


def test_out_of_order_steps_say_why() -> None:
    exp = sample_experiment()
    with exactly(InvalidTransition, "tasks are planned while Running"):
        exp.plan_tasks([TaskRecord("t", "fit", (), {})])
    with exactly(InvalidTransition, "checkpoints are written while Running"):
        exp.checkpoint("t", CKPT, 0.1, T1)
    with exactly(InvalidTransition, "nothing to approve before validation"):
        exp.approve(ledger_entry_id="led-2", reviewer="rev", at=T1)
    with exactly(InvalidTransition, "publication needs a signed approval"):
        exp.publish(doi="10.5072/x", bundle=BUNDLE, at=T1)
    exp.start(ledger_entry_id="led-1", at=T1)
    exp.plan_tasks([TaskRecord("t", "fit", (), {})])
    with exactly(InvariantViolation, "resumed plan changed the kind of task t"):
        exp.plan_tasks([TaskRecord("t", "plot", (), {})])
    exp.plan_tasks([TaskRecord("t", "fit", ("other",), {})])  # a resume keeps the recorded task
    assert exp.tasks["t"].depends_on == ()


def test_a_report_must_be_this_experiments_under_its_predeclaration() -> None:
    exp = sample_experiment()
    exp.start(ledger_entry_id="led-1", at=T1)
    exp.complete(Findings(), {}, T2)
    base: dict[str, Any] = {"protocol_id": "p", "protocol_version": "1.0.0", "checks": ()}
    other = ValidationReport.from_checks(experiment_id="exp-other", predeclaration_hash=PREDECLARATION, **base)
    with exactly(InvariantViolation, "report belongs to another experiment"):
        exp.validate(other, T3)
    drifted = ValidationReport.from_checks(
        experiment_id="exp-contract", predeclaration_hash="sha256:" + "9" * 64, **base
    )
    with exactly(InvariantViolation, "report was evaluated against a different predeclaration"):
        exp.validate(drifted, T3)


def test_inputs_are_found_by_role() -> None:
    definition = sample_experiment().definition
    assert definition.input("series").media_type == "application/json"
    with pytest.raises(KeyError, match="'weights'"):
        definition.input("weights")


@pytest.mark.req("KRN-013")
def test_environment_document_round_trips_every_field_and_defaults_the_rest() -> None:
    env = EnvironmentRecord(
        "rev",
        "lock",
        "cuda",
        "3.12",
        "linux-aarch64",
        image_digest="sha256:" + "f" * 64,
        driver_version="580",
        cuda_version="13.0",
        agreement_test_id="agree-1",
        constants_version="codata-2022",
        packages={"numpy": "2.3.0"},
    )
    assert EnvironmentRecord.from_document(env.to_document()) == env
    bare = {
        "code_revision": "rev",
        "lock_digest": "lock",
        "backend": "cpu",
        "python_version": "3.12",
        "platform": "linux",
    }
    assert EnvironmentRecord.from_document(bare) == EnvironmentRecord("rev", "lock", "cpu", "3.12", "linux")
