"""Metadata repositories and the unit of work that makes events transactional (KRN-010).

The spec's driven-port catalogue names DatasetRepository explicitly and places experiment,
hypothesis and provenance metadata in PostgreSQL; these repositories are those tables' ports.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from typing import Any, Protocol, Self

from sdp.kernel.domain.dossier import Dossier
from sdp.kernel.domain.experiment import Experiment
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.predeclaration import FrozenPredeclaration, Predeclaration
from sdp.kernel.ports.events import EventLog
from sdp.kernel.ports.evidence import EvidenceRepository
from sdp.kernel.ports.integration import PlanRepository
from sdp.kernel.ports.investigate import InvestigationRepository
from sdp.kernel.ports.storage import DatasetRepository


class HypothesisRepository(Protocol):
    def add(self, hypothesis: Hypothesis) -> None:
        """Append a version; versions are never overwritten (KRN-001)."""
        ...

    def get(self, hypothesis_id: str, version: int | None = None) -> Hypothesis | None: ...

    def versions(self, hypothesis_id: str) -> Sequence[Hypothesis]: ...

    def list_latest(self) -> Sequence[Hypothesis]: ...


class PredeclarationRepository(Protocol):
    def save_draft(self, predeclaration: Predeclaration) -> None: ...

    def save_frozen(self, frozen: FrozenPredeclaration, ledger_entry_id: str) -> None: ...

    def get_frozen(self, predeclaration_hash: str) -> FrozenPredeclaration | None: ...

    def get_draft(self, predeclaration_id: str) -> Predeclaration | None: ...

    def list_frozen(self) -> Sequence[tuple[FrozenPredeclaration, str]]: ...


class ExperimentRepository(Protocol):
    def save(self, experiment: Experiment) -> None: ...

    def get(self, experiment_id: str) -> Experiment | None: ...

    def list(self) -> Sequence[Experiment]: ...

    def save_manifest(self, experiment_id: str, manifest: dict[str, Any]) -> None:
        """Mirror of the manifest written to the artifact store."""
        ...

    def manifest(self, experiment_id: str) -> dict[str, Any] | None: ...


class DossierRepository(Protocol):
    def save(self, dossier: Dossier) -> None: ...

    def get(self, dossier_id: str) -> Dossier | None: ...

    def for_experiment(self, experiment_id: str) -> Sequence[Dossier]: ...


class UnitOfWork(Protocol):
    """One transaction over the metadata store: state changes and their events commit together."""

    @property
    def hypotheses(self) -> HypothesisRepository: ...

    @property
    def predeclarations(self) -> PredeclarationRepository: ...

    @property
    def experiments(self) -> ExperimentRepository: ...

    @property
    def dossiers(self) -> DossierRepository: ...

    @property
    def datasets(self) -> DatasetRepository: ...

    @property
    def events(self) -> EventLog: ...

    @property
    def evidence(self) -> EvidenceRepository: ...

    @property
    def investigations(self) -> InvestigationRepository: ...

    @property
    def plans(self) -> PlanRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        """Roll back unless commit() was called."""
        ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
