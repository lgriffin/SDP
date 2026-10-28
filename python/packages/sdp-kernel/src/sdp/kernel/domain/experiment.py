"""Experiment aggregate and its lifecycle (§5).

Defined -> Running -> Completed -> Validated -> Approved -> Published, with Refused (no frozen
predeclaration in the ledger) and Failed (a task error) as retained terminal states. A resumed
experiment re-enters Running from its last checkpoint.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sdp.kernel.domain import events as ev
from sdp.kernel.domain.errors import InvalidTransition, InvariantViolation, PredeclarationNotFrozen
from sdp.kernel.domain.findings import Findings
from sdp.kernel.domain.ids import ArtifactId, ProtocolId
from sdp.kernel.domain.verdict import ValidationReport, VerdictStatus


class ExperimentState(StrEnum):
    DEFINED = "defined"
    REFUSED = "refused"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETED = "completed"
    VALIDATED = "validated"
    APPROVED = "approved"
    PUBLISHED = "published"


_ALLOWED: Mapping[ExperimentState, frozenset[ExperimentState]] = {
    ExperimentState.DEFINED: frozenset({ExperimentState.RUNNING, ExperimentState.REFUSED}),
    ExperimentState.RUNNING: frozenset({ExperimentState.RUNNING, ExperimentState.COMPLETED, ExperimentState.FAILED}),
    ExperimentState.COMPLETED: frozenset({ExperimentState.VALIDATED}),
    ExperimentState.VALIDATED: frozenset({ExperimentState.APPROVED}),
    ExperimentState.APPROVED: frozenset({ExperimentState.PUBLISHED}),
    ExperimentState.REFUSED: frozenset(),
    ExperimentState.FAILED: frozenset(),
    ExperimentState.PUBLISHED: frozenset(),
}


@dataclass(frozen=True)
class InputBinding:
    """An artifact supplied to an experiment under a role the protocol asked for."""

    role: str
    artifact_id: ArtifactId
    media_type: str


@dataclass(frozen=True)
class EnvironmentRecord:
    """Code revision, lock digest, image digest, backend and its versions (KRN-004, KRN-013)."""

    code_revision: str
    lock_digest: str
    backend: str
    python_version: str
    platform: str
    image_digest: str | None = None
    driver_version: str | None = None
    cuda_version: str | None = None
    agreement_test_id: str | None = None
    constants_version: str | None = None
    packages: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.backend not in ("cpu", "cuda"):
            raise InvariantViolation(f"unknown backend {self.backend!r}")
        if self.backend == "cuda" and not (self.driver_version and self.cuda_version and self.agreement_test_id):
            raise InvariantViolation("a CUDA run must record driver, CUDA version and agreement test id (KRN-013)")

    def to_document(self) -> dict[str, Any]:
        return {
            "code_revision": self.code_revision,
            "lock_digest": self.lock_digest,
            "image_digest": self.image_digest,
            "backend": self.backend,
            "driver_version": self.driver_version,
            "cuda_version": self.cuda_version,
            "agreement_test_id": self.agreement_test_id,
            "python_version": self.python_version,
            "platform": self.platform,
            "constants_version": self.constants_version,
            "packages": dict(self.packages),
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> EnvironmentRecord:
        return cls(
            code_revision=doc["code_revision"],
            lock_digest=doc["lock_digest"],
            backend=doc["backend"],
            python_version=doc["python_version"],
            platform=doc["platform"],
            image_digest=doc.get("image_digest"),
            driver_version=doc.get("driver_version"),
            cuda_version=doc.get("cuda_version"),
            agreement_test_id=doc.get("agreement_test_id"),
            constants_version=doc.get("constants_version"),
            packages=dict(doc.get("packages", {})),
        )


@dataclass(frozen=True)
class ExperimentDefinition:
    """Protocol, predeclaration hash, inputs, parameters, seed, code revision and environment."""

    protocol: ProtocolId
    predeclaration_hash: str
    inputs: tuple[InputBinding, ...]
    parameters: Mapping[str, Any]
    seed: int
    environment: EnvironmentRecord

    def input(self, role: str) -> InputBinding:
        for binding in self.inputs:
            if binding.role == role:
                return binding
        raise KeyError(role)


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    task_kind: str
    depends_on: tuple[str, ...]
    policy: Mapping[str, Any]
    status: str = "pending"
    checkpoint: ArtifactId | None = None
    duration_s: float | None = None


@dataclass
class Experiment:
    """The aggregate root. Mutating methods record domain events; pull_events() drains them."""

    experiment_id: str
    definition: ExperimentDefinition
    state: ExperimentState = ExperimentState.DEFINED
    tasks: dict[str, TaskRecord] = field(default_factory=dict)
    findings: Findings | None = None
    report: ValidationReport | None = None
    outputs: dict[str, ArtifactId] = field(default_factory=dict)
    failure: dict[str, Any] | None = None
    refusal: str | None = None
    ledger_entry_id: str | None = None
    approvals: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    manifest_id: ArtifactId | None = None
    bundle_doi: str | None = None
    _pending: list[ev.DomainEvent] = field(default_factory=list, repr=False)

    @classmethod
    def define(cls, experiment_id: str, definition: ExperimentDefinition, at: datetime) -> Experiment:
        exp = cls(experiment_id=experiment_id, definition=definition)
        exp._record(
            ev.ExperimentDefined(
                experiment_id,
                at,
                {
                    "protocol_id": definition.protocol.name,
                    "protocol_version": definition.protocol.version,
                    "predeclaration_hash": definition.predeclaration_hash,
                    "seed": definition.seed,
                },
            )
        )
        return exp

    # -- lifecycle -------------------------------------------------------------------------

    def _move(self, target: ExperimentState) -> None:
        if target not in _ALLOWED[self.state]:
            raise InvalidTransition(f"{self.experiment_id}: {self.state} -> {target} is not allowed")
        self.state = target

    def _record(self, event: ev.DomainEvent) -> None:
        self._pending.append(event)

    def pull_events(self) -> list[ev.DomainEvent]:
        pending, self._pending = self._pending, []
        return pending

    def start(self, *, ledger_entry_id: str | None, at: datetime) -> None:
        """Enter Running only if the predeclaration hash is in the ledger (KRN-003).

        A resume of a Running experiment keeps its checkpoints and re-enters Running.
        """
        if self.state is ExperimentState.RUNNING:
            self._record(ev.ExperimentStarted(self.experiment_id, at, {"resumed": True}))
            return
        if ledger_entry_id is None:
            self.refusal = f"predeclaration {self.definition.predeclaration_hash} is not in the ledger"
            self._move(ExperimentState.REFUSED)
            self.finished_at = at
            self._record(ev.ExperimentRefused(self.experiment_id, at, {"reason": self.refusal}))
            raise PredeclarationNotFrozen(self.refusal)
        self._move(ExperimentState.RUNNING)
        self.ledger_entry_id = ledger_entry_id
        self.started_at = at
        self._record(
            ev.ExperimentStarted(self.experiment_id, at, {"resumed": False, "ledger_entry_id": ledger_entry_id})
        )

    def plan_tasks(self, tasks: list[TaskRecord]) -> None:
        if self.state is not ExperimentState.RUNNING:
            raise InvalidTransition("tasks are planned while Running")
        for task in tasks:
            existing = self.tasks.get(task.task_id)
            if existing is not None and existing.task_kind != task.task_kind:
                raise InvariantViolation(f"resumed plan changed the kind of task {task.task_id}")
            if existing is None:
                self.tasks[task.task_id] = task

    def checkpoint(self, task_id: str, artifact: ArtifactId, duration_s: float, at: datetime) -> None:
        """Record a completed task (KRN-005)."""
        if self.state is not ExperimentState.RUNNING:
            raise InvalidTransition("checkpoints are written while Running")
        task = self.tasks[task_id]
        self.tasks[task_id] = TaskRecord(
            task.task_id, task.task_kind, task.depends_on, task.policy, "completed", artifact, duration_s
        )
        self._record(
            ev.CheckpointWritten(
                self.experiment_id, at, {"task_id": task_id, "artifact_id": artifact.value, "duration_s": duration_s}
            )
        )

    def completed_checkpoints(self) -> dict[str, ArtifactId]:
        return {t.task_id: t.checkpoint for t in self.tasks.values() if t.status == "completed" and t.checkpoint}

    def complete(self, findings: Findings, outputs: Mapping[str, ArtifactId], at: datetime) -> None:
        self._move(ExperimentState.COMPLETED)
        self.findings = findings
        self.outputs.update(outputs)
        self.finished_at = at
        self._record(ev.ExperimentCompleted(self.experiment_id, at, {"findings": sorted(findings.items)}))

    def fail(self, *, task_id: str | None, reason: str, diagnostics: Mapping[str, Any], at: datetime) -> None:
        """Preserve manifest, diagnostics and partial outputs and mark Failed (KRN-007)."""
        if self.state is not ExperimentState.RUNNING:
            raise InvalidTransition(f"only a Running experiment can fail, not {self.state}")
        self._move(ExperimentState.FAILED)
        if task_id is not None and task_id in self.tasks:
            t = self.tasks[task_id]
            self.tasks[task_id] = TaskRecord(t.task_id, t.task_kind, t.depends_on, t.policy, "failed")
        self.failure = {"task_id": task_id, "reason": reason, "diagnostics": dict(diagnostics)}
        self.finished_at = at
        self._record(ev.ExperimentFailed(self.experiment_id, at, {"task_id": task_id, "reason": reason}))

    def validate(self, report: ValidationReport, at: datetime) -> None:
        """Persist the report and issue the verdict (KRN-008)."""
        if report.experiment_id != self.experiment_id:
            raise InvariantViolation("report belongs to another experiment")
        if report.predeclaration_hash != self.definition.predeclaration_hash:
            raise InvariantViolation("report was evaluated against a different predeclaration")
        self._move(ExperimentState.VALIDATED)
        self.report = report
        self._record(
            ev.VerdictIssued(
                self.experiment_id,
                at,
                {
                    "status": report.verdict.status.value,
                    "gates": {c.gate_id: c.outcome.value for c in report.checks},
                },
            )
        )

    def approve(self, *, ledger_entry_id: str, reviewer: str, at: datetime) -> None:
        if self.report is None:
            raise InvalidTransition("nothing to approve before validation")
        self._move(ExperimentState.APPROVED)
        approval = {"ledger_entry_id": ledger_entry_id, "reviewer": reviewer, "recorded_at": at.isoformat()}
        self.approvals.append(approval)
        self._record(ev.ApprovalRecorded(self.experiment_id, at, approval))

    def publish(self, *, doi: str, bundle: ArtifactId, at: datetime) -> None:
        """Only a Validated experiment with a signed approval reaches Published."""
        if not self.approvals:
            raise InvalidTransition("publication needs a signed approval")
        self._move(ExperimentState.PUBLISHED)
        self.bundle_doi = doi
        self.outputs["bundle"] = bundle
        self._record(ev.BundlePublished(self.experiment_id, at, {"doi": doi, "bundle": bundle.value}))

    @property
    def verdict_status(self) -> VerdictStatus | None:
        return self.report.verdict.status if self.report else None
