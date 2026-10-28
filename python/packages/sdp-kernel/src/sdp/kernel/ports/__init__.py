"""Ports: Protocol classes for everything the application needs (driven) or offers (driving)."""

from sdp.kernel.ports.archives import (
    ArchiveGateway,
    CrossMatchResult,
    CrossMatchService,
    DialectCurator,
    HistoricalSourceGateway,
)
from sdp.kernel.ports.compute import (
    CheckpointSink,
    ClusterLabels,
    ClusterSearchAlgorithm,
    ComputeExecutor,
    EnvironmentProbe,
    TaskFailed,
)
from sdp.kernel.ports.determinism import Clock, Hasher, IdGenerator
from sdp.kernel.ports.events import EventLog, StoredEvent
from sdp.kernel.ports.evidence import EvidenceRepository, ExperimentTracker
from sdp.kernel.ports.ledger import Ledger, LedgerEntry, LedgerKind
from sdp.kernel.ports.plugins import ProtocolRegistry
from sdp.kernel.ports.publication import BundlePublisher, DoiRecord
from sdp.kernel.ports.repositories import (
    DossierRepository,
    ExperimentRepository,
    HypothesisRepository,
    PredeclarationRepository,
    UnitOfWork,
)
from sdp.kernel.ports.storage import ArtifactStore, DatasetRepository

__all__ = [
    "ArchiveGateway",
    "ArtifactStore",
    "BundlePublisher",
    "CheckpointSink",
    "Clock",
    "ClusterLabels",
    "ClusterSearchAlgorithm",
    "ComputeExecutor",
    "CrossMatchResult",
    "CrossMatchService",
    "DatasetRepository",
    "DialectCurator",
    "DoiRecord",
    "DossierRepository",
    "EnvironmentProbe",
    "EventLog",
    "EvidenceRepository",
    "ExperimentRepository",
    "ExperimentTracker",
    "Hasher",
    "HistoricalSourceGateway",
    "HypothesisRepository",
    "IdGenerator",
    "Ledger",
    "LedgerEntry",
    "LedgerKind",
    "PredeclarationRepository",
    "ProtocolRegistry",
    "StoredEvent",
    "TaskFailed",
    "UnitOfWork",
]
