"""The ``sdp`` command line (ADR-016): commands that change evidence, each signed through the ledger.

Every command prints JSON on stdout so scripts and the harness can read it. The application is
built lazily from the environment (``sdp.adapters.settings``); tests swap ``runtime_factory`` for
an in-memory kernel.
"""

from __future__ import annotations

import json
import mimetypes
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Protocol

import typer

from sdp.kernel.application.investigate import ROW_CAP_REACHED, STORED_BYTES, QueryInvestigationsUseCase
from sdp.kernel.application.kernel import Kernel
from sdp.kernel.application.planning import DEFAULT_BUDGET, QueryPlansUseCase, plan_view
from sdp.kernel.domain.errors import DomainError
from sdp.kernel.domain.investigation import (
    STAGES,
    Field,
    Investigation,
    InvestigationSet,
    TestProfile,
    parse_region,
)
from sdp.kernel.ports.driving import (
    ApproveVerdictCommand,
    AssembleDossierCommand,
    DefineExperimentCommand,
    FreezePredeclarationCommand,
)
from sdp.kernel.ports.integration import (
    ApprovePlanCommand,
    ExecutePlanCommand,
    PlanInvestigationCommand,
    PlanRecord,
)
from sdp.kernel.ports.investigate import InvestigateFieldCommand, TriageCommand
from sdp.kernel.ports.ledger import Ledger
from sdp.kernel.ports.storage import ArtifactStore
from sdp.kernel.science.plan import Backend


class Runtime(Protocol):
    @property
    def kernel(self) -> Kernel: ...

    @property
    def store(self) -> ArtifactStore: ...

    @property
    def ledger(self) -> Ledger: ...


def _production_runtime() -> Runtime:
    from sdp.adapters.bootstrap import build_application
    from sdp.adapters.settings import Settings

    return build_application(Settings.from_env())


@dataclass
class _State:
    factory: Callable[[], Runtime] = _production_runtime
    _runtime: Runtime | None = None

    def runtime(self) -> Runtime:
        if self._runtime is None:
            self._runtime = self.factory()
        return self._runtime


state = _State()


def use_runtime(factory: Callable[[], Runtime]) -> None:
    """Point the CLI at another composition root (tests, notebooks)."""
    state.factory, state._runtime = factory, None


app = typer.Typer(
    help="Scientific discovery platform: predeclare, run, validate, approve, publish.", no_args_is_help=True
)
predeclaration_app = typer.Typer(help="Freeze predeclarations into the ledger.", no_args_is_help=True)
experiment_app = typer.Typer(help="Define, run, validate and inspect experiments.", no_args_is_help=True)
dossier_app = typer.Typer(help="Assemble dossiers for review.", no_args_is_help=True)
ledger_app = typer.Typer(help="Inspect and verify the signed ledger.", no_args_is_help=True)
db_app = typer.Typer(help="Database schema migrations (NFR-007).", no_args_is_help=True)
artifact_app = typer.Typer(help="Put and fetch content-addressed artifacts.", no_args_is_help=True)
telemetry_app = typer.Typer(help="Prometheus textfile telemetry (NFR-010).", no_args_is_help=True)
archive_app = typer.Typer(help="Archive sources, acquisition and the live schema smoke (§8).", no_args_is_help=True)
integration_app = typer.Typer(
    help="Integration v0.4.0: live recordings of capability descriptors and reference products.",
    no_args_is_help=True,
)
plan_app = typer.Typer(
    help="Plan, approve and execute acquisitions (Integration v0.4.0 §5, ADR-020). Approval above the "
    "budget threshold is a signed ledger entry.",
    no_args_is_help=True,
)
demo_app = typer.Typer(
    help="Guided end-to-end runs on real archives, for learning SDP. Every step is the same one the "
    "plan and investigate commands take, and every result is EXPLORATORY.",
    no_args_is_help=True,
)
investigate_app = typer.Typer(
    help="Investigate a field: identify, retrieve, test, candidates (Investigate §1). Results are EXPLORATORY.",
    invoke_without_command=True,
)
for name, sub in (
    ("predeclaration", predeclaration_app),
    ("experiment", experiment_app),
    ("dossier", dossier_app),
    ("ledger", ledger_app),
    ("db", db_app),
    ("artifact", artifact_app),
    ("telemetry", telemetry_app),
    ("archive", archive_app),
    ("investigate", investigate_app),
    ("plan", plan_app),
    ("integration", integration_app),
    ("demo", demo_app),
):
    app.add_typer(sub, name=name)


def emit(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))


def _pair(text: str, what: str) -> tuple[str, str]:
    if "=" not in text:
        raise typer.BadParameter(f"expected NAME=VALUE for {what}, got {text!r}")
    key, value = text.split("=", 1)
    return key.strip(), value


def _parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# --- predeclarations ----------------------------------------------------------------------------


