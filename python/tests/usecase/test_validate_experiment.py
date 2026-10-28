from __future__ import annotations

import pytest

from sdp.kernel.domain.errors import InvariantViolation, VerdictInconsistent
from sdp.kernel.domain.experiment import ExperimentState
from sdp.kernel.domain.findings import Findings
from sdp.kernel.domain.predeclaration import Predeclaration
from sdp.kernel.domain.verdict import ValidationReport, VerdictStatus
from sdp.testing.synthetic import SyntheticLinearProtocol

from .conftest import Harness, harness_for

pytestmark = pytest.mark.tier("T4")


@pytest.mark.req("KRN-008")
def test_validation_persists_report_with_every_gate_outcome(harness: Harness) -> None:
    experiment = harness.run()
    report = harness.k.kernel.validate_experiment.execute(experiment.experiment_id)

    assert report.verdict.status is VerdictStatus.VERIFIED
    gate_ids = {g.gate_id for g in harness.protocol.predeclare().gates}
    assert {c.gate_id for c in report.checks} == gate_ids
    stored = harness.k.kernel.query_evidence.experiment(experiment.experiment_id)
    assert stored is not None and stored.state is ExperimentState.VALIDATED and stored.report == report
    assert "VerdictIssued" in harness.event_names()
    manifest = harness.k.kernel.query_evidence.manifest(experiment.experiment_id)
    assert manifest is not None and manifest["verdict"]["status"] == "VERIFIED"


@pytest.mark.req("KRN-009")
def test_a_wrong_planted_slope_is_rejected() -> None:
    harness = harness_for(SyntheticLinearProtocol(planted_slope=3.0))
    experiment = harness.run()
    report = harness.k.kernel.validate_experiment.execute(experiment.experiment_id)
    assert report.verdict.status is VerdictStatus.REJECTED


class OptimisticProtocol(SyntheticLinearProtocol):
    """A science module that claims every gate passed; the kernel must not believe it."""

    def validate(self, findings: Findings, predeclaration: Predeclaration, *, experiment_id: str) -> ValidationReport:
        honest = super().validate(findings, predeclaration, experiment_id=experiment_id)
        from dataclasses import replace

        from sdp.kernel.domain.gates import CheckOutcome

        checks = tuple(replace(c, outcome=CheckOutcome.PASS, value=0.0) for c in honest.checks)
        return ValidationReport.from_checks(
            experiment_id=experiment_id,
            protocol_id=honest.protocol_id,
            protocol_version=honest.protocol_version,
            predeclaration_hash=honest.predeclaration_hash,
            checks=checks,
        )


@pytest.mark.req("KRN-008", "KRN-009")
def test_kernel_refuses_a_report_that_disagrees_with_its_gate_evaluation() -> None:
    harness = harness_for(OptimisticProtocol(planted_slope=3.0))
    experiment = harness.run()
    with pytest.raises(VerdictInconsistent):
        harness.k.kernel.validate_experiment.execute(experiment.experiment_id)
    stored = harness.k.kernel.query_evidence.experiment(experiment.experiment_id)
    assert stored is not None and stored.state is ExperimentState.COMPLETED


@pytest.mark.req("KRN-008")
def test_only_completed_experiments_validate(harness: Harness) -> None:
    experiment = harness.run(inject_failure="fit")
    with pytest.raises(InvariantViolation):
        harness.k.kernel.validate_experiment.execute(experiment.experiment_id)
