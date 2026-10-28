"""QueryEvidence: read-only lineage, manifests, verdicts and ledger entries."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sdp.kernel.domain.dataset import DatasetArtifact
from sdp.kernel.domain.dossier import Dossier
from sdp.kernel.domain.experiment import Experiment
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.ports.events import StoredEvent
from sdp.kernel.ports.ledger import Ledger, LedgerEntry
from sdp.kernel.ports.repositories import UnitOfWork


@dataclass
class QueryEvidenceUseCase:
    uow: Callable[[], UnitOfWork]
    ledger: Ledger

    def experiment(self, experiment_id: str) -> Experiment | None:
        with self.uow() as uow:
            return uow.experiments.get(experiment_id)

    def experiments(self) -> Sequence[Experiment]:
        with self.uow() as uow:
            return uow.experiments.list()

    def manifest(self, experiment_id: str) -> dict[str, Any] | None:
        with self.uow() as uow:
            return uow.experiments.manifest(experiment_id)

    def lineage(self, entity_id: str) -> list[str]:
        with self.uow() as uow:
            return uow.evidence.lineage(entity_id)

    def ledger_entries(self) -> Sequence[LedgerEntry]:
        return self.ledger.entries()

    def hypotheses(self) -> Sequence[Hypothesis]:
        with self.uow() as uow:
            return uow.hypotheses.list_latest()

    def hypothesis_versions(self, hypothesis_id: str) -> Sequence[Hypothesis]:
        with self.uow() as uow:
            return uow.hypotheses.versions(hypothesis_id)

    def dossiers(self, experiment_id: str) -> Sequence[Dossier]:
        with self.uow() as uow:
            return uow.dossiers.for_experiment(experiment_id)

    def dossier(self, dossier_id: str) -> Dossier | None:
        with self.uow() as uow:
            return uow.dossiers.get(dossier_id)

    def datasets(self) -> Sequence[DatasetArtifact]:
        with self.uow() as uow:
            return uow.datasets.list()

    def events(self, after: int = 0, limit: int | None = None) -> Sequence[StoredEvent]:
        with self.uow() as uow:
            return uow.events.read(after, limit)
