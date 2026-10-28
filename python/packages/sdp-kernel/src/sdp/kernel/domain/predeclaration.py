"""Predeclaration aggregate: frozen intent (§5, ADR-004)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sdp.kernel.domain.canonical import hash_document
from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.gates import Gate
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.ids import check_semver

SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class OracleTolerance:
    oracle_id: str
    rtol: float
    atol: float
    unit: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {"oracle_id": self.oracle_id, "rtol": self.rtol, "atol": self.atol, "unit": self.unit}


@dataclass(frozen=True)
class Target:
    """A target region or source, with the reason it was chosen (STL-001)."""

    target_id: str
    kind: str
    reason: str
    spec: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in ("region", "source"):
            raise InvariantViolation(f"target kind must be region or source, got {self.kind!r}")
        if not self.reason.strip():
            raise InvariantViolation(f"target {self.target_id!r} needs a recorded reason")

    def to_document(self) -> dict[str, Any]:
        return {"target_id": self.target_id, "kind": self.kind, "reason": self.reason, "spec": dict(self.spec)}


@dataclass(frozen=True)
class Predeclaration:
    """Hypotheses, gates, oracle tolerances, targets and parameters for one protocol version."""

    predeclaration_id: str
    protocol_id: str
    protocol_version: str
    title: str
    hypotheses: tuple[Hypothesis, ...]
    gates: tuple[Gate, ...]
    oracle_tolerances: tuple[OracleTolerance, ...] = ()
    targets: tuple[Target, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)
    notes: str | None = None

    def __post_init__(self) -> None:
        check_semver(self.protocol_version)
        if not self.hypotheses:
            raise InvariantViolation("a predeclaration states at least one hypothesis")
        if not self.gates:
            raise InvariantViolation("a predeclaration declares at least one gate")
        ids = [g.gate_id for g in self.gates]
        if len(ids) != len(set(ids)):
            raise InvariantViolation("gate ids must be unique")
        known = {t.oracle_id for t in self.oracle_tolerances}
        missing = sorted({o for g in self.gates for o in g.oracle_ids} - known)
        if missing:
            raise InvariantViolation(f"gates use oracles without a predeclared tolerance: {missing}")

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "predeclaration_id": self.predeclaration_id,
            "protocol_id": self.protocol_id,
            "protocol_version": self.protocol_version,
            "title": self.title,
            "hypotheses": [h.to_document() for h in self.hypotheses],
            "gates": [g.to_document() for g in self.gates],
            "oracle_tolerances": [t.to_document() for t in self.oracle_tolerances],
            "targets": [t.to_document() for t in self.targets],
            "parameters": dict(self.parameters),
            "notes": self.notes,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Predeclaration:
        return cls(
            predeclaration_id=doc["predeclaration_id"],
            protocol_id=doc["protocol_id"],
            protocol_version=doc["protocol_version"],
            title=doc["title"],
            hypotheses=tuple(Hypothesis.from_document(h) for h in doc["hypotheses"]),
            gates=tuple(Gate.from_document(g) for g in doc["gates"]),
            oracle_tolerances=tuple(
                OracleTolerance(t["oracle_id"], float(t["rtol"]), float(t["atol"]), t.get("unit"))
                for t in doc.get("oracle_tolerances", [])
            ),
            targets=tuple(
                Target(t["target_id"], t["kind"], t["reason"], dict(t.get("spec", {}))) for t in doc.get("targets", [])
            ),
            parameters=dict(doc.get("parameters", {})),
            notes=doc.get("notes"),
        )

    def compute_hash(self) -> str:
        """The hash over canonical JSON that the ledger records."""
        return hash_document(self.to_document())

    def freeze(self) -> FrozenPredeclaration:
        return FrozenPredeclaration(self, self.compute_hash())

    def gate(self, gate_id: str) -> Gate:
        for g in self.gates:
            if g.gate_id == gate_id:
                return g
        raise KeyError(gate_id)


@dataclass(frozen=True)
class FrozenPredeclaration:
    """A predeclaration with its hash. Equality of hash is equality of intent."""

    predeclaration: Predeclaration
    hash: str

    def __post_init__(self) -> None:
        if self.predeclaration.compute_hash() != self.hash:
            raise InvariantViolation("frozen hash does not match the predeclaration's canonical JSON")
