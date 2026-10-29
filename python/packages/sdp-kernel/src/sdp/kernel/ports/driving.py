"""Driving ports: the nine use cases the kernel offers (§6). Commands are plain values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sdp.kernel.domain.archive import CatalogueQuery
from sdp.kernel.domain.dataset import DatasetArtifact
from sdp.kernel.domain.dossier import Dossier, Figure
from sdp.kernel.domain.experiment import Experiment
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.predeclaration import FrozenPredeclaration, Predeclaration
from sdp.kernel.domain.prediction import Prediction
from sdp.kernel.domain.verdict import ValidationReport
from sdp.kernel.ports.ledger import LedgerEntry
from sdp.kernel.ports.publication import DoiRecord
from sdp.kernel.science.plan import Backend


@dataclass(frozen=True)
class RegisterHypothesisCommand:
    hypothesis_id: str
    statement: str
    predictions: tuple[Prediction, ...] = ()
    evaluation_criteria: tuple[str, ...] = ()


@dataclass(frozen=True)
class FreezePredeclarationCommand:
    predeclaration: Predeclaration
    signer: str


@dataclass(frozen=True)
class AcquireDatasetCommand:
    query: CatalogueQuery
    curate: bool = True


@dataclass(frozen=True)
class AcquisitionResult:
    raw: DatasetArtifact
    curated: DatasetArtifact | None
    reused: bool
    #: rows the raw artifact holds, and the query's row cap
    rows: int = 0
    row_cap: int = 0

    @property
    def reached_row_cap(self) -> bool:
        """The archive filled the cap, so the region may hold more rows than were read (#59)."""
        return self.row_cap > 0 and self.rows >= self.row_cap


@dataclass(frozen=True)
class DefineExperimentCommand:
    protocol_name: str
    predeclaration_hash: str
    inputs: Mapping[str, ArtifactId]
    parameters: Mapping[str, Any] = field(default_factory=dict)
    seed: int = 0
    backend: Backend = Backend.CPU
    protocol_version: str | None = None


@dataclass(frozen=True)
class AssembleDossierCommand:
    experiment_id: str
    limitations: tuple[str, ...]
    figures: tuple[Figure, ...] = ()
    sections: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ApproveVerdictCommand:
    experiment_id: str
    reviewer: str


class RegisterHypothesis(Protocol):
    def execute(self, command: RegisterHypothesisCommand) -> Hypothesis: ...


class FreezePredeclaration(Protocol):
    def execute(self, command: FreezePredeclarationCommand) -> tuple[FrozenPredeclaration, LedgerEntry]: ...


class AcquireDataset(Protocol):
    def execute(self, command: AcquireDatasetCommand) -> AcquisitionResult: ...


class DefineExperiment(Protocol):
    def execute(self, command: DefineExperimentCommand) -> Experiment: ...


class RunExperiment(Protocol):
    def execute(self, experiment_id: str) -> Experiment: ...


class ValidateExperiment(Protocol):
    def execute(self, experiment_id: str) -> ValidationReport: ...


class AssembleDossier(Protocol):
    def execute(self, command: AssembleDossierCommand) -> Dossier: ...


class PublishBundle(Protocol):
    def approve(self, command: ApproveVerdictCommand) -> LedgerEntry: ...

    def publish(self, dossier_id: str) -> DoiRecord: ...


class QueryEvidence(Protocol):
    def experiment(self, experiment_id: str) -> Experiment | None: ...

    def experiments(self) -> Sequence[Experiment]: ...

    def manifest(self, experiment_id: str) -> dict[str, Any] | None: ...

    def lineage(self, entity_id: str) -> list[str]: ...

    def ledger_entries(self) -> Sequence[LedgerEntry]: ...

    def hypotheses(self) -> Sequence[Hypothesis]: ...

    def dossiers(self, experiment_id: str) -> Sequence[Dossier]: ...

    def datasets(self) -> Sequence[DatasetArtifact]: ...
