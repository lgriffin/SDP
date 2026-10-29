"""PostgreSQL adapters: metadata repositories, event log and evidence graph in one transaction (KRN-010).

Aggregates are stored as the same lossless JSON records the fakes use (sdp.kernel.application.records),
so the contract suites hold both to one behaviour. A unit of work is one database transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import psycopg
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from sdp.kernel.application.records import dossier_from_record, experiment_from_record, experiment_to_record
from sdp.kernel.domain.dataset import DatasetArtifact
from sdp.kernel.domain.dossier import Dossier
from sdp.kernel.domain.errors import ImmutableArtifact, InvariantViolation
from sdp.kernel.domain.events import DomainEvent, event_from_document
from sdp.kernel.domain.experiment import Experiment
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.integration import AcquisitionPlan
from sdp.kernel.domain.investigation import Investigation, InvestigationSet
from sdp.kernel.domain.predeclaration import FrozenPredeclaration, Predeclaration
from sdp.kernel.domain.provenance import RELATIONS, ProvGraph
from sdp.kernel.ports.events import StoredEvent
from sdp.kernel.ports.integration import PlanExecution, PlanRecord


def _json(value: Any) -> Jsonb:
    return Jsonb(value)


class PostgresHypothesisRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def add(self, hypothesis: Hypothesis) -> None:
        row = self._c.execute(
            "SELECT coalesce(max(version), 0) FROM hypotheses WHERE hypothesis_id = %s", (hypothesis.hypothesis_id,)
        ).fetchone()
        latest = int(row[0]) if row else 0
        if hypothesis.version != latest + 1:
            raise InvariantViolation(
                f"{hypothesis.hypothesis_id} v{hypothesis.version} would not append; versions are never overwritten"
            )
        self._c.execute(
            "INSERT INTO hypotheses (hypothesis_id, version, document) VALUES (%s, %s, %s)",
            (hypothesis.hypothesis_id, hypothesis.version, _json(hypothesis.to_document())),
        )

    def get(self, hypothesis_id: str, version: int | None = None) -> Hypothesis | None:
        if version is None:
            row = self._c.execute(
                "SELECT document FROM hypotheses WHERE hypothesis_id = %s ORDER BY version DESC LIMIT 1",
                (hypothesis_id,),
            ).fetchone()
        else:
            row = self._c.execute(
                "SELECT document FROM hypotheses WHERE hypothesis_id = %s AND version = %s", (hypothesis_id, version)
            ).fetchone()
        return Hypothesis.from_document(row[0]) if row else None

    def versions(self, hypothesis_id: str) -> Sequence[Hypothesis]:
        rows = self._c.execute(
            "SELECT document FROM hypotheses WHERE hypothesis_id = %s ORDER BY version", (hypothesis_id,)
        ).fetchall()
        return [Hypothesis.from_document(r[0]) for r in rows]

    def list_latest(self) -> Sequence[Hypothesis]:
        rows = self._c.execute(
            "SELECT DISTINCT ON (hypothesis_id) document FROM hypotheses ORDER BY hypothesis_id, version DESC"
        ).fetchall()
        return [Hypothesis.from_document(r[0]) for r in rows]


class PostgresPredeclarationRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def save_draft(self, predeclaration: Predeclaration) -> None:
        self._c.execute(
            "INSERT INTO predeclaration_drafts (predeclaration_id, document) VALUES (%s, %s)"
            " ON CONFLICT (predeclaration_id) DO UPDATE SET document = excluded.document",
            (predeclaration.predeclaration_id, _json(predeclaration.to_document())),
        )

    def save_frozen(self, frozen: FrozenPredeclaration, ledger_entry_id: str) -> None:
        self._c.execute(
            "INSERT INTO frozen_predeclarations (predeclaration_hash, document, ledger_entry_id) VALUES (%s, %s, %s)"
            " ON CONFLICT (predeclaration_hash) DO NOTHING",
            (frozen.hash, _json(frozen.predeclaration.to_document()), ledger_entry_id),
        )

    def get_frozen(self, predeclaration_hash: str) -> FrozenPredeclaration | None:
        row = self._c.execute(
            "SELECT document FROM frozen_predeclarations WHERE predeclaration_hash = %s", (predeclaration_hash,)
        ).fetchone()
        return FrozenPredeclaration(Predeclaration.from_document(row[0]), predeclaration_hash) if row else None

    def get_draft(self, predeclaration_id: str) -> Predeclaration | None:
        row = self._c.execute(
            "SELECT document FROM predeclaration_drafts WHERE predeclaration_id = %s", (predeclaration_id,)
        ).fetchone()
        return Predeclaration.from_document(row[0]) if row else None

    def list_frozen(self) -> Sequence[tuple[FrozenPredeclaration, str]]:
        rows = self._c.execute(
            "SELECT predeclaration_hash, document, ledger_entry_id FROM frozen_predeclarations ORDER BY frozen_at"
        ).fetchall()
        return [(FrozenPredeclaration(Predeclaration.from_document(doc), h), entry) for h, doc, entry in rows]


class PostgresExperimentRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def save(self, experiment: Experiment) -> None:
        self._c.execute(
            "INSERT INTO experiments (experiment_id, state, protocol_name, record) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (experiment_id) DO UPDATE SET state = excluded.state, record = excluded.record,"
            " updated_at = now()",
            (
                experiment.experiment_id,
                experiment.state.value,
                experiment.definition.protocol.name,
                _json(experiment_to_record(experiment)),
            ),
        )

    def get(self, experiment_id: str) -> Experiment | None:
        row = self._c.execute("SELECT record FROM experiments WHERE experiment_id = %s", (experiment_id,)).fetchone()
        return experiment_from_record(row[0]) if row else None

    def list(self) -> Sequence[Experiment]:
        rows = self._c.execute("SELECT record FROM experiments ORDER BY experiment_id").fetchall()
        return [experiment_from_record(r[0]) for r in rows]

    def save_manifest(self, experiment_id: str, manifest: dict[str, Any]) -> None:
        self._c.execute(
            "UPDATE experiments SET manifest = %s WHERE experiment_id = %s", (_json(manifest), experiment_id)
        )

    def manifest(self, experiment_id: str) -> dict[str, Any] | None:
        row = self._c.execute("SELECT manifest FROM experiments WHERE experiment_id = %s", (experiment_id,)).fetchone()
        return dict(row[0]) if row and row[0] is not None else None


class PostgresDossierRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def save(self, dossier: Dossier) -> None:
        self._c.execute(
            "INSERT INTO dossiers (dossier_id, experiment_id, document) VALUES (%s, %s, %s)"
            " ON CONFLICT (dossier_id) DO UPDATE SET document = excluded.document",
            (dossier.dossier_id, dossier.experiment_id, _json(dossier.to_document())),
        )

    def get(self, dossier_id: str) -> Dossier | None:
        row = self._c.execute("SELECT document FROM dossiers WHERE dossier_id = %s", (dossier_id,)).fetchone()
        return dossier_from_record(row[0]) if row else None

    def for_experiment(self, experiment_id: str) -> Sequence[Dossier]:
        rows = self._c.execute(
            "SELECT document FROM dossiers WHERE experiment_id = %s ORDER BY dossier_id", (experiment_id,)
        ).fetchall()
        return [dossier_from_record(r[0]) for r in rows]


class PostgresDatasetRepository:
    """DatasetArtifact metadata; re-registering an id with different metadata is refused (KRN-014)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def add(self, artifact: DatasetArtifact) -> None:
        document = artifact.to_document()
        existing = self._c.execute(
            "SELECT document FROM datasets WHERE artifact_id = %s", (artifact.artifact_id.value,)
        ).fetchone()
        if existing is not None:
            if existing[0] != document:
                raise ImmutableArtifact(
                    f"{artifact.artifact_id} is already registered with different metadata (KRN-014)"
                )
            return
        self._c.execute(
            "INSERT INTO datasets (artifact_id, kind, idempotency_key, parents, document) VALUES (%s, %s, %s, %s, %s)",
            (
                artifact.artifact_id.value,
                artifact.kind.value,
                artifact.acquisition.idempotency_key if artifact.acquisition else None,
                [p.value for p in artifact.parents],
                _json(document),
            ),
        )

    def get(self, artifact_id: ArtifactId) -> DatasetArtifact | None:
        row = self._c.execute("SELECT document FROM datasets WHERE artifact_id = %s", (artifact_id.value,)).fetchone()
        return DatasetArtifact.from_document(row[0]) if row else None

    def find_by_idempotency_key(self, key: str) -> DatasetArtifact | None:
        row = self._c.execute(
            "SELECT document FROM datasets WHERE idempotency_key = %s ORDER BY artifact_id LIMIT 1", (key,)
        ).fetchone()
        return DatasetArtifact.from_document(row[0]) if row else None

    def children(self, parent: ArtifactId) -> Sequence[DatasetArtifact]:
        rows = self._c.execute(
            "SELECT document FROM datasets WHERE %s = ANY(parents) ORDER BY artifact_id", (parent.value,)
        ).fetchall()
        return [DatasetArtifact.from_document(r[0]) for r in rows]

    def list(self) -> Sequence[DatasetArtifact]:
        rows = self._c.execute("SELECT document FROM datasets ORDER BY artifact_id").fetchall()
        return [DatasetArtifact.from_document(r[0]) for r in rows]


