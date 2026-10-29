"""The GraphQL query side (ADR-016). Read-only: commands go through the CLI's signed workflow.

``contracts/graphql/schema.graphql`` is exported from this module and committed; a drift test and
the TypeScript client generator both read the committed copy (CTR-005).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import strawberry
from strawberry.scalars import JSON
from strawberry.types import Info

from sdp.api.jobs import REGISTER_JOB, plan_job
from sdp.kernel.application.kernel import Kernel
from sdp.kernel.application.planning import plan_view
from sdp.kernel.domain.dataset import DatasetArtifact
from sdp.kernel.domain.dossier import Dossier as DomainDossier
from sdp.kernel.domain.experiment import Experiment as DomainExperiment
from sdp.kernel.domain.gates import Check
from sdp.kernel.domain.hypothesis import Hypothesis as DomainHypothesis
from sdp.kernel.domain.investigation import Investigation as DomainInvestigation
from sdp.kernel.domain.investigation import InvestigationSet as DomainInvestigationSet
from sdp.kernel.ports.events import StoredEvent
from sdp.kernel.ports.integration import PlanRecord
from sdp.kernel.ports.ledger import LedgerEntry as DomainLedgerEntry

if TYPE_CHECKING:
    JSONValue = Any
else:
    # Strawberry's JSON scalar is a NewType over object; mypy sees any JSON-compatible value.
    JSONValue = JSON


def _kernel(info: Info[Any, None]) -> Kernel:
    kernel: Kernel = info.context["kernel"]
    return kernel


def _job(info: Info[Any, None], key: str) -> dict[str, Any] | None:
    """A long command's state from the API's jobs (sdp.api.jobs), when the server runs any."""
    jobs = info.context.get("jobs")
    job = jobs.get(key) if jobs is not None else None
    return job.to_document() if job is not None else None


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


@strawberry.type(description="One Investigate stage of a set (Investigate §1).")
class InvestigationStage:
    stage: str
    state: str
    artifact_id: str | None
    finished_at: str | None
    error: str | None
    summary: JSONValue


@strawberry.type(description="A state change of an investigation, with who made it and why.")
class InvestigationTransition:
    from_state: str | None
    to_state: str
    reason: str
    actor: str
    at: str


@strawberry.type(description="One candidate from a field: EXPLORATORY, never a verdict (INV-008).")
class Investigation:
    id: strawberry.ID
    set_id: str
    field_id: str
    candidate_key: str
    candidate: JSONValue
    state: str
    label: str
    score: float
    ranking_policy: str
    test_results: JSONValue
    evidence_bundle_ids: list[str]
    evidence_used: JSONValue
    transitions: list[InvestigationTransition]
    notes: JSONValue
    links: JSONValue

    @strawberry.field(description="The candidate's dossier, rebuilt from the set's stage artifacts.")  # type: ignore[untyped-decorator]
    def dossier(self, info: Info[Any, None]) -> JSONValue | None:
        query = _kernel(info).query_investigations
        return query.dossier(str(self.id)) if query else None

    @classmethod
    def of(cls, i: DomainInvestigation) -> Investigation:
        doc = i.to_document()
        return cls(
            id=strawberry.ID(i.investigation_id),
            set_id=i.set_id,
            field_id=i.field_id,
            candidate_key=i.candidate.key,
            candidate=doc["candidate"],
            state=i.state.value,
            label=i.label,
            score=i.rank.score,
            ranking_policy=f"{i.rank.policy_id}@{i.rank.policy_version}",
            test_results=doc["test_results"],
            evidence_bundle_ids=list(i.evidence_bundle_ids),
            evidence_used=doc["evidence_used"],
            transitions=[
                InvestigationTransition(
                    from_state=t["from"], to_state=t["to"], reason=t["reason"], actor=t["actor"], at=t["at"]
                )
                for t in doc["transitions"]
            ],
            notes=doc["notes"],
            links=doc["links"],
        )


@strawberry.type(description="One run of Investigate over a field under a profile, with its multiplicity.")
class InvestigationSet:
    id: strawberry.ID
    field_id: str
    field: JSONValue
    profile_id: str
    profile_version: str
    profile_hash: str
    profile: JSONValue
    seed: int
    started_at: str
    complete: bool
    stages: list[InvestigationStage]
    objects_examined: int
    tests_applied: JSONValue
    archive_releases: list[str]
    null_results: JSONValue
    investigation_ids: list[str]
    manifest_id: str | None
    label: str
    plan_artifact_id: str | None = strawberry.field(
        default=None, description="The approved AcquisitionPlan this run executed, when it was planned."
    )

    @strawberry.field(description="The set's manifest, as written to the store.")  # type: ignore[untyped-decorator]
    def manifest(self, info: Info[Any, None]) -> JSONValue | None:
        query = _kernel(info).query_investigations
        return query.manifest(str(self.id)) if query else None

    @strawberry.field(description="The set's investigations, highest score first.")  # type: ignore[untyped-decorator]
    def investigations(self, info: Info[Any, None], state: str | None = None) -> list[Investigation]:
        query = _kernel(info).query_investigations
        found = sorted(query.investigations(str(self.id)) if query else [], key=lambda i: -i.rank.score)
        return [Investigation.of(i) for i in found if state is None or i.state.value == state]

    @classmethod
    def of(cls, s: DomainInvestigationSet) -> InvestigationSet:
        doc = s.to_document()
        return cls(
            id=strawberry.ID(s.set_id),
            field_id=s.field.field_id,
            field=doc["field"],
            profile_id=s.profile.profile_id,
            profile_version=s.profile.version,
            profile_hash=s.profile_hash,
            profile=doc["profile"],
            seed=s.seed,
            started_at=doc["started_at"],
            complete=s.complete,
            stages=[
                InvestigationStage(
                    stage=d["stage"],
                    state=d["state"],
                    artifact_id=d["artifact_id"],
                    finished_at=d["finished_at"],
                    error=d["error"],
                    summary=d["summary"],
                )
                for d in doc["stages"]
            ],
            objects_examined=len(s.objects_examined),
            tests_applied=doc["tests_applied"],
            archive_releases=doc["archive_releases"],
            null_results=doc["null_results"],
            investigation_ids=list(s.investigation_ids),
            manifest_id=doc["manifest_id"],
            label=s.label,
            plan_artifact_id=s.plan_artifact_id,
        )


@strawberry.type(
    description="An AcquisitionPlan (ADR-020) with its state: drafted, auto_approved, approved, refused or executed."
)
class Plan:
    id: strawberry.ID
    set_id: str | None = strawberry.field(description="The investigation set its execution opened.")
    state: str
    document: JSONValue = strawberry.field(description="The plan document (acquisition-plan.schema.json).")
    execution: JSONValue | None = strawberry.field(description="plan_id, acquired artifact ids, unavailable items.")
    run: JSONValue | None = strawberry.field(
        default=None,
        description="The execution started in the background, while it runs and after: state (running, finished or "
        "failed), started_at, finished_at and error. Null when none was started since the server started.",
    )

    @classmethod
    def of(cls, record: PlanRecord, run: dict[str, Any] | None = None) -> Plan:
        view = plan_view(record)
        return cls(
            id=strawberry.ID(view["id"]),
            set_id=view["setId"],
            state=view["state"],
            document=view["document"],
            execution=view["execution"],
            run=run,
        )


@strawberry.type(description="A source plans are matched against, as registered (INV-019).")
class ArchiveSource:
    source_id: str
    origin: str
    verified_on: str = strawberry.field(description="When its descriptor was last read, as an ISO date.")
    product_types: list[str]


@strawberry.type(description="The registered archives and the last registration started on this server.")
class Archives:
    sources: list[ArchiveSource]
    registration: JSONValue | None = strawberry.field(
        description="state (running, finished or failed), started_at, finished_at and error; null if none was started."
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

    @strawberry.field(description="Investigation sets, newest first (Investigate).")  # type: ignore[untyped-decorator]
    def investigation_sets(self, info: Info[Any, None]) -> list[InvestigationSet]:
        query = _kernel(info).query_investigations
        found = sorted(query.sets() if query else [], key=lambda s: s.started_at, reverse=True)
        return [InvestigationSet.of(s) for s in found]

    @strawberry.field
    def investigation_set(self, info: Info[Any, None], id: strawberry.ID) -> InvestigationSet | None:
        query = _kernel(info).query_investigations
        found = query.set(str(id)) if query else None
        return InvestigationSet.of(found) if found else None

    @strawberry.field
    def investigation(self, info: Info[Any, None], id: strawberry.ID) -> Investigation | None:
        query = _kernel(info).query_investigations
        found = query.investigation(str(id)) if query else None
        return Investigation.of(found) if found else None

    @strawberry.field(description="Acquisition plans (ADR-020).")  # type: ignore[untyped-decorator]
    def plans(self, info: Info[Any, None]) -> list[Plan]:
        query = _kernel(info).query_plans
        return [Plan.of(r, _job(info, plan_job(r.plan.plan_id))) for r in (query.plans() if query else [])]

    @strawberry.field
    def plan(self, info: Info[Any, None], id: strawberry.ID) -> Plan | None:
        query = _kernel(info).query_plans
        found = query.plan(str(id)) if query else None
        return Plan.of(found, _job(info, plan_job(str(id)))) if found else None

    @strawberry.field(description="The archives plans are matched against; null when the server cannot register them.")  # type: ignore[untyped-decorator]
    def archives(self, info: Info[Any, None]) -> Archives | None:
        archives = info.context.get("archives")
        if archives is None:
            return None
        return Archives(
            sources=[
                ArchiveSource(
                    source_id=d["source_id"],
                    origin=d["origin"],
                    verified_on=d["verified_on"],
                    product_types=list(d["product_types"]),
                )
                for d in archives.registered()
            ],
            registration=_job(info, REGISTER_JOB),
        )


schema = strawberry.Schema(query=Query)


def export_schema() -> str:
    return schema.as_str() + "\n"
