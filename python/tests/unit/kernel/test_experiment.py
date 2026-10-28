from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdp.kernel.domain.errors import InvalidTransition, InvariantViolation, PredeclarationNotFrozen
from sdp.kernel.domain.experiment import EnvironmentRecord, ExperimentState, TaskRecord
from sdp.kernel.domain.findings import Finding, Findings
from sdp.kernel.domain.gates import evaluate_gates
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.verdict import ValidationReport
from sdp.testing.contracts.metadata import sample_experiment
from sdp.testing.synthetic import SyntheticLinearProtocol

pytestmark = pytest.mark.tier("T1")
AT = datetime(2026, 10, 28, tzinfo=UTC)
CKPT = ArtifactId("sha256:" + "d" * 64)


@pytest.mark.req("KRN-003")
def test_experiment_refuses_unfrozen_predeclaration() -> None:
    exp = sample_experiment()
    with pytest.raises(PredeclarationNotFrozen):
        exp.start(ledger_entry_id=None, at=AT)
    assert exp.state is ExperimentState.REFUSED
    assert exp.refusal and "not in the ledger" in exp.refusal
    assert [e.name for e in exp.pull_events()] == ["ExperimentDefined", "ExperimentRefused"]


def test_refused_is_terminal() -> None:
    exp = sample_experiment()
    with pytest.raises(PredeclarationNotFrozen):
        exp.start(ledger_entry_id=None, at=AT)
    with pytest.raises(InvalidTransition):
        exp.start(ledger_entry_id="led-1", at=AT)


def test_happy_lifecycle_reaches_published() -> None:
    exp = sample_experiment()
    exp.start(ledger_entry_id="led-1", at=AT)
    exp.plan_tasks([TaskRecord("t", "k", (), {})])
    exp.checkpoint("t", CKPT, 0.1, AT)
    findings = Findings.of(Finding("slope_abs_error", 0.001), Finding("residual_rms", 0.1))
    exp.complete(findings, {}, AT)
    predeclaration = SyntheticLinearProtocol().predeclare()
    report = ValidationReport.from_checks(
        experiment_id=exp.experiment_id,
        protocol_id="synthetic-linear",
        protocol_version="1.0.0",
        predeclaration_hash=exp.definition.predeclaration_hash,
        checks=evaluate_gates(predeclaration.gates, findings),
    )
    exp.validate(report, AT)
    with pytest.raises(InvalidTransition):
        exp.publish(doi="10.5072/x", bundle=CKPT, at=AT)
    exp.approve(ledger_entry_id="led-2", reviewer="rev", at=AT)
    exp.publish(doi="10.5072/x", bundle=CKPT, at=AT)
    assert exp.state is ExperimentState.PUBLISHED


@pytest.mark.req("KRN-007")
def test_failure_preserves_diagnostics() -> None:
    exp = sample_experiment()
    exp.start(ledger_entry_id="led-1", at=AT)
    exp.plan_tasks([TaskRecord("t", "k", (), {}), TaskRecord("u", "k", ("t",), {})])
    exp.checkpoint("t", CKPT, 0.1, AT)
    exp.fail(task_id="u", reason="ZeroDivisionError", diagnostics={"traceback": "..."}, at=AT)
    assert exp.state is ExperimentState.FAILED
    assert exp.tasks["t"].checkpoint == CKPT
    assert exp.tasks["u"].status == "failed"
    assert exp.failure == {"task_id": "u", "reason": "ZeroDivisionError", "diagnostics": {"traceback": "..."}}


def test_report_for_another_predeclaration_is_refused() -> None:
    exp = sample_experiment()
    exp.start(ledger_entry_id="led-1", at=AT)
    exp.complete(Findings(), {}, AT)
    report = ValidationReport.from_checks(
        experiment_id=exp.experiment_id,
        protocol_id="p",
        protocol_version="1.0.0",
        predeclaration_hash="sha256:" + "9" * 64,
        checks=(),
    )
    with pytest.raises(InvariantViolation):
        exp.validate(report, AT)


@pytest.mark.req("KRN-013")
def test_cuda_environment_must_record_driver_cuda_and_agreement() -> None:
    with pytest.raises(InvariantViolation):
        EnvironmentRecord("rev", "lock", "cuda", "3.12", "linux-aarch64")
    env = EnvironmentRecord(
        "rev",
        "lock",
        "cuda",
        "3.12",
        "linux-aarch64",
        driver_version="580",
        cuda_version="13.0",
        agreement_test_id="agree-1",
    )
    assert env.to_document()["agreement_test_id"] == "agree-1"