class PostgresEventLog:
    """Append-only (a trigger refuses updates and deletes); each insert NOTIFYs sdp_events."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def append(self, events: Sequence[DomainEvent]) -> Sequence[int]:
        sequences = []
        for event in events:
            row = self._c.execute(
                "INSERT INTO events (name, aggregate_id, occurred_at, document) VALUES (%s, %s, %s, %s)"
                " RETURNING sequence",
                (event.name, event.aggregate_id, event.occurred_at, _json(event.to_document())),
            ).fetchone()
            assert row is not None
            sequences.append(int(row[0]))
        return sequences

    def read(self, after: int = 0, limit: int | None = None) -> Sequence[StoredEvent]:
        rows = self._c.execute(
            "SELECT sequence, document FROM events WHERE sequence > %s ORDER BY sequence LIMIT %s", (after, limit)
        ).fetchall()
        return [StoredEvent(int(s), event_from_document(d)) for s, d in rows]

    def for_aggregate(self, aggregate_id: str) -> Sequence[StoredEvent]:
        rows = self._c.execute(
            "SELECT sequence, document FROM events WHERE aggregate_id = %s ORDER BY sequence", (aggregate_id,)
        ).fetchall()
        return [StoredEvent(int(s), event_from_document(d)) for s, d in rows]


class PostgresEvidenceGraph:
    """PROV nodes and relations as tables. Queries load the connected graph and reuse the domain's
    traversal, so lineage and subgraph semantics stay identical to the fake."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def record(self, graph: ProvGraph) -> None:
        for node_type, nodes in (("entity", graph.entities), ("activity", graph.activities), ("agent", graph.agents)):
            for node_id, attributes in nodes.items():
                self._c.execute(
                    "INSERT INTO prov_nodes (node_id, node_type, attributes) VALUES (%s, %s, %s)"
                    " ON CONFLICT (node_id) DO UPDATE SET attributes = prov_nodes.attributes || excluded.attributes",
                    (node_id, node_type, _json(dict(attributes))),
                )
        for relation in RELATIONS:
            for subject, obj in graph.relations[relation]:
                self._c.execute(
                    "INSERT INTO prov_relations (relation, subject, object) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (relation, subject, obj),
                )

    def _load(self) -> ProvGraph:
        graph = ProvGraph()
        targets = {"entity": graph.entities, "activity": graph.activities, "agent": graph.agents}
        with self._c.cursor(row_factory=tuple_row) as cur:
            for node_id, node_type, attributes in cur.execute("SELECT node_id, node_type, attributes FROM prov_nodes"):
                targets[node_type][node_id] = dict(attributes)
            for relation, subject, obj in cur.execute(
                "SELECT relation, subject, object FROM prov_relations ORDER BY ordinal"
            ):
                graph.relations[relation].append((subject, obj))
        return graph

    def subgraph(self, activity_id: str) -> ProvGraph:
        return self._load().subgraph(activity_id)

    def lineage(self, entity_id: str) -> list[str]:
        return self._load().ancestors(entity_id)


