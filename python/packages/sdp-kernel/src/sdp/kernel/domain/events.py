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
    "InvestigationSetStarted",
    "InvestigationStageCompleted",
    "InvestigationStageFailed",
    "InvestigationOpened",
    "InvestigationParked",
    "InvestigationReopened",
    "InvestigationPromoted",
    "InvestigationPromotionRefused",
    "InvestigationClosed",
    "InvestigationAnnotated",
    "PlanDrafted",
    "PlanApproved",
    "PlanRefused",
    "PlanExecuted",
    "AssociationReviewed",
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
# Investigate (UC-03): the four triage transitions of the Investigation aggregate, the set's
# stage progress, and the refusal INV-009 requires to be recorded.
InvestigationSetStarted = _event("InvestigationSetStarted")
InvestigationStageCompleted = _event("InvestigationStageCompleted")
InvestigationStageFailed = _event("InvestigationStageFailed")
InvestigationOpened = _event("InvestigationOpened")
InvestigationParked = _event("InvestigationParked")
InvestigationReopened = _event("InvestigationReopened")
InvestigationPromoted = _event("InvestigationPromoted")
InvestigationPromotionRefused = _event("InvestigationPromotionRefused")
InvestigationClosed = _event("InvestigationClosed")
InvestigationAnnotated = _event("InvestigationAnnotated")
# Integration v0.4.0 §5, §4: the plan's lifecycle, and each reviewer decision on an association.
PlanDrafted = _event("PlanDrafted")
PlanApproved = _event("PlanApproved")
PlanRefused = _event("PlanRefused")
PlanExecuted = _event("PlanExecuted")
AssociationReviewed = _event("AssociationReviewed")

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
        InvestigationSetStarted,
        InvestigationStageCompleted,
        InvestigationStageFailed,
        InvestigationOpened,
        InvestigationParked,
        InvestigationReopened,
        InvestigationPromoted,
        InvestigationPromotionRefused,
        InvestigationClosed,
        InvestigationAnnotated,
        PlanDrafted,
        PlanApproved,
        PlanRefused,
        PlanExecuted,
        AssociationReviewed,
    )
}


def event_from_document(doc: dict[str, Any]) -> DomainEvent:
    cls = EVENT_TYPES[doc["name"]]
    return cls(
        aggregate_id=doc["aggregate_id"],
        occurred_at=datetime.fromisoformat(doc["occurred_at"]),
        payload=dict(doc.get("payload", {})),
    )
