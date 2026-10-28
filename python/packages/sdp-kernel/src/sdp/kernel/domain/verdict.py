"""Verdicts. A Verdict is never constructed without a ValidationReport (§5, KRN-009)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.gates import Check, CheckOutcome, Severity


class VerdictStatus(StrEnum):
    VERIFIED = "VERIFIED"
    CANDIDATE = "CANDIDATE"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"


def decide_verdict(checks: Sequence[Check]) -> tuple[VerdictStatus, tuple[str, ...]]:
    """The verdict rule, mirrored by the harness GateEvaluator:

    * any blocking gate FAILED -> REJECTED;
    * else any blocking gate MISSING or ORACLE_UNVERIFIED -> INCONCLUSIVE;
    * else any warning gate not PASS -> CANDIDATE;
    * else VERIFIED. No gates at all is INCONCLUSIVE.
    """
    if not checks:
        return VerdictStatus.INCONCLUSIVE, ("no gates were evaluated",)
    blocking = [c for c in checks if c.severity is Severity.BLOCKING]
    failed = [c for c in blocking if c.outcome is CheckOutcome.FAIL]
    if failed:
        return VerdictStatus.REJECTED, tuple(f"blocking gate {c.gate_id} failed" for c in failed)
    open_ = [c for c in blocking if c.outcome in (CheckOutcome.MISSING, CheckOutcome.ORACLE_UNVERIFIED)]
    if open_:
        return VerdictStatus.INCONCLUSIVE, tuple(
            f"blocking gate {c.gate_id} {'has no finding' if c.outcome is CheckOutcome.MISSING else 'uses an unverified oracle'}"
            for c in open_
        )
    warnings = [c for c in checks if c.severity is Severity.WARNING and not c.passed]
    if warnings:
        return VerdictStatus.CANDIDATE, tuple(f"warning gate {c.gate_id} did not pass" for c in warnings)
    return VerdictStatus.VERIFIED, ("all gates passed",)


@dataclass(frozen=True)
class Verdict:
    status: VerdictStatus
    protocol_version: str
    reasons: tuple[str, ...]

    def to_document(self) -> dict[str, Any]:
        return {"status": self.status.value, "protocol_version": self.protocol_version, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class ValidationReport:
    """Every gate's check value, threshold and outcome, and the verdict they imply."""

    experiment_id: str
    protocol_id: str
    protocol_version: str
    predeclaration_hash: str
    checks: tuple[Check, ...]
    verdict: Verdict

    def __post_init__(self) -> None:
        expected, _ = decide_verdict(self.checks)
        if self.verdict.status is not expected:
            raise InvariantViolation(
                f"verdict {self.verdict.status} does not follow from the checks (expected {expected})"
            )
        if self.verdict.protocol_version != self.protocol_version:
            raise InvariantViolation("verdict and report disagree on the protocol version")

    @classmethod
    def from_checks(
        cls,
        *,
        experiment_id: str,
        protocol_id: str,
        protocol_version: str,
        predeclaration_hash: str,
        checks: Sequence[Check],
    ) -> ValidationReport:
        status, reasons = decide_verdict(checks)
        return cls(
            experiment_id=experiment_id,
            protocol_id=protocol_id,
            protocol_version=protocol_version,
            predeclaration_hash=predeclaration_hash,
            checks=tuple(checks),
            verdict=Verdict(status, protocol_version, reasons),
        )

    def check(self, gate_id: str) -> Check:
        for c in self.checks:
            if c.gate_id == gate_id:
                return c
        raise KeyError(gate_id)

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "experiment_id": self.experiment_id,
            "protocol_id": self.protocol_id,
            "protocol_version": self.protocol_version,
            "predeclaration_hash": self.predeclaration_hash,
            "checks": [c.to_document() for c in self.checks],
            "verdict": self.verdict.to_document(),
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ValidationReport:
        verdict = doc["verdict"]
        return cls(
            experiment_id=doc["experiment_id"],
            protocol_id=doc["protocol_id"],
            protocol_version=doc["protocol_version"],
            predeclaration_hash=doc["predeclaration_hash"],
            checks=tuple(Check.from_document(c) for c in doc["checks"]),
            verdict=Verdict(VerdictStatus(verdict["status"]), verdict["protocol_version"], tuple(verdict["reasons"])),
        )