class PostgresInvestigationRepository:
    """Sets and investigations as documents; the schema refuses deletes (INV-010)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def save_set(self, investigation_set: InvestigationSet) -> None:
        self._c.execute(
            "INSERT INTO investigation_sets (set_id, field_id, profile_hash, document) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (set_id) DO UPDATE SET document = EXCLUDED.document, updated_at = now()",
            (
                investigation_set.set_id,
                investigation_set.field.field_id,
                investigation_set.profile_hash,
                _json(investigation_set.to_document()),
            ),
        )

    def get_set(self, set_id: str) -> InvestigationSet | None:
        row = self._c.execute("SELECT document FROM investigation_sets WHERE set_id = %s", (set_id,)).fetchone()
        return InvestigationSet.from_document(row[0]) if row else None

    def list_sets(self) -> Sequence[InvestigationSet]:
        rows = self._c.execute("SELECT document FROM investigation_sets ORDER BY set_id").fetchall()
        return [InvestigationSet.from_document(r[0]) for r in rows]

    def save(self, investigation: Investigation) -> None:
        self._c.execute(
            "INSERT INTO investigations (investigation_id, set_id, state, document) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (investigation_id) DO UPDATE SET state = EXCLUDED.state, document = EXCLUDED.document,"
            " updated_at = now()",
            (
                investigation.investigation_id,
                investigation.set_id,
                investigation.state.value,
                _json(investigation.to_document()),
            ),
        )

    def get(self, investigation_id: str) -> Investigation | None:
        row = self._c.execute(
            "SELECT document FROM investigations WHERE investigation_id = %s", (investigation_id,)
        ).fetchone()
        return Investigation.from_document(row[0]) if row else None

    def for_set(self, set_id: str) -> Sequence[Investigation]:
        rows = self._c.execute(
            "SELECT document FROM investigations WHERE set_id = %s ORDER BY investigation_id", (set_id,)
        ).fetchall()
        return [Investigation.from_document(r[0]) for r in rows]


class PostgresPlanRepository:
    """Plans as documents; the schema refuses an edited plan, a second decision or execution, and
    deletes (ADR-020)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def _row(self, plan_id: str) -> tuple[Any, ...] | None:
        return self._c.execute(
            "SELECT document, set_id, execution FROM acquisition_plans WHERE plan_id = %s FOR UPDATE", (plan_id,)
        ).fetchone()

    def add(self, plan: AcquisitionPlan) -> None:
        if plan.approval is not None:
            raise InvariantViolation("a plan is stored as drafted and decided afterwards")
        self._c.execute(
            "INSERT INTO acquisition_plans (plan_id, profile_hash, document) VALUES (%s, %s, %s)"
            " ON CONFLICT (plan_id) DO NOTHING",
            (plan.plan_id, plan.profile_hash, _json(plan.to_document())),
        )

    def decide(self, plan: AcquisitionPlan) -> None:
        if plan.approval is None:
            raise InvariantViolation("a decision records an approval or a refusal")
        row = self._row(plan.plan_id)
        if row is None:
            raise InvariantViolation(f"no plan {plan.plan_id} to decide")
        if row[0]["approval"] is not None:
            raise InvariantViolation(f"plan {plan.plan_id} is already decided")
        self._c.execute(
            "UPDATE acquisition_plans SET document = %s, decision = %s WHERE plan_id = %s",
            (_json(plan.to_document()), plan.approval.decision.value, plan.plan_id),
        )

    def executed(self, plan_id: str, set_id: str, execution: PlanExecution) -> None:
        row = self._row(plan_id)
        if row is None:
            raise InvariantViolation(f"no plan {plan_id} to record an execution for")
        approval = row[0]["approval"]
        if approval is None or approval["decision"] == "refused":
            raise InvariantViolation(f"plan {plan_id} is not approved; it cannot have been executed")
        if row[2] is not None:
            raise InvariantViolation(f"plan {plan_id} is already executed")
        self._c.execute(
            "UPDATE acquisition_plans SET set_id = %s, execution = %s WHERE plan_id = %s",
            (set_id, _json(execution.to_document()), plan_id),
        )

    @staticmethod
    def _record(row: tuple[Any, ...]) -> PlanRecord:
        return PlanRecord(
            AcquisitionPlan.from_document(row[0]), row[1], PlanExecution.from_document(row[2]) if row[2] else None
        )

    def get(self, plan_id: str) -> PlanRecord | None:
        row = self._c.execute(
            "SELECT document, set_id, execution FROM acquisition_plans WHERE plan_id = %s", (plan_id,)
        ).fetchone()
        return self._record(row) if row else None

    def list(self) -> Sequence[PlanRecord]:
        rows = self._c.execute("SELECT document, set_id, execution FROM acquisition_plans ORDER BY plan_id").fetchall()
        return [self._record(r) for r in rows]


