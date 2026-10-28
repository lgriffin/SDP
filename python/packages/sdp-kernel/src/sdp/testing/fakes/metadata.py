"""The in-memory metadata store: repositories, event log and evidence graph behind one unit of work.

The unit of work snapshots the whole store on entry and restores it unless commit() is called,
which is exactly the transactional behaviour KRN-010 asks of the PostgreSQL adapter.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Self

from sdp.kernel.application.records import dossier_from_record, experiment_from_record, experiment_to_record
from sdp.kernel.domain.dossier import Dossier
from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.events import DomainEvent
from sdp.kernel.domain.experiment import Experiment
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.predeclaration import FrozenPredeclaration, Predeclaration
from sdp.kernel.domain.provenance import ProvGraph
from sdp.kernel.ports.events import StoredEvent
from sdp.testing.fakes.storage import InMemoryDatasetRepository


@dataclass
class _State:
    hypotheses: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    drafts: dict[str, dict[str, Any]] = field(default_factory=dict)
    frozen: dict[str, tuple[dict[str, Any], str]] = field(default_factory=dict)
    experiments: dict[str, dict[str, Any]] = field(default_factory=dict)
    manifests: dict[str, dict[str, Any]] = field(default_factory=dict)
    dossiers: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[StoredEvent] = field(default_factory=list)
    graph: ProvGraph = field(default_factory=ProvGraph)
    datasets: InMemoryDatasetRepository = field(default_factory=InMemoryDatasetRepository)


class InMemoryHypothesisRepository:
    def __init__(self, state: _State) -> None:
        self._s = state

    def add(self, hypothesis: Hypothesis) -> None:
        versions = self._s.hypotheses.setdefault(hypothesis.hypothesis_id, [])
        if hypothesis.version != len(versions) + 1:
            raise InvariantViolation(
                f"{hypothesis.hypothesis_id} v{hypothesis.version} would not append; versions are never overwritten"
            )
        versions.append(hypothesis.to_document())

    def get(self, hypothesis_id: str, version: int | None = None) -> Hypothesis | None:
        versions = self._s.hypotheses.get(hypothesis_id)
        if not versions:
            return None
        doc = versions[-1] if version is None else (versions[version - 1] if 0 < version <= len(versions) else None)
        return Hypothesis.from_document(doc) if doc else None

    def versions(self, hypothesis_id: str) -> Sequence[Hypothesis]:
        return [Hypothesis.from_document(d) for d in self._s.hypotheses.get(hypothesis_id, [])]

    def list_latest(self) -> Sequence[Hypothesis]:
        return [Hypothesis.from_document(v[-1]) for _, v in sorted(self._s.hypotheses.items())]


class InMemoryPredeclarationRepository:
    def __init__(self, state: _State) -> None:
        self._s = state

    def save_draft(self, predeclaration: Predeclaration) -> None:
        self._s.drafts[predeclaration.predeclaration_id] = predeclaration.to_document()

    def save_frozen(self, frozen: FrozenPredeclaration, ledger_entry_id: str) -> None:
        if frozen.hash in self._s.frozen:
            return
        self._s.frozen[frozen.hash] = (frozen.predeclaration.to_document(), ledger_entry_id)

    def get_frozen(self, predeclaration_hash: str) -> FrozenPredeclaration | None:
        item = self._s.frozen.get(predeclaration_hash)
        if item is None:
            return None
        return FrozenPredeclaration(Predeclaration.from_document(item[0]), predeclaration_hash)

    def get_draft(self, predeclaration_id: str) -> Predeclaration | None:
        doc = self._s.drafts.get(predeclaration_id)
        return Predeclaration.from_document(doc) if doc else None

    def list_frozen(self) -> Sequence[tuple[FrozenPredeclaration, str]]:
        return [
            (FrozenPredeclaration(Predeclaration.from_document(doc), h), entry)
            for h, (doc, entry) in self._s.frozen.items()
        ]


class InMemoryExperimentRepository:
    def __init__(self, state: _State) -> None:
        self._s = state

    def save(self, experiment: Experiment) -> None:
        self._s.experiments[experiment.experiment_id] = experiment_to_record(experiment)

    def get(self, experiment_id: str) -> Experiment | None:
        record = self._s.experiments.get(experiment_id)
        return experiment_from_record(copy.deepcopy(record)) if record else None

    def list(self) -> Sequence[Experiment]:
        return [experiment_from_record(copy.deepcopy(r)) for _, r in sorted(self._s.experiments.items())]

    def save_manifest(self, experiment_id: str, manifest: dict[str, Any]) -> None:
        self._s.manifests[experiment_id] = copy.deepcopy(manifest)

    def manifest(self, experiment_id: str) -> dict[str, Any] | None:
        m = self._s.manifests.get(experiment_id)
        return copy.deepcopy(m) if m else None


class InMemoryDossierRepository:
    def __init__(self, state: _State) -> None:
        self._s = state

    def save(self, dossier: Dossier) -> None:
        self._s.dossiers[dossier.dossier_id] = dossier.to_document()

    def get(self, dossier_id: str) -> Dossier | None:
        doc = self._s.dossiers.get(dossier_id)
        return dossier_from_record(copy.deepcopy(doc)) if doc else None

    def for_experiment(self, experiment_id: str) -> Sequence[Dossier]:
        return [
            dossier_from_record(copy.deepcopy(d))
            for d in self._s.dossiers.values()
            if d["experiment_id"] == experiment_id
        ]


class InMemoryEventLog:
    def __init__(self, state: _State | None = None) -> None:
        self._s = state or _State()

    def append(self, events: Sequence[DomainEvent]) -> Sequence[int]:
        sequences = []
        for event in events:
            seq = len(self._s.events) + 1
            self._s.events.append(StoredEvent(seq, event))
            sequences.append(seq)
        return sequences

    def read(self, after: int = 0, limit: int | None = None) -> Sequence[StoredEvent]:
        items = [e for e in self._s.events if e.sequence > after]
        return items[:limit] if limit is not None else items

    def for_aggregate(self, aggregate_id: str) -> Sequence[StoredEvent]:
        return [e for e in self._s.events if e.event.aggregate_id == aggregate_id]


class InMemoryEvidenceGraph:
    def __init__(self, state: _State | None = None) -> None:
        self._s = state or _State()

    def record(self, graph: ProvGraph) -> None:
        self._s.graph.merge(graph)

    def subgraph(self, activity_id: str) -> ProvGraph:
        return self._s.graph.subgraph(activity_id)

    def lineage(self, entity_id: str) -> list[str]:
        return self._s.graph.ancestors(entity_id)


@dataclass
class InMemoryDatabase:
    """The shared store; each unit of work operates on it transactionally."""

    state: _State = field(default_factory=_State)
    commits: int = 0

    def unit_of_work(self) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(self)


class InMemoryUnitOfWork:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db
        self._snapshot: _State | None = None
        self._committed = False
        state = db.state
        self.hypotheses = InMemoryHypothesisRepository(state)
        self.predeclarations = InMemoryPredeclarationRepository(state)
        self.experiments = InMemoryExperimentRepository(state)
        self.dossiers = InMemoryDossierRepository(state)
        self.datasets = state.datasets
        self.events = InMemoryEventLog(state)
        self.evidence = InMemoryEvidenceGraph(state)

    def __enter__(self) -> Self:
        self._snapshot = copy.deepcopy(self._db.state)
        self._committed = False
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if not self._committed:
            self.rollback()

    def commit(self) -> None:
        self._committed = True
        self._db.commits += 1
        self._snapshot = copy.deepcopy(self._db.state)

    def rollback(self) -> None:
        if self._snapshot is None:
            return
        restored = self._snapshot
        state = self._db.state
        for name in state.__dataclass_fields__:
            setattr(state, name, getattr(restored, name))
        self.datasets = state.datasets
        self._snapshot = copy.deepcopy(state)
