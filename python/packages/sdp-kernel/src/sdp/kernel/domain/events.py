"""Domain events (§5). Each is appended to the EventLog in the same transaction as its state change."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

EVENT_NAMES = (
    "HypothesisRegistered",
    "PredeclarationFrozen",
    "DatasetAcquired",
    "DatasetRejected",
    "ExperimentDefined",
    "ExperimentStarted",
    "ExperimentRefused",
    "CheckpointWritten",
    "ExperimentCompleted",
    "ExperimentFailed",
    "VerdictIssued",
    "DossierAssembled",
    "ApprovalRecorded",
    "BundlePublished",
)


@dataclass(frozen=True)
class DomainEvent:
    """A fact that happened. ``name`` is the class name; ``payload`` is JSON-compatible."""

    aggregate_id: str
    occurred_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    name: ClassVar[str] = "DomainEvent"

    def to_document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "aggregate_id": self.aggregate_id,
            "occurred_at": self.occurred_at.isoformat(),
            "payload": self.payload,
        }


def _event(name: str) -> type[DomainEvent]:
    return type(name, (DomainEvent,), {"name": name, "__doc__": f"Domain event {name}."})


HypothesisRegistered = _event("HypothesisRegistered")
PredeclarationFrozen = _event("PredeclarationFrozen")
DatasetAcquired = _event("DatasetAcquired")
DatasetRejected = _event("DatasetRejected")
ExperimentDefined = _event("ExperimentDefined")
ExperimentStarted = _event("ExperimentStarted")
#: Not in the §5 list by name, but the lifecycle's Refused state needs a recorded refusal (KRN-003).
ExperimentRefused = _event("ExperimentRefused")
CheckpointWritten = _event("CheckpointWritten")
ExperimentCompleted = _event("ExperimentCompleted")
ExperimentFailed = _event("ExperimentFailed")
VerdictIssued = _event("VerdictIssued")
DossierAssembled = _event("DossierAssembled")
ApprovalRecorded = _event("ApprovalRecorded")
BundlePublished = _event("BundlePublished")

EVENT_TYPES: dict[str, type[DomainEvent]] = {
    cls.name: cls
    for cls in (
        HypothesisRegistered,
        PredeclarationFrozen,
        DatasetAcquired,
        DatasetRejected,
        ExperimentDefined,
        ExperimentStarted,
        ExperimentRefused,
        CheckpointWritten,
        ExperimentCompleted,
        ExperimentFailed,
        VerdictIssued,
        DossierAssembled,
        ApprovalRecorded,
        BundlePublished,
    )
}


def event_from_document(doc: dict[str, Any]) -> DomainEvent:
    cls = EVENT_TYPES[doc["name"]]
    return cls(
        aggregate_id=doc["aggregate_id"],
        occurred_at=datetime.fromisoformat(doc["occurred_at"]),
        payload=dict(doc.get("payload", {})),
    )
