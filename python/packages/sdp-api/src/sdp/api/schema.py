"""The GraphQL query side (ADR-016). Read-only: commands go through the CLI's signed workflow.

``contracts/graphql/schema.graphql`` is exported from this module and committed; a drift test and
the TypeScript client generator both read the committed copy (CTR-005).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import strawberry
from strawberry.scalars import JSON
from strawberry.types import Info

from sdp.kernel.application.kernel import Kernel
from sdp.kernel.domain.dataset import DatasetArtifact
from sdp.kernel.domain.dossier import Dossier as DomainDossier
from sdp.kernel.domain.experiment import Experiment as DomainExperiment
from sdp.kernel.domain.gates import Check
from sdp.kernel.domain.hypothesis import Hypothesis as DomainHypothesis
from sdp.kernel.ports.events import StoredEvent
from sdp.kernel.ports.ledger import LedgerEntry as DomainLedgerEntry

if TYPE_CHECKING:
    JSONValue = Any
else:
    # Strawberry's JSON scalar is a NewType over object; mypy sees any JSON-compatible value.
    JSONValue = JSON


def _kernel(info: Info[Any, None]) -> Kernel:
    kernel: Kernel = info.context["kernel"]
    return kernel


@strawberry.type(description="One predeclared gate evaluated against a finding.")
class GateCheck:
    gate_id: str
    finding: str
    value: JSONValue | None
    unit: str | None
    comparator: str
    threshold: JSONValue
    severity: str
    oracle_ids: list[str]
    oracles_verified: bool
    outcome: str

    @classmethod
    def of(cls, c: Check) -> GateCheck:
        return cls(
            gate_id=c.gate_id,
            finding=c.finding,
            value=c.value,
            unit=c.unit,
            comparator=c.comparator.value,
            threshold=c.threshold,
            severity=c.severity.value,
            oracle_ids=list(c.oracle_ids),
            oracles_verified=c.oracles_verified,
            outcome=c.outcome.value,
        )


@strawberry.type
class Verdict:
    status: str
    protocol_version: str
    reasons: list[str]


@strawberry.type
class Task:
    task_id: str
    task_kind: str
    depends_on: list[str]
    status: str
    checkpoint: str | None
    duration_s: float | None


@strawberry.type
class Experiment:
    id: strawberry.ID
    state: str
    protocol: str
    protocol_version: str
    predeclaration_hash: str
    seed: int
    backend: str
    parameters: JSONValue
    inputs: JSONValue
    tasks: list[Task]
    verdict: Verdict | None
    checks: list[GateCheck]
    failure: JSONValue | None
    refusal: str | None
    limitations: list[str]
    manifest_id: str | None
    bundle_doi: str | None
    started_at: str | None
    finished_at: str | None

    @strawberry.field(description="The evidence manifest, as written to the NAS.")  # type: ignore[untyped-decorator]
    def manifest(self, info: Info[Any, None]) -> JSONValue | None:
        return _kernel(info).query_evidence.manifest(str(self.id))

    @strawberry.field
    def dossiers(self, info: Info[Any, None]) -> list[Dossier]:
        return [Dossier.of(d) for d in _kernel(info).query_evidence.dossiers(str(self.id))]

    @classmethod
    def of(cls, e: DomainExperiment) -> Experiment:
        d = e.definition
        report = e.report
        return cls(
            id=strawberry.ID(e.experiment_id),
            state=e.state.value,
            protocol=d.protocol.name,
            protocol_version=d.protocol.version,
            predeclaration_hash=d.predeclaration_hash,
            seed=d.seed,
            backend=d.environment.backend,
            parameters=dict(d.parameters),
            inputs={b.role: b.artifact_id.value for b in d.inputs},
            tasks=[
                Task(
                    task_id=t.task_id,
                    task_kind=t.task_kind,
                    depends_on=list(t.depends_on),
                    status=t.status,
                    checkpoint=t.checkpoint.value if t.checkpoint else None,
                    duration_s=t.duration_s,
                )
                for t in e.tasks.values()
            ],
            verdict=Verdict(
                status=report.verdict.status.value,
                protocol_version=report.verdict.protocol_version,
                reasons=list(report.verdict.reasons),
            )
            if report
            else None,
            checks=[GateCheck.of(c) for c in report.checks] if report else [],
            failure=e.failure,
            refusal=e.refusal,
            limitations=list(e.limitations),
            manifest_id=e.manifest_id.value if e.manifest_id else None,
            bundle_doi=e.bundle_doi,
            started_at=e.started_at.isoformat() if e.started_at else None,
            finished_at=e.finished_at.isoformat() if e.finished_at else None,
        )


@strawberry.type
class Dossier:
    id: strawberry.ID
    experiment_id: str
    manifest_id: str
    verdict: str
    findings: JSONValue
    limitations: list[str]
    sections: JSONValue
    approvals: JSONValue

    @classmethod
    def of(cls, d: DomainDossier) -> Dossier:
        return cls(
            id=strawberry.ID(d.dossier_id),
            experiment_id=d.experiment_id,
            manifest_id=d.manifest_id.value,
            verdict=d.verdict,
            findings=dict(d.findings),
            limitations=list(d.limitations),
            sections=dict(d.sections),
            approvals=[dict(a) for a in d.approvals],
        )


@strawberry.type
class Hypothesis:
    id: strawberry.ID
    version: int
    statement: str
    evaluation_criteria: list[str]
    supersedes: int | None

    @classmethod
    def of(cls, h: DomainHypothesis) -> Hypothesis:
        return cls(
            id=strawberry.ID(h.hypothesis_id),
            version=h.version,
            statement=h.statement,
            evaluation_criteria=list(h.evaluation_criteria),
            supersedes=h.supersedes,
        )


@strawberry.type
class Dataset:
    id: strawberry.ID
    kind: str
    status: str
    media_type: str
    byte_count: int
    parents: list[str]
    document: JSONValue

    @classmethod
    def of(cls, a: DatasetArtifact) -> Dataset:
        return cls(
            id=strawberry.ID(a.artifact_id.value),
            kind=a.kind.value,
            status=a.status.value,
            media_type=a.media_type,
            byte_count=a.byte_count,
            parents=[p.value for p in a.parents],
            document=a.to_document(),
        )


@strawberry.type
class Event:
    sequence: int
    name: str
    aggregate_id: str
    occurred_at: str
    payload: JSONValue

    @classmethod
    def of(cls, s: StoredEvent) -> Event:
        return cls(
            sequence=s.sequence,
            name=s.event.name,
            aggregate_id=s.event.aggregate_id,
            occurred_at=s.event.occurred_at.isoformat(),
            payload=dict(s.event.payload),
        )


@strawberry.type
class LedgerEntry:
    id: strawberry.ID
    sequence: int
    kind: str
    subject_id: str
    subject_hash: str
    recorded_at: str
    signer: str
    signature_method: str
    previous_entry_hash: str | None
    payload: JSONValue

    @classmethod
    def of(cls, e: DomainLedgerEntry) -> LedgerEntry:
        doc = e.to_document()
        return cls(
            id=strawberry.ID(e.entry_id),
            sequence=e.sequence,
            kind=e.kind,
            subject_id=e.subject_id,
            subject_hash=e.subject_hash,
            recorded_at=doc["recorded_at"],
            signer=e.signer,
            signature_method=e.signature_method,
            previous_entry_hash=e.previous_entry_hash,
            payload=dict(e.payload),
        )


@strawberry.type
class Query:
    @strawberry.field
    def experiments(self, info: Info[Any, None], state: str | None = None) -> list[Experiment]:
        found = _kernel(info).query_evidence.experiments()
        return [Experiment.of(e) for e in found if state is None or e.state.value == state]

    @strawberry.field
    def experiment(self, info: Info[Any, None], id: strawberry.ID) -> Experiment | None:
        e = _kernel(info).query_evidence.experiment(str(id))
        return Experiment.of(e) if e else None

    @strawberry.field
    def hypotheses(self, info: Info[Any, None]) -> list[Hypothesis]:
        return [Hypothesis.of(h) for h in _kernel(info).query_evidence.hypotheses()]

    @strawberry.field
    def hypothesis_versions(self, info: Info[Any, None], id: strawberry.ID) -> list[Hypothesis]:
        return [Hypothesis.of(h) for h in _kernel(info).query_evidence.hypothesis_versions(str(id))]

    @strawberry.field
    def dossier(self, info: Info[Any, None], id: strawberry.ID) -> Dossier | None:
        d = _kernel(info).query_evidence.dossier(str(id))
        return Dossier.of(d) if d else None

    @strawberry.field
    def datasets(self, info: Info[Any, None]) -> list[Dataset]:
        return [Dataset.of(a) for a in _kernel(info).query_evidence.datasets()]

    @strawberry.field(description="Domain events after a sequence number, oldest first.")  # type: ignore[untyped-decorator]
    def events(self, info: Info[Any, None], after: int = 0, limit: int = 100) -> list[Event]:
        return [Event.of(s) for s in _kernel(info).query_evidence.events(after, min(limit, 1000))]

    @strawberry.field(description="Ancestors of an entity in the provenance graph.")  # type: ignore[untyped-decorator]
    def lineage(self, info: Info[Any, None], entity_id: strawberry.ID) -> list[str]:
        return _kernel(info).query_evidence.lineage(str(entity_id))

    @strawberry.field
    def ledger(self, info: Info[Any, None]) -> list[LedgerEntry]:
        return [LedgerEntry.of(e) for e in _kernel(info).query_evidence.ledger_entries()]


schema = strawberry.Schema(query=Query)


def export_schema() -> str:
    return schema.as_str() + "\n"
