"""DefineExperiment, RunExperiment (with the kernel-owned checkpointing runner) and ValidateExperiment."""

from __future__ import annotations

import logging
import resource
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sdp.kernel.application import codec
from sdp.kernel.application.manifest import build_manifest
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.domain.errors import (
    InvariantViolation,
    PlanInvalid,
    PredeclarationNotFrozen,
    VerdictInconsistent,
)
from sdp.kernel.domain.experiment import (
    Experiment,
    ExperimentDefinition,
    ExperimentState,
    InputBinding,
    TaskRecord,
)
from sdp.kernel.domain.gates import evaluate_gates
from sdp.kernel.domain.ids import ArtifactId, ProtocolId
from sdp.kernel.domain.provenance import ProvGraph
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.domain.verdict import ValidationReport
from sdp.kernel.ports.compute import ComputeExecutor, EnvironmentProbe, TaskFailed
from sdp.kernel.ports.determinism import Clock, IdGenerator
from sdp.kernel.ports.driving import DefineExperimentCommand
from sdp.kernel.ports.evidence import ExperimentTracker
from sdp.kernel.ports.ledger import Ledger
from sdp.kernel.ports.plugins import ProtocolRegistry
from sdp.kernel.ports.repositories import UnitOfWork
from sdp.kernel.ports.storage import ArtifactStore
from sdp.kernel.science.io import ComputeResults, ResolvedInput, ResolvedInputs
from sdp.kernel.science.plan import ComputeTask, validate_plan

log = logging.getLogger("sdp.kernel.runner")

KERNEL_AGENT = "kernel"


def manifest_path(experiment: Experiment) -> str:
    return f"experiments/{experiment.definition.protocol.name}/{experiment.experiment_id}/manifest.json"


@dataclass
class ManifestWriter:
    """Writes the manifest atomically to the artifact store and mirrors it in the repository."""

    store: ArtifactStore

    def write(self, uow: UnitOfWork, experiment: Experiment) -> dict[str, Any]:
        frozen = uow.predeclarations.get_frozen(experiment.definition.predeclaration_hash)
        sources: dict[str, SourceRef] = {}
        for binding in experiment.definition.inputs:
            artifact = uow.datasets.get(binding.artifact_id)
            sources[binding.role] = (
                artifact.source_ref
                if artifact
                else SourceRef(SourceKind.LOCAL, f"artifact {binding.artifact_id}", "unknown", "unregistered artifact")
            )
        provenance = uow.evidence.subgraph(f"experiment/{experiment.experiment_id}")
        media = {name: (codec.MEDIA_TYPE if name != "bundle" else "application/zip") for name in experiment.outputs}
        manifest = build_manifest(experiment, frozen, sources, provenance, media)
        manifest_id = self.store.put(canonical_json(manifest).encode("utf-8"), JSON)
        self.store.link(manifest_path(experiment), manifest_id)
        experiment.manifest_id = manifest_id
        uow.experiments.save(experiment)
        uow.experiments.save_manifest(experiment.experiment_id, manifest)
        return manifest


def _persist(uow: UnitOfWork, experiment: Experiment, writer: ManifestWriter | None = None) -> None:
    uow.experiments.save(experiment)
    uow.events.append(experiment.pull_events())
    if writer is not None:
        writer.write(uow, experiment)


@dataclass
class DefineExperimentUseCase:
    uow: Callable[[], UnitOfWork]
    protocols: ProtocolRegistry
    store: ArtifactStore
    clock: Clock
    ids: IdGenerator
    environment: EnvironmentProbe
    manifests: ManifestWriter

    def execute(self, command: DefineExperimentCommand) -> Experiment:
        protocol = self.protocols.get(command.protocol_name, command.protocol_version)
        specs = {s.role: s for s in protocol.required_inputs()}
        missing = sorted(r for r, s in specs.items() if s.required and r not in command.inputs)
        if missing:
            raise InvariantViolation(f"experiment is missing inputs {missing}")
        unknown = sorted(set(command.inputs) - set(specs))
        if unknown:
            raise InvariantViolation(f"protocol {protocol.protocol_id} does not take inputs {unknown}")
        bindings = []
        for role, artifact in sorted(command.inputs.items()):
            if not self.store.exists(artifact):
                raise InvariantViolation(f"input {role} names an artifact the store does not hold: {artifact}")
            media_type = self.store.media_type(artifact)
            if media_type != specs[role].media_type:
                raise InvariantViolation(f"input {role} is {media_type}, protocol expects {specs[role].media_type}")
            bindings.append(InputBinding(role, artifact, media_type))
        definition = ExperimentDefinition(
            protocol=ProtocolId(protocol.protocol_id.name, protocol.protocol_id.version),
            predeclaration_hash=command.predeclaration_hash,
            inputs=tuple(bindings),
            parameters=dict(command.parameters),
            seed=command.seed,
            environment=self.environment.record(command.backend),
        )
        experiment = Experiment.define(self.ids.new("exp"), definition, self.clock.now())
        with self.uow() as uow:
            frozen = uow.predeclarations.get_frozen(command.predeclaration_hash)
            if frozen is not None and (
                frozen.predeclaration.protocol_id != protocol.protocol_id.name
                or frozen.predeclaration.protocol_version != protocol.protocol_id.version
            ):
                raise InvariantViolation("the predeclaration was frozen for a different protocol version")
            graph = ProvGraph()
            activity = graph.activity(
                f"experiment/{experiment.experiment_id}", type="Experiment", protocol=str(definition.protocol)
            )
            graph.agent(KERNEL_AGENT, **{"prov:type": "prov:SoftwareAgent"})
            graph.agent(f"protocol/{definition.protocol}", **{"prov:type": "prov:SoftwareAgent"})
            graph.relate("wasAssociatedWith", activity, KERNEL_AGENT)
            graph.relate("wasAssociatedWith", activity, f"protocol/{definition.protocol}")
            graph.entity(command.predeclaration_hash, type="Predeclaration")
            graph.relate("used", activity, command.predeclaration_hash)
            for b in bindings:
                graph.entity(b.artifact_id.value, role=b.role)
                graph.relate("used", activity, b.artifact_id.value)
            uow.evidence.record(graph)
            _persist(uow, experiment, self.manifests)
            uow.commit()
        return experiment


