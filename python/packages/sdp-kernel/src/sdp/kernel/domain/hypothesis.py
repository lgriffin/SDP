"""Hypothesis aggregate: editing creates a new version; old versions never disappear (KRN-001)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.prediction import Prediction


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    version: int
    statement: str
    predictions: tuple[Prediction, ...] = ()
    evaluation_criteria: tuple[str, ...] = ()
    supersedes: int | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvariantViolation("hypothesis versions start at 1")
        if not self.statement.strip():
            raise InvariantViolation("a hypothesis needs a statement")
        if self.supersedes is not None and self.supersedes != self.version - 1:
            raise InvariantViolation("a hypothesis version supersedes exactly its predecessor")

    def revise(
        self,
        *,
        statement: str | None = None,
        predictions: tuple[Prediction, ...] | None = None,
        evaluation_criteria: tuple[str, ...] | None = None,
    ) -> Hypothesis:
        """An edit is a new version; this one is left untouched."""
        return Hypothesis(
            hypothesis_id=self.hypothesis_id,
            version=self.version + 1,
            statement=self.statement if statement is None else statement,
            predictions=self.predictions if predictions is None else predictions,
            evaluation_criteria=self.evaluation_criteria if evaluation_criteria is None else evaluation_criteria,
            supersedes=self.version,
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "version": self.version,
            "statement": self.statement,
            "predictions": [p.to_document() for p in self.predictions],
            "evaluation_criteria": list(self.evaluation_criteria),
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> Hypothesis:
        version = int(doc["version"])
        return cls(
            hypothesis_id=doc["hypothesis_id"],
            version=version,
            statement=doc["statement"],
            predictions=tuple(Prediction.from_document(p) for p in doc.get("predictions", [])),
            evaluation_criteria=tuple(doc.get("evaluation_criteria", [])),
            supersedes=version - 1 if version > 1 else None,
        )


@dataclass
class HypothesisHistory:
    """All versions of one hypothesis, in order."""

    hypothesis_id: str
    versions: list[Hypothesis] = field(default_factory=list)

    def append(self, hypothesis: Hypothesis) -> None:
        if hypothesis.hypothesis_id != self.hypothesis_id:
            raise InvariantViolation("history belongs to a single hypothesis")
        expected = len(self.versions) + 1
        if hypothesis.version != expected:
            raise InvariantViolation(f"expected version {expected}, got {hypothesis.version}")
        self.versions.append(hypothesis)

    @property
    def latest(self) -> Hypothesis:
        return self.versions[-1]
