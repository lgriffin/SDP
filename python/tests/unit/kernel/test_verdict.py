from __future__ import annotations

import pytest

from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.findings import Finding, Findings, OracleStatus
from sdp.kernel.domain.gates import CheckOutcome, Comparator, Gate, Severity, evaluate_gate, evaluate_gates
from sdp.kernel.domain.verdict import ValidationReport, Verdict, VerdictStatus, decide_verdict

pytestmark = pytest.mark.tier("T1")

BLOCK = Gate("TST-G-01", "error small", "err", Comparator.LT, 0.05, Severity.BLOCKING, ("oracle-a",))
WARN = Gate("TST-G-02", "rms small", "rms", Comparator.LE, 0.5, Severity.WARNING)
VERIFIED_ORACLE = {"oracle-a": OracleStatus("oracle-a", True)}


def report(findings: Findings) -> ValidationReport:
    return ValidationReport.from_checks(
        experiment_id="e",
        protocol_id="p",
        protocol_version="1.0.0",
        predeclaration_hash="sha256:" + "0" * 64,
        checks=evaluate_gates((BLOCK, WARN), findings),
    )


@pytest.mark.req("KRN-009")
def test_blocking_gate_failure_prevents_verified() -> None:
    r = report(Findings.of(Finding("err", 0.2), Finding("rms", 0.1), oracles=VERIFIED_ORACLE))
    assert r.verdict.status is VerdictStatus.REJECTED


@pytest.mark.req("KRN-009")
def test_unverified_oracle_prevents_verified() -> None:
    r = report(
        Findings.of(Finding("err", 0.01), Finding("rms", 0.1), oracles={"oracle-a": OracleStatus("oracle-a", False)})
    )
    assert r.verdict.status is VerdictStatus.INCONCLUSIVE
    assert r.check("TST-G-01").outcome is CheckOutcome.ORACLE_UNVERIFIED


def test_all_gates_pass_is_verified() -> None:
    r = report(Findings.of(Finding("err", 0.01), Finding("rms", 0.1), oracles=VERIFIED_ORACLE))
    assert r.verdict.status is VerdictStatus.VERIFIED


def test_warning_failure_is_candidate() -> None:
    r = report(Findings.of(Finding("err", 0.01), Finding("rms", 0.9), oracles=VERIFIED_ORACLE))
    assert r.verdict.status is VerdictStatus.CANDIDATE


def test_missing_finding_is_inconclusive() -> None:
    r = report(Findings.of(Finding("rms", 0.1), oracles=VERIFIED_ORACLE))
    assert r.check("TST-G-01").outcome is CheckOutcome.MISSING
    assert r.verdict.status is VerdictStatus.INCONCLUSIVE


def test_no_gates_is_inconclusive() -> None:
    assert decide_verdict(())[0] is VerdictStatus.INCONCLUSIVE


@pytest.mark.req("KRN-009")
def test_a_verdict_that_does_not_follow_from_its_checks_cannot_be_constructed() -> None:
    checks = evaluate_gates((BLOCK,), Findings.of(Finding("err", 0.9), oracles=VERIFIED_ORACLE))
    with pytest.raises(InvariantViolation):
        ValidationReport("e", "p", "1.0.0", "sha256:" + "0" * 64, checks, Verdict(VerdictStatus.VERIFIED, "1.0.0", ()))


def test_boolean_gates_only_match_booleans() -> None:
    gate = Gate("TST-G-03", "present", "present", Comparator.EQ, True)
    assert evaluate_gate(gate, Findings.of(Finding("present", True))).outcome is CheckOutcome.PASS
    assert evaluate_gate(gate, Findings.of(Finding("present", 1))).outcome is CheckOutcome.FAIL
    with pytest.raises(InvariantViolation):
        Gate("TST-G-04", "bad", "x", Comparator.LT, True)


def test_report_round_trips_through_its_document() -> None:
    r = report(Findings.of(Finding("err", 0.01), Finding("rms", 0.1), oracles=VERIFIED_ORACLE))
    assert ValidationReport.from_document(r.to_document()) == r
