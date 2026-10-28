"""Gates and their evaluation. The TypeScript GateEvaluator re-implements evaluate_gate exactly."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.findings import Findings

_GATE_ID = re.compile(r"^[A-Z]{2,4}-G-[0-9]{2}$")


class Comparator(StrEnum):
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    EQ = "eq"
    NE = "ne"


class Severity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"


class CheckOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    MISSING = "missing"
    ORACLE_UNVERIFIED = "oracle_unverified"


_COMPARE: Mapping[Comparator, Callable[[Any, Any], bool]] = {
    Comparator.LT: lambda a, b: a < b,
    Comparator.LE: lambda a, b: a <= b,
    Comparator.GT: lambda a, b: a > b,
    Comparator.GE: lambda a, b: a >= b,
    Comparator.EQ: lambda a, b: a == b,
    Comparator.NE: lambda a, b: a != b,
}


@dataclass(frozen=True)
class Gate:
    """``finding <comparator> threshold``, with a severity and the oracles it depends on.

    Gates belong to a Predeclaration and are immutable once it is frozen.
    """

    gate_id: str
    description: str
    finding: str
    comparator: Comparator
    threshold: float | bool
    severity: Severity = Severity.BLOCKING
    oracle_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _GATE_ID.match(self.gate_id):
            raise InvariantViolation(f"gate id must look like REL-G-01, got {self.gate_id!r}")
        if isinstance(self.threshold, bool) and self.comparator not in (Comparator.EQ, Comparator.NE):
            raise InvariantViolation(f"{self.gate_id}: a boolean threshold only supports eq/ne")

    def to_document(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "description": self.description,
            "finding": self.finding,
            "comparator": self.comparator.value,
            "threshold": self.threshold,
            "severity": self.severity.value,
            "oracle_ids": list(self.oracle_ids),
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Gate:
        return cls(
            gate_id=doc["gate_id"],
            description=doc["description"],
            finding=doc["finding"],
            comparator=Comparator(doc["comparator"]),
            threshold=doc["threshold"],
            severity=Severity(doc["severity"]),
            oracle_ids=tuple(doc.get("oracle_ids", ())),
        )


@dataclass(frozen=True)
class Check:
    """A gate evaluated against a finding."""

    gate_id: str
    finding: str
    value: float | bool | None
    unit: str | None
    comparator: Comparator
    threshold: float | bool
    severity: Severity
    oracle_ids: tuple[str, ...]
    oracles_verified: bool
    outcome: CheckOutcome

    @property
    def passed(self) -> bool:
        return self.outcome is CheckOutcome.PASS

    def to_document(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "finding": self.finding,
            "value": self.value,
            "unit": self.unit,
            "comparator": self.comparator.value,
            "threshold": self.threshold,
            "severity": self.severity.value,
            "oracle_ids": list(self.oracle_ids),
            "oracles_verified": self.oracles_verified,
            "outcome": self.outcome.value,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Check:
        return cls(
            gate_id=doc["gate_id"],
            finding=doc["finding"],
            value=doc["value"],
            unit=doc.get("unit"),
            comparator=Comparator(doc["comparator"]),
            threshold=doc["threshold"],
            severity=Severity(doc["severity"]),
            oracle_ids=tuple(doc["oracle_ids"]),
            oracles_verified=bool(doc["oracles_verified"]),
            outcome=CheckOutcome(doc["outcome"]),
        )


def _scalar(value: Any) -> float | bool | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(value)
    return None


def evaluate_gate(gate: Gate, findings: Findings) -> Check:
    """Evaluate one gate. Order matters and is mirrored by the harness:

    1. a finding that is absent or not a scalar is MISSING;
    2. any oracle the gate uses without a verification record makes it ORACLE_UNVERIFIED;
    3. otherwise the comparison decides PASS or FAIL. A boolean threshold only matches booleans.
    """
    finding = findings.get(gate.finding)
    value = _scalar(finding.value) if finding is not None else None
    oracles_verified = all(findings.oracle_verified(o) for o in gate.oracle_ids)
    if finding is None or value is None:
        outcome = CheckOutcome.MISSING
    elif not oracles_verified:
        outcome = CheckOutcome.ORACLE_UNVERIFIED
    elif isinstance(gate.threshold, bool) != isinstance(value, bool):
        outcome = CheckOutcome.FAIL
    else:
        outcome = CheckOutcome.PASS if _COMPARE[gate.comparator](value, gate.threshold) else CheckOutcome.FAIL
    return Check(
        gate_id=gate.gate_id,
        finding=gate.finding,
        value=value,
        unit=finding.unit if finding is not None else None,
        comparator=gate.comparator,
        threshold=gate.threshold,
        severity=gate.severity,
        oracle_ids=gate.oracle_ids,
        oracles_verified=oracles_verified,
        outcome=outcome,
    )


def evaluate_gates(gates: Sequence[Gate], findings: Findings) -> tuple[Check, ...]:
    return tuple(evaluate_gate(g, findings) for g in gates)