class _RunnerSink:
    """The kernel-owned checkpoint sink: each completed task becomes an artifact and an event."""

    def __init__(
        self, experiment: Experiment, uow_factory: Callable[[], UnitOfWork], store: ArtifactStore, clock: Clock
    ) -> None:
        self.experiment = experiment
        self.uow_factory = uow_factory
        self.store = store
        self.clock = clock
        self.executed: list[str] = []

    def completed(self, task_id: str) -> tuple[bool, Any]:
        artifact = self.experiment.completed_checkpoints().get(task_id)
        if artifact is None:
            return False, None
        return True, codec.loads(self.store.get(artifact))

    def write(self, task: ComputeTask, value: Any, duration_s: float, resources: Mapping[str, float]) -> None:
        artifact = self.store.put(codec.dumps(value), codec.MEDIA_TYPE)
        self.experiment.checkpoint(task.task_id, artifact, duration_s, self.clock.now())
        self.executed.append(task.task_id)
        log.info(
            "task completed",
            extra={
                "experiment_id": self.experiment.experiment_id,
                "task_id": task.task_id,
                "duration_s": duration_s,
                **{f"resource_{k}": v for k, v in resources.items()},
            },
        )
        with self.uow_factory() as uow:
            graph = ProvGraph()
            graph.entity(artifact.value, type="Checkpoint", task_id=task.task_id)
            graph.relate("wasGeneratedBy", artifact.value, f"experiment/{self.experiment.experiment_id}")
            uow.evidence.record(graph)
            _persist(uow, self.experiment)
            uow.commit()