@dataclass(frozen=True)
class PostgresDatabase:
    """Composition-root handle: ``unit_of_work`` opens one connection and one transaction."""

    dsn: str

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(self.dsn)


class PostgresUnitOfWork:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: psycopg.Connection | None = None

    def __enter__(self) -> Self:
        self._conn = psycopg.connect(self._dsn, autocommit=False, client_encoding="utf8")
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        assert self._conn is not None
        try:
            if not self._conn.closed:
                self._conn.rollback()
        finally:
            self._conn.close()
            self._conn = None

    @property
    def _c(self) -> psycopg.Connection:
        if self._conn is None:
            raise RuntimeError("use the unit of work inside a with block")
        return self._conn

    @property
    def hypotheses(self) -> PostgresHypothesisRepository:
        return PostgresHypothesisRepository(self._c)

    @property
    def predeclarations(self) -> PostgresPredeclarationRepository:
        return PostgresPredeclarationRepository(self._c)

    @property
    def experiments(self) -> PostgresExperimentRepository:
        return PostgresExperimentRepository(self._c)

    @property
    def dossiers(self) -> PostgresDossierRepository:
        return PostgresDossierRepository(self._c)

    @property
    def datasets(self) -> PostgresDatasetRepository:
        return PostgresDatasetRepository(self._c)

    @property
    def events(self) -> PostgresEventLog:
        return PostgresEventLog(self._c)

    @property
    def evidence(self) -> PostgresEvidenceGraph:
        return PostgresEvidenceGraph(self._c)

    @property
    def investigations(self) -> PostgresInvestigationRepository:
        return PostgresInvestigationRepository(self._c)

    @property
    def plans(self) -> PostgresPlanRepository:
        return PostgresPlanRepository(self._c)

    def commit(self) -> None:
        self._c.commit()

    def rollback(self) -> None:
        self._c.rollback()