@predeclaration_app.command("freeze")
def freeze(
    protocol: Annotated[str, typer.Argument(help="Protocol name, as registered under sdp.protocols")],
    signer: Annotated[str, typer.Option(help="Principal in allowed_signers")],
    version: Annotated[str | None, typer.Option(help="Protocol version; latest when omitted")] = None,
) -> None:
    """Freeze the protocol's predeclaration and record it in the ledger (KRN-002)."""
    runtime = state.runtime()
    module = runtime.kernel.ports.protocols.get(protocol, version)
    frozen, entry = runtime.kernel.freeze_predeclaration.execute(
        FreezePredeclarationCommand(module.predeclare(), signer)
    )
    emit({"predeclaration_hash": frozen.hash, "ledger_entry": entry.to_document()})


# --- experiments --------------------------------------------------------------------------------


@experiment_app.command("define")
def define(
    protocol: Annotated[str, typer.Argument()],
    predeclaration: Annotated[str, typer.Option(help="Hash of a frozen predeclaration")],
    inputs: Annotated[list[str], typer.Option("--input", help="NAME=PATH, or NAME=sha256:<hex> for a stored artifact")],
    params: Annotated[list[str] | None, typer.Option("--param", help="NAME=VALUE; VALUE is parsed as JSON")] = None,
    seed: Annotated[int, typer.Option()] = 0,
    backend: Annotated[Backend, typer.Option()] = Backend.CPU,
    version: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Define an experiment against a frozen predeclaration, storing file inputs first."""
    from sdp.kernel.domain.ids import ArtifactId

    runtime = state.runtime()
    bound: dict[str, ArtifactId] = {}
    for item in inputs:
        name, value = _pair(item, "--input")
        if value.startswith("sha256:"):
            bound[name] = ArtifactId(value)
        else:
            path = Path(value)
            media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            bound[name] = runtime.store.put(path.read_bytes(), media)
    parameters = dict(_pair(p, "--param") for p in params or [])
    experiment = runtime.kernel.define_experiment.execute(
        DefineExperimentCommand(
            protocol,
            predeclaration,
            bound,
            {k: _parse_value(v) for k, v in parameters.items()},
            seed=seed,
            backend=backend,
            protocol_version=version,
        )
    )
    emit({"experiment_id": experiment.experiment_id, "state": experiment.state.value})


@experiment_app.command("run")
def run(experiment_id: str) -> None:
    """Run (or resume) an experiment."""
    experiment = state.runtime().kernel.run_experiment.execute(experiment_id)
    emit({"experiment_id": experiment.experiment_id, "state": experiment.state.value})


@experiment_app.command("validate")
def validate(experiment_id: str) -> None:
    """Evaluate the predeclared gates and issue a verdict."""
    report = state.runtime().kernel.validate_experiment.execute(experiment_id)
    emit(
        {
            "experiment_id": experiment_id,
            "verdict": report.verdict.status.value,
            "checks": [{"gate_id": c.gate_id, "outcome": c.outcome.value} for c in report.checks],
        }
    )


@experiment_app.command("show")
def show(experiment_id: str) -> None:
    """Print the experiment's evidence manifest."""
    manifest = state.runtime().kernel.query_evidence.manifest(experiment_id)
    if manifest is None:
        raise DomainError(f"no manifest for {experiment_id}")
    emit(manifest)


@experiment_app.command("list")
def list_experiments() -> None:
    emit(
        [
            {"experiment_id": e.experiment_id, "state": e.state.value}
            for e in state.runtime().kernel.query_evidence.experiments()
        ]
    )


# --- dossiers, approval, publication ------------------------------------------------------------


@dossier_app.command("assemble")
def assemble(
    experiment_id: str,
    limitations: Annotated[list[str], typer.Option("--limitation", help="A stated limitation; at least one")],
) -> None:
    """Assemble a dossier for a validated experiment."""
    dossier = state.runtime().kernel.assemble_dossier.execute(AssembleDossierCommand(experiment_id, tuple(limitations)))
    emit({"dossier_id": dossier.dossier_id, "experiment_id": experiment_id})


@app.command("approve")
def approve(experiment_id: str, reviewer: Annotated[str, typer.Option(help="Principal in allowed_signers")]) -> None:
    """Record a reviewer's signed approval of the verdict (ADR-004)."""
    entry = state.runtime().kernel.publish_bundle.approve(ApproveVerdictCommand(experiment_id, reviewer))
    emit(entry.to_document())


@app.command("bundle")
def bundle(dossier_id: str, out: Annotated[Path, typer.Option(help="Where to write the .zip")]) -> None:
    """Build the RO-Crate bundle for a dossier without depositing it (for review and `sdp reproduce`)."""
    from sdp.kernel.application.rocrate import verify_crate

    data = state.runtime().kernel.publish_bundle.build_bundle(dossier_id)
    out.write_bytes(data)
    emit({"bundle": str(out), "bytes": len(data), "problems": verify_crate(data)})


@app.command("publish")
def publish(dossier_id: str) -> None:
    """Build the RO-Crate bundle for a dossier and deposit it for a DOI."""
    record = state.runtime().kernel.publish_bundle.publish(dossier_id)
    emit({"doi": record.doi, "url": record.url, "deposition_id": record.deposition_id})


@app.command("reproduce")
def reproduce(
    evidence: Annotated[Path, typer.Argument(help="RO-Crate bundle (.zip) or manifest.json")],
    inputs: Annotated[Path, typer.Option(help="Directory holding the input artifacts, found by checksum")],
    out: Annotated[Path, typer.Option()] = Path("reports/reproduction"),
) -> None:
    """Re-run a recorded experiment from its evidence and inputs and compare (T9, NFR-004); exits 1 on a difference."""
    from sdp.adapters.bootstrap import clusterers
    from sdp.testing.reproduce import ReproductionError, index_inputs, load_manifest, write_outputs
    from sdp.testing.reproduce import reproduce as rerun

    try:
        result = rerun(load_manifest(evidence), index_inputs(inputs), services={"clusterers": clusterers()})
    except ReproductionError as exc:
        typer.echo(f"FAIL {exc}", err=True)
        raise typer.Exit(2) from exc
    write_outputs(result, out)
    for difference in result.differences:
        typer.echo(f"FAIL {difference}", err=True)
    emit(result.to_document())
    if not result.ok:
        raise typer.Exit(1)


@app.command("events")
def events(after: int = 0, limit: Annotated[int | None, typer.Option()] = None) -> None:
    """Domain events after a sequence number."""
    emit(
        [
            {"sequence": s.sequence, "name": s.event.name, "aggregate_id": s.event.aggregate_id}
            for s in state.runtime().kernel.query_evidence.events(after, limit)
        ]
    )


# --- ledger -------------------------------------------------------------------------------------


@ledger_app.command("verify")
def ledger_verify() -> None:
    """Check the hash chain and every signature; exits 1 on any problem."""
    ledger = state.runtime().ledger
    problems = ledger.verify()
    emit({"entries": len(ledger.entries()), "problems": problems})
    if problems:
        raise typer.Exit(1)


@ledger_app.command("list")
def ledger_list() -> None:
    emit([e.to_document() for e in state.runtime().ledger.entries()])


# --- artifacts ----------------------------------------------------------------------------------


@artifact_app.command("put")
def artifact_put(
    path: Path,
    media_type: Annotated[str | None, typer.Option()] = None,
    logical_path: Annotated[str | None, typer.Option()] = None,
) -> None:
    media = media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    artifact = state.runtime().store.put(path.read_bytes(), media, logical_path=logical_path)
    emit({"artifact_id": artifact.value, "media_type": media})


@artifact_app.command("usage")
def artifact_usage() -> None:
    """The artifact store's size: objects and bytes in all and per media type, and the free space the
    acquisition guard sees against SDP_MIN_FREE_BYTES (DSI-010). Needs only SDP_ARTIFACT_ROOT."""
    import os

    from sdp.adapters.bootstrap import store_usage

    root = os.environ.get("SDP_ARTIFACT_ROOT")
    if not root:
        raise DomainError("SDP_ARTIFACT_ROOT is not set: it names the artifact store to measure")
    emit(store_usage(Path(root), int(os.environ.get("SDP_MIN_FREE_BYTES", str(50 * 1024**3)))))


@artifact_app.command("get")
def artifact_get(artifact_id: str, out: Annotated[Path | None, typer.Option()] = None) -> None:
    from sdp.kernel.domain.ids import ArtifactId

    data = state.runtime().store.get(ArtifactId(artifact_id))
    if out is None:
        sys.stdout.buffer.write(data)
    else:
        out.write_bytes(data)


# --- investigate ------------------------------------------------------------------------------


def load_profile(path: Path) -> TestProfile:
    """A profile is a versioned YAML (or JSON) document; its hash is recorded before anything runs."""
    import yaml

    document = yaml.safe_load(path.read_text())
    if not isinstance(document, dict):
        raise ValueError(f"{path} is not a profile document")
    return TestProfile.from_document(document)


def _set_summary(s: InvestigationSet) -> dict[str, Any]:
    return {
        "set_id": s.set_id,
        "field_id": s.field.field_id,
        "profile": f"{s.profile.profile_id}@{s.profile.version}",
        "profile_hash": s.profile_hash,
        "stages": {stage.value: record.state.value for stage, record in s.stages.items()},
        "objects_examined": len(s.objects_examined),
        "investigations": len(s.investigation_ids),
        "manifest_id": s.manifest_id.value if s.manifest_id else None,
        "label": s.label,
        "row_cap_reached": row_cap_reached(s),
        "timing": stage_timing(s),
    }


def stage_timing(s: InvestigationSet) -> dict[str, dict[str, Any]]:
    """Seconds and stored bytes per finished stage (#60). Stages run one after another, so a stage took
    from the end of the one before (or the set's start) to its own end; on a resumed set the first
    stage run again also counts the time between the two runs."""
    timing: dict[str, dict[str, Any]] = {}
    previous = s.started_at
    for stage in STAGES:
        record = s.stages.get(stage)
        if record is None or record.finished_at is None:
            continue
        timing[stage.value] = {
            "state": record.state.value,
            "seconds": round((record.finished_at - previous).total_seconds(), 3),
            "stored_bytes": record.summary.get(STORED_BYTES),
        }
        previous = record.finished_at
    return timing


def row_cap_reached(s: InvestigationSet) -> dict[str, int]:
    """Every source whose read filled its row cap in any stage, with the cap (#59)."""
    reached: dict[str, int] = {}
    for record in s.stages.values():
        reached.update(record.summary.get(ROW_CAP_REACHED, {}))
    return reached


def warn_row_caps(reached: dict[str, int]) -> None:
    for source, cap in sorted(reached.items()):
        typer.echo(
            f"warning: {source} had at least {cap:,} rows here, so only part of the field was read; "
            "choose a smaller region or raise row_cap",
            err=True,
        )


def _investigation_summary(i: Investigation) -> dict[str, Any]:
    return {
        "investigation_id": i.investigation_id,
        "candidate": i.candidate.key,
        "score": i.rank.score,
        "state": i.state.value,
        "label": i.label,
    }


@investigate_app.callback()
def investigate(
    ctx: typer.Context,
    region: Annotated[str | None, typer.Option(help='DS9 region, e.g. "icrs;circle(120.0,-10.0,0.5)"')] = None,
    profile: Annotated[Path | None, typer.Option(help="Test profile (YAML)")] = None,
    epoch: Annotated[float, typer.Option(help="Field epoch, Julian year")] = 2016.0,
    label: Annotated[str, typer.Option()] = "",
    seed: Annotated[int, typer.Option()] = 0,
    actor: Annotated[str, typer.Option()] = "cli",
    resume: Annotated[str | None, typer.Option(help="Resume this set: completed stages are read back")] = None,
) -> None:
    """Run InvestigateField over a region under a profile, or use a subcommand to query and triage."""
    if ctx.invoked_subcommand is not None:
        return
    if region is None or profile is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(2)
    use_case = state.runtime().kernel.investigate_field
    if use_case is None:
        raise DomainError("Investigate is not wired: no observation archive or cross-match adapters are configured")
    run = use_case.execute(
        InvestigateFieldCommand(
            Field(parse_region(region), epoch_jyear=epoch, label=label),
            load_profile(profile),
            seed=seed,
            actor=actor,
            set_id=resume,
        )
    )
    emit(
        {
            **_set_summary(run.investigation_set),
            "candidates": [_investigation_summary(i) for i in run.investigations],
        }
    )
    warn_row_caps(row_cap_reached(run.investigation_set))


def _query() -> QueryInvestigationsUseCase:
    query = state.runtime().kernel.query_investigations
    if query is None:
        raise DomainError("investigation queries are not wired")
    return query


@investigate_app.command("sets")
def investigate_sets() -> None:
    """Every investigation set, oldest first."""
    emit([_set_summary(s) for s in _query().sets()])


@investigate_app.command("show")
def investigate_show(set_id: str) -> None:
    """The set's manifest: field, profile hash, tests, multiplicity, nulls and archive releases."""
    manifest = _query().manifest(set_id)
    if manifest is None:
        raise DomainError(f"no manifest for {set_id}")
    emit(manifest)


@investigate_app.command("list")
def investigate_list(set_id: str) -> None:
    """The set's investigations, highest score first."""
    found = sorted(_query().investigations(set_id), key=lambda i: -i.rank.score)
    emit([_investigation_summary(i) for i in found])


@investigate_app.command("dossier")
def investigate_dossier(investigation_id: str) -> None:
    """The candidate's dossier: rows by source, observation index, results with null comparisons."""
    dossier = _query().dossier(investigation_id)
    if dossier is None:
        raise DomainError(f"no dossier for {investigation_id}")
    emit(dossier)


def _triage(command: TriageCommand) -> None:
    use_case = state.runtime().kernel.triage_investigation
    if use_case is None:
        raise DomainError("triage is not wired")
    investigation = use_case.execute(command)
    emit({**_investigation_summary(investigation), "transitions": len(investigation.transitions)})


Actor = Annotated[str, typer.Option(help="Who is triaging")]
Reason = Annotated[str, typer.Option(help="Why; recorded with the transition (required)")]


@investigate_app.command("park")
def investigate_park(investigation_id: str, actor: Actor, reason: Reason) -> None:
    _triage(TriageCommand(investigation_id, "park", actor, reason))


@investigate_app.command("reopen")
def investigate_reopen(investigation_id: str, actor: Actor, reason: Reason) -> None:
    _triage(TriageCommand(investigation_id, "reopen", actor, reason))


@investigate_app.command("close")
def investigate_close(investigation_id: str, actor: Actor, reason: Reason) -> None:
    _triage(TriageCommand(investigation_id, "close", actor, reason))


@investigate_app.command("annotate")
def investigate_annotate(investigation_id: str, actor: Actor, note: Annotated[str, typer.Option()]) -> None:
    _triage(TriageCommand(investigation_id, "annotate", actor, note))


@investigate_app.command("promote")
def investigate_promote(
    investigation_id: str,
    actor: Actor,
    predeclaration: Annotated[str, typer.Option(help="Hash of a frozen confirmatory predeclaration")],
    experiment: Annotated[str | None, typer.Option(help="The confirmatory experiment, if defined")] = None,
    reason: Reason = "",
) -> None:
    """Promote to a confirmatory experiment (INV-009): refused unless the frozen predeclaration names
    evidence this investigation did not use. A refusal is recorded and exits 1."""
    _triage(TriageCommand(investigation_id, "promote", actor, reason, predeclaration, experiment))


# --- plans ------------------------------------------------------------------------------------


def _plan_summary(record: PlanRecord) -> dict[str, Any]:
    plan = record.plan
    return {
        "plan_id": plan.plan_id,
        "state": record.state,
        "set_id": record.set_id,
        "items": len(plan.items),
        "gaps": [g.to_document() for g in plan.gaps],
        "total_bytes": plan.cost.total_bytes,
        "approval_threshold_bytes": plan.budget.approval_threshold_bytes,
        "requires_signed_approval": plan.requires_signed_approval,
    }


def _plans() -> QueryPlansUseCase:
    query = state.runtime().kernel.query_plans
    if query is None:
        raise DomainError("plan queries are not wired")
    return query


def _plan_record(plan_id: str) -> PlanRecord:
    record = _plans().plan(plan_id)
    if record is None:
        raise DomainError(f"no plan {plan_id}")
    return record


@plan_app.command("draft")
def plan_draft(
    region: Annotated[str, typer.Option(help='DS9 region, e.g. "icrs;circle(120.0,-10.0,0.5)"')],
    profile: Annotated[Path, typer.Option(help="Test profile (YAML)")],
    epoch: Annotated[float, typer.Option(help="Field epoch, Julian year")] = 2016.0,
    label: Annotated[str, typer.Option()] = "",
    seed: Annotated[int, typer.Option()] = 0,
    actor: Annotated[str, typer.Option()] = "cli",
) -> None:
    """Draft the plan for a field under a profile: what its tests need, from which sources, at what cost.
    Nothing is acquired."""
    runtime = state.runtime()
    use_case = runtime.kernel.plan_investigation
    if use_case is None:
        raise DomainError("the planner is not wired: Investigate and capability discovery are both needed")
    plan = use_case.execute(
        PlanInvestigationCommand(
            Field(parse_region(region), epoch_jyear=epoch, label=label),
            load_profile(profile),
            getattr(runtime, "budget", DEFAULT_BUDGET),
            actor=actor,
            seed=seed,
        )
    )
    emit(_plan_summary(_plan_record(plan.plan_id)))


@plan_app.command("list")
def plan_list() -> None:
    """Every plan with its state."""
    emit([_plan_summary(r) for r in _plans().plans()])


@plan_app.command("show")
def plan_show(plan_id: str) -> None:
    """The plan document: intent, capability matches, items, cost, gaps and approval."""
    emit(plan_view(_plan_record(plan_id)))


@plan_app.command("approve")
def plan_approve(plan_id: str, actor: Annotated[str, typer.Option(help="Principal in allowed_signers")]) -> None:
    """Approve a plan. Below its budget threshold the approval is automatic and recorded; above it,
    this signs a ledger approval of the plan id as ``actor`` first (INT-014)."""
    _approve(plan_id, actor)
    emit(_plan_summary(_plan_record(plan_id)))


def _approve(plan_id: str, actor: str) -> None:
    runtime = state.runtime()
    use_case = runtime.kernel.approve_plan
    assert use_case is not None
    plan = _plan_record(plan_id).plan
    entry_id = None
    if plan.requires_signed_approval and plan.approval is None:
        # A retry after a failure reuses this actor's approval rather than signing the plan again.
        signed = next(
            (
                e
                for e in runtime.ledger.entries()
                if e.kind == "approval" and e.subject_hash == plan_id and e.signer == actor
            ),
            None,
        )
        if signed is None:
            try:
                signed = runtime.ledger.append(
                    kind="approval",
                    subject_id=plan_id,
                    subject_hash=plan_id,
                    signer=actor,
                    payload={
                        "plan_id": plan_id,
                        "total_bytes": plan.cost.total_bytes,
                        "approval_threshold_bytes": plan.budget.approval_threshold_bytes,
                    },
                )
            except RuntimeError as exc:  # the ledger's own refusal, e.g. no signing key configured
                raise DomainError(f"cannot sign the approval of {plan_id}: {exc}") from exc
        entry_id = signed.entry_id
    use_case.execute(ApprovePlanCommand(plan_id, actor, ledger_entry_id=entry_id))


@plan_app.command("refuse")
def plan_refuse(plan_id: str, actor: Actor, reason: Reason) -> None:
    """Refuse a plan; the reason is recorded and the plan can never be executed."""
    use_case = state.runtime().kernel.approve_plan
    assert use_case is not None
    if not reason.strip():
        raise typer.BadParameter("a refusal records why", param_hint="--reason")
    use_case.execute(ApprovePlanCommand(plan_id, actor, refuse_reason=reason))
    emit(_plan_summary(_plan_record(plan_id)))


@plan_app.command("execute")
def plan_execute(plan_id: str, actor: Annotated[str, typer.Option()] = "cli") -> None:
    """Run the approved plan: InvestigateField, with every acquisition checked against the plan (INT-015)."""
    use_case = state.runtime().kernel.execute_plan
    if use_case is None:
        raise DomainError("Investigate is not wired: no observation archive or cross-match adapters are configured")
    execution = use_case.execute(ExecutePlanCommand(plan_id, actor))
    summary = _plan_summary(_plan_record(plan_id))
    executed = _query().set(summary["set_id"]) if summary["set_id"] else None
    reached = row_cap_reached(executed) if executed is not None else {}
    timing = stage_timing(executed) if executed is not None else {}
    emit({**summary, "execution": execution.to_document(), "row_cap_reached": reached, "timing": timing})
    warn_row_caps(reached)


# --- database and telemetry (need only settings) ------------------------------------------------


def _database_url() -> str:
    from sdp.adapters.settings import Settings

    return Settings.from_env().database_url


@db_app.command("migrate")
def db_migrate(to: Annotated[int | None, typer.Option(help="Target version; latest when omitted")] = None) -> None:
    from sdp.adapters.postgres import migrate

    emit({"applied": migrate.upgrade(_database_url(), to)})


@db_app.command("rollback")
def db_rollback(steps: Annotated[int, typer.Option(min=1)] = 1) -> None:
    from sdp.adapters.postgres import migrate

    emit({"reverted": migrate.downgrade(_database_url(), steps)})


@telemetry_app.command("gpu")
def telemetry_gpu(directory: Annotated[Path, typer.Option(envvar="SDP_METRICS_DIR")]) -> None:
    """Write sdp_gpu.prom for node_exporter's textfile collector (run from a timer)."""
    from sdp.adapters.telemetry.prometheus import write_gpu_telemetry

    emit({"written": str(write_gpu_telemetry(directory))})


# --- archives -----------------------------------------------------------------------------------


@archive_app.command("sources")
def archive_sources() -> None:
    """The packaged archive sources: release, table, licence, citation, limits and dialect."""
    from sdp.adapters.archives.tap import load_sources

    emit(
        [
            {
                "source_id": s.descriptor.source_id,
                "release_label": s.descriptor.release_label,
                "service_url": s.service_url,
                "table": s.descriptor.table,
                "licence": s.descriptor.licence,
                "citation": s.descriptor.citation,
                "max_rows_per_call": s.descriptor.max_rows_per_call,
                "dialect_id": s.descriptor.dialect_id,
            }
            for s in load_sources().values()
        ]
    )


@archive_app.command("acquire")
def archive_acquire(
    source: Annotated[str, typer.Option(help="Source id, e.g. gaia")],
    release: Annotated[str, typer.Option(help="Release label, e.g. gaia:DR3")],
    ra: Annotated[float, typer.Option(help="Cone centre RA (deg)")],
    dec: Annotated[float, typer.Option(help="Cone centre Dec (deg)")],
    radius: Annotated[float, typer.Option(help="Cone radius (deg, at most 10)")],
    column: Annotated[list[str], typer.Option("--column", help="Native column; repeat")],
    row_cap: Annotated[int, typer.Option()] = 100_000,
    chunk_rows: Annotated[int, typer.Option()] = 50_000,
    curate: Annotated[bool, typer.Option()] = True,
) -> None:
    """Acquire a bounded cone: raw Parquet parts, then canonical astrometry through the source's dialect."""
    from sdp.kernel.domain.archive import CatalogueQuery, Cone
    from sdp.kernel.ports.driving import AcquireDatasetCommand

    query = CatalogueQuery(
        source, release, Cone(ra, dec, radius), tuple(column), row_cap=row_cap, chunk_rows=chunk_rows
    )
    use_case = state.runtime().kernel.acquire_dataset
    started = time.monotonic()
    result = use_case.execute(AcquireDatasetCommand(query, curate=curate))
    emit(
        {
            "raw": result.raw.artifact_id.value,
            "curated": result.curated.artifact_id.value if result.curated else None,
            "reused": result.reused,
            "rows": result.rows,
            "raw_bytes": result.raw.byte_count,
            "curated_bytes": result.curated.byte_count if result.curated else 0,
            "stored_bytes": result.stored_bytes,
            "requests": result.requests,
            "seconds": round(time.monotonic() - started, 3),
            "row_cap_reached": result.reached_row_cap,
            "truncated": result.reached_row_cap,
            "query": result.raw.source_ref.query_text,
        }
    )
    if result.reached_row_cap:
        warn_row_caps({source: row_cap})


@archive_app.command("smoke")
def archive_smoke(
    baseline: Annotated[Path, typer.Option(help="Committed cassettes to compare against")],
    record: Annotated[Path, typer.Option(help="Where to write the new recordings")],
) -> None:
    """The weekly live smoke (DSI-008): fails naming every added, removed or retyped column."""
    from sdp.adapters.archives.smoke import run_smoke
    from sdp.adapters.archives.tap import load_sources
    from sdp.adapters.archives.transport import PoliteTransport, UrllibTransport

    results = run_smoke(
        load_sources(),
        lambda s: PoliteTransport(UrllibTransport(), min_seconds_between_calls=s.descriptor.min_seconds_between_calls),
        baseline,
        record,
    )
    from sdp.adapters.archives.investigate_smoke import run_investigate_smoke

    results += run_investigate_smoke(
        lambda _: PoliteTransport(UrllibTransport(), min_seconds_between_calls=1.0), record
    )
    emit([r.to_document() for r in results])
    if any(r.problems for r in results):
        raise SystemExit(1)


def _register(mast: bool) -> list[dict[str, Any]]:
    from sdp.adapters.archives.registration import register_defaults
    from sdp.adapters.bootstrap import archive_transport
    from sdp.adapters.postgres.capabilities import PostgresCapabilityRegistry
    from sdp.adapters.settings import Settings

    settings = Settings.from_env()
    registered = register_defaults(
        PostgresCapabilityRegistry(settings.database_url),
        lambda name: archive_transport(settings, name, 1.0),
        mast=mast,
    )
    return [
        {
            "source_id": d.source_id,
            "origin": d.origin,
            "product_types": sorted(t.value for t in d.product_types),
            "operations": sorted(o.value for o in d.operations),
            "adapter": d.adapter.name if d.adapter else None,
            "admitted_for": sorted(t for t, ok in (d.adapter.conformance if d.adapter else {}).items() if ok),
        }
        for d in registered
    ]


@integration_app.command("register")
def integration_register(
    mast: Annotated[bool, typer.Option(help="Also read MAST's descriptor from the VO (live)")] = True,
) -> None:
    """Register the descriptors plans are matched against: every packaged catalogue source, and MAST
    as the VO describes it. Run once on a new database, and again to refresh them."""
    emit(_register(mast))


@integration_app.command("record")
def integration_record(
    obs_id: Annotated[str, typer.Option(help="MAST ObsCore obs_id of a public observation")],
    filename: Annotated[str, typer.Option(help="The product file to record, e.g. jw..._i2d.fits")],
    name: Annotated[str, typer.Option(help="Directory name for the reference product")],
    out: Annotated[Path, typer.Option(help="Reference products root")] = Path("../fixtures/reference-products"),
    cassettes: Annotated[Path, typer.Option(help="Where to keep every exchange")] = Path("../reports/cassettes"),
) -> None:
    """Record MAST's capability descriptor and one product as a to_verify reference product (live)."""
    from sdp.adapters.archives.reference_recording import describe_mast, record_product
    from sdp.adapters.archives.transport import CassetteTransport, PoliteTransport, UrllibTransport

    live = PoliteTransport(UrllibTransport(), min_seconds_between_calls=1.0)
    transport = CassetteTransport(
        cassettes / "integration" / name, "record", inner=live, recorder="integration-recording"
    )
    descriptor = describe_mast(transport, cassettes / "integration")
    recorded = record_product(transport, obs_id, filename, out / name)
    emit(
        {
            "descriptor": {k: descriptor[k] for k in ("ivoid", "product_types", "protocols", "limits")},
            "product": {"directory": str(recorded.directory), "url": recorded.access_url, "bytes": recorded.size_bytes},
        }
    )


#: A patch of the globular cluster M92's outskirts, on one NIRCam detector of public programme 1334, with
#: about 50 cleanly measured Gaia stars. The core is too crowded: Gaia measures almost none there cleanly.
M92_REGION = "icrs;circle(259.323,43.1505,0.004)"
DEMO_PROFILE = "jwst-image-check.yaml"


def _demo_profile(given: Path | None) -> Path:
    """The profile given, else the one in the person's profiles folder, else the repository's."""
    import os

    if given is not None:
        return given
    for folder in (os.environ.get("SDP_PROFILES_DIR"), os.environ.get("SDP_REPO_ROOT")):
        if not folder:
            continue
        for candidate in (Path(folder) / DEMO_PROFILE, Path(folder) / "profiles" / DEMO_PROFILE):
            if candidate.is_file():
                return candidate
    raise DomainError(f"no {DEMO_PROFILE} found; pass --profile with its path")


def _say(text: str) -> None:
    typer.echo(text, err=True)


@demo_app.command("jwst")
def demo_jwst(
    region: Annotated[str, typer.Option(help="The patch of sky, as a DS9 region")] = M92_REGION,
    profile: Annotated[Path | None, typer.Option(help="Test profile; jwst-image-check.yaml by default")] = None,
    label: Annotated[str, typer.Option()] = "M92 outskirts (JWST demo)",
    actor: Annotated[str, typer.Option(help="Who the plan's approval is recorded under")] = "demo",
    register: Annotated[bool, typer.Option(help="Read the sources' descriptors again first")] = False,
) -> None:
    """Gaia against JWST on a real field, live: register sources, plan, approve, execute, summarise.

    Defaults to a patch of the globular cluster M92's outskirts and the jwst-image-check profile,
    which cuts 40-arcsec squares out of JWST's NIRCam mosaics, so a run reads and stores about
    40 MB. It takes 10 to 20 minutes, mostly waiting for MAST. Running it again picks up the same
    plan where it stopped, or shows its results if it already finished.
    """
    from sdp.adapters.archives.transport import TransportError

    try:
        _demo_run(_demo_profile(profile), region, label, actor, register)
    except TransportError as exc:
        # The archives are public services under load; a busy answer is not the run's fault.
        raise DomainError(f"an archive did not answer ({exc}); wait a few minutes and run it again") from exc


def _demo_run(path: Path, region: str, label: str, actor: str, register: bool) -> None:
    from sdp.adapters.postgres.capabilities import PostgresCapabilityRegistry
    from sdp.adapters.settings import Settings

    registry = PostgresCapabilityRegistry(Settings.from_env().database_url)
    if register or registry.get("mast") is None or registry.get("gaia") is None:
        _say("1/4 Registering what each archive can do (MAST is read from the Virtual Observatory, ~5 min)...")
        _register(mast=True)
    else:
        _say("1/4 Archives already registered (pass --register to read them again).")

    runtime = state.runtime()
    kernel = runtime.kernel
    if kernel.plan_investigation is None or kernel.approve_plan is None or kernel.execute_plan is None:
        raise DomainError("the planner is not wired: Investigate and capability discovery are both needed")
    _say(f"2/4 Planning {region} with {path.name}: what to fetch, from where, and how big...")
    plan = kernel.plan_investigation.execute(
        PlanInvestigationCommand(
            Field(parse_region(region), epoch_jyear=2016.0, label=label),
            load_profile(path),
            getattr(runtime, "budget", DEFAULT_BUDGET),
            actor=actor,
            seed=0,
        )
    )
    for item in plan.items:
        _say(f"    {item.source_id:5} {item.operation.value:7} {item.expected_bytes / 1e6:7.1f} MB  {item.target[:90]}")
    for gap in plan.gaps:
        _say(f"    gap: {gap.requirement_id}: {gap.reason}")
    if plan.gaps:
        raise DomainError(f"plan {plan.plan_id} has gaps, so nothing was fetched; see `sdp plan show {plan.plan_id}`")
    # The plan id hashes the plan, so a rerun of the same demo meets the same plan: approve it only
    # once, and execute it only if an earlier attempt did not finish.
    record = _plan_record(plan.plan_id)
    if record.plan.approval is None:
        _say(f"3/4 Approving plan {plan.plan_id[:19]}... ({plan.cost.total_bytes / 1e6:.0f} MB planned)")
        _approve(plan.plan_id, actor)
    elif not record.plan.approval.approves:
        raise DomainError(f"plan {plan.plan_id} was refused, so it is never executed; see `sdp plan show`")
    else:
        _say(f"3/4 Plan {plan.plan_id[:19]}... was approved on an earlier run.")
    if record.execution is None:
        _say("4/4 Executing: Gaia stars, then JWST cutouts, then the image-counterpart test (10-20 min)...")
        execution = kernel.execute_plan.execute(ExecutePlanCommand(plan.plan_id, actor))
    else:
        _say("4/4 This plan already ran; showing its results.")
        execution = record.execution
    record = _plan_record(plan.plan_id)
    found = sorted(_query().investigations(record.set_id), key=lambda i: -i.rank.score) if record.set_id else []
    _say(
        f"Done. {len(execution.acquired)} acquired, {len(execution.unavailable)} unavailable, "
        f"{len(found)} candidate(s). Open the app's home page, or run `sdp investigate list {record.set_id}`."
    )
    emit(
        {
            "plan_id": plan.plan_id,
            "set_id": record.set_id,
            "execution": execution.to_document(),
            "candidates": [_investigation_summary(i) for i in found],
        }
    )


def main() -> None:
    try:
        app()
    except (DomainError, KeyError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