@dataclass
class RunExperimentUseCase:
    uow: Callable[[], UnitOfWork]
    ledger: Ledger
    protocols: ProtocolRegistry
    store: ArtifactStore
    executor: ComputeExecutor
    clock: Clock
    manifests: ManifestWriter
    tracker: ExperimentTracker
    services: Mapping[str, Any] = field(default_factory=dict)

    def execute(self, experiment_id: str) -> Experiment:
        """Run or resume. Refuses without a ledger entry (KRN-003); checkpoints every task (KRN-005);
        resumes without re-executing completed tasks (KRN-006); preserves failures (KRN-007)."""
        with self.uow() as uow:
            experiment = uow.experiments.get(experiment_id)
            if experiment is None:
                raise KeyError(experiment_id)
            frozen = uow.predeclarations.get_frozen(experiment.definition.predeclaration_hash)
            uow.commit()
        entry = self.ledger.find(experiment.definition.predeclaration_hash, "predeclaration")
        try:
            experiment.start(ledger_entry_id=entry.entry_id if entry and frozen else None, at=self.clock.now())
        except PredeclarationNotFrozen:
            with self.uow() as uow:
                _persist(uow, experiment, self.manifests)
                uow.commit()
            raise
        with self.uow() as uow:
            _persist(uow, experiment)
            uow.commit()

        definition = experiment.definition
        protocol = self.protocols.get(definition.protocol.name, definition.protocol.version)
        self.tracker.log_params(experiment_id, {"seed": definition.seed, **definition.parameters})
        try:
            inputs = self._resolve_inputs(experiment)
            plan = protocol.plan(inputs, definition.parameters, definition.seed)
            registry = protocol.task_kinds()
            validate_plan(plan, registry)
        except (PlanInvalid, InvariantViolation) as exc:
            return self._fail(experiment, None, exc, {})
        experiment.plan_tasks(
            [TaskRecord(t.task_id, t.kind, t.depends_on, t.policy.to_document()) for t in plan.topological_order()]
        )
        with self.uow() as uow:
            _persist(uow, experiment)
            uow.commit()

        sink = _RunnerSink(experiment, self.uow, self.store, self.clock)
        started = time.perf_counter()
        try:
            values = self.executor.execute(
                plan, registry, seed=definition.seed, sink=sink, services={"inputs": inputs, **self.services}
            )
        except TaskFailed as failure:
            return self._fail(experiment, failure.task_id, failure.cause, failure.diagnostics)
        wanted = plan.outputs or tuple(t.task_id for t in plan.tasks)
        results = ComputeResults({k: values[k] for k in wanted})
        try:
            findings = protocol.interpret(results)
        except Exception as exc:
            return self._fail(experiment, None, exc, {"stage": "interpret"})
        outputs = {f"result/{k}": experiment.completed_checkpoints()[k] for k in wanted}
        findings_id = self.store.put(codec.dumps({f.name: f.to_document() for f in findings}), codec.MEDIA_TYPE)
        outputs["findings"] = findings_id
        experiment.complete(findings, outputs, self.clock.now())
        with self.uow() as uow:
            graph = ProvGraph()
            graph.entity(findings_id.value, type="Findings")
            graph.relate("wasGeneratedBy", findings_id.value, f"experiment/{experiment_id}")
            uow.evidence.record(graph)
            _persist(uow, experiment, self.manifests)
            uow.commit()
        numeric = {
            f.name: float(f.value)
            for f in findings
            if isinstance(f.value, int | float) and not isinstance(f.value, bool)
        }
        self.tracker.log_metrics(experiment_id, numeric)
        log.info(
            "experiment completed",
            extra={
                "experiment_id": experiment_id,
                "duration_s": time.perf_counter() - started,
                "executed_tasks": len(sink.executed),
                "resource_maxrss_kb": float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            },
        )
        return experiment

    def _resolve_inputs(self, experiment: Experiment) -> ResolvedInputs:
        items = {}
        with self.uow() as uow:
            for binding in experiment.definition.inputs:
                artifact = uow.datasets.get(binding.artifact_id)
                source = (
                    artifact.source_ref
                    if artifact
                    else SourceRef(
                        SourceKind.LOCAL, f"artifact {binding.artifact_id}", "unknown", "unregistered artifact"
                    )
                )
                data = decode_artifact(self.store.get(binding.artifact_id), binding.media_type)
                items[binding.role] = ResolvedInput(binding.role, binding.artifact_id, binding.media_type, source, data)
            uow.commit()
        return ResolvedInputs(items)

    def _fail(
        self, experiment: Experiment, task_id: str | None, cause: BaseException, diagnostics: Mapping[str, Any]
    ) -> Experiment:
        reason = f"{type(cause).__name__}: {cause}"
        log.error(
            "experiment failed",
            extra={"experiment_id": experiment.experiment_id, "task_id": task_id, "failure_reason": reason},
        )
        experiment.fail(task_id=task_id, reason=reason, diagnostics=diagnostics, at=self.clock.now())
        with self.uow() as uow:
            _persist(uow, experiment, self.manifests)
            uow.commit()
        return experiment


@dataclass
class ValidateExperimentUseCase:
    uow: Callable[[], UnitOfWork]
    protocols: ProtocolRegistry
    clock: Clock
    manifests: ManifestWriter
    tracker: ExperimentTracker

    def execute(self, experiment_id: str) -> ValidationReport:
        """Call validate(), check it against the kernel's own gate evaluation, persist, issue the
        verdict carrying every gate outcome (KRN-008, KRN-009)."""
        with self.uow() as uow:
            experiment = uow.experiments.get(experiment_id)
            if experiment is None:
                raise KeyError(experiment_id)
            if experiment.state is not ExperimentState.COMPLETED or experiment.findings is None:
                raise InvariantViolation(f"{experiment_id} is {experiment.state}; only completed experiments validate")
            frozen = uow.predeclarations.get_frozen(experiment.definition.predeclaration_hash)
            if frozen is None:
                raise PredeclarationNotFrozen(experiment.definition.predeclaration_hash)
            protocol = self.protocols.get(experiment.definition.protocol.name, experiment.definition.protocol.version)
            report = protocol.validate(experiment.findings, frozen.predeclaration, experiment_id=experiment_id)
            expected = evaluate_gates(frozen.predeclaration.gates, experiment.findings)
            if tuple(report.checks) != expected:
                raise VerdictInconsistent(
                    f"{protocol.protocol_id} reported checks that differ from the kernel's evaluation of the frozen gates"
                )
            experiment.validate(report, self.clock.now())
            graph = ProvGraph()
            report_entity = graph.entity(
                f"experiment/{experiment_id}/report", type="ValidationReport", verdict=report.verdict.status.value
            )
            graph.relate("wasGeneratedBy", report_entity, f"experiment/{experiment_id}")
            uow.evidence.record(graph)
            _persist(uow, experiment, self.manifests)
            uow.commit()
        self.tracker.set_tags(experiment_id, {"verdict": report.verdict.status.value})
        return report


def load_json_artifact(store: ArtifactStore, artifact: ArtifactId) -> Any:
    return decode_artifact(store.get(artifact), JSON)
