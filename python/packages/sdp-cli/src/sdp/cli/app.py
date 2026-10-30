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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Protocol

import typer

from sdp.cli.discover import _benchmark_path, discover_app
from sdp.kernel.application.investigate import ROW_CAP_REACHED, STORED_BYTES, QueryInvestigationsUseCase
from sdp.kernel.application.kernel import Kernel
from sdp.kernel.application.planning import DEFAULT_BUDGET, QueryPlansUseCase, plan_view
from sdp.kernel.application.set_bundle import SetDossierUseCase, reproduce_dossier
from sdp.kernel.domain.errors import DomainError, IntegrityError
from sdp.kernel.domain.investigation import (
    STAGES,
    Field,
    Investigation,
    InvestigationSet,
    TestProfile,
    parse_region,
)
from sdp.kernel.ports.compute import ComputeExecutor
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
from sdp.kernel.science.investigation import InvestigationTestRegistry
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


ReproductionTools = tuple[ComputeExecutor, InvestigationTestRegistry, Mapping[str, Any]]


def _production_reproduction_tools() -> ReproductionTools:
    from sdp.adapters.bootstrap import reproduction_tools as installed

    return installed()


reproduction_tools: Callable[[], ReproductionTools] = _production_reproduction_tools


def use_reproduction_tools(factory: Callable[[], ReproductionTools]) -> None:
    """Point ``sdp investigate reproduce`` at other tests and services (tests)."""
    global reproduction_tools
    reproduction_tools = factory


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
local_app = typer.Typer(
    help="Your own frames: folders SDP reads in place and never writes to (#71). Everything SDP makes "
    "from them goes under SDP's data folder (SDP_DATA).",
    no_args_is_help=True,
)
benchmark_app = typer.Typer(
    help="Catalogues of known star clusters, so a cluster search can tell new from known (UC-01, STL-005).",
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
    ("local", local_app),
    ("benchmark", benchmark_app),
    ("discover", discover_app),
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


def _set_dossier() -> SetDossierUseCase:
    use_case = state.runtime().kernel.set_dossier
    if use_case is None:
        raise DomainError("Investigate is not wired: no observation archive or cross-match adapters are configured")
    return use_case


@investigate_app.command("bundle")
def investigate_bundle(
    set_id: str,
    out: Annotated[Path | None, typer.Option(help="Where to write the dossier; default <set_id>.zip")] = None,
) -> None:
    """Save the set's evidence dossier: one RO-Crate with its stages, plan, candidates, images and data (INV-021)."""
    try:
        bundle = _set_dossier().build(set_id)
    except KeyError:
        raise DomainError(f"no investigation set {set_id}") from None
    path = out or Path(f"{set_id}.zip")
    path.write_bytes(bundle)
    emit({"set_id": set_id, "path": str(path), "bytes": len(bundle)})


@investigate_app.command("reproduce")
def investigate_reproduce(
    bundle: Annotated[Path, typer.Argument(help="An evidence dossier from `sdp investigate bundle`")],
) -> None:
    """Re-run the Test stage from a dossier alone and compare with what it records (INV-022); exits 1 on a difference.

    Needs no database and no archive: only the installed tests and the services they call."""
    try:
        result = reproduce_dossier(bundle.read_bytes(), *reproduction_tools())
    except IntegrityError as exc:
        typer.echo(f"FAIL {exc}", err=True)
        raise typer.Exit(2) from exc
    for difference in result.differences:
        typer.echo(f"FAIL {difference}", err=True)
    emit(result.to_document())
    if not result.ok:
        raise typer.Exit(1)


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


# --- local sources (need no database) -------------------------------------------------------------


@local_app.command("add")
def local_add(
    name: Annotated[str, typer.Argument(help="A short name, such as astro")],
    path: Annotated[Path, typer.Argument(help="The folder, such as D:\\astrophotography")],
    scale: Annotated[
        float | None, typer.Option(help="Arcseconds per raw pixel, for frames whose header has no FOCALLEN")
    ] = None,
) -> None:
    """Add a folder of FITS frames as a read-only source. Nothing in the folder is changed."""
    from sdp.adapters.local.sources import LocalSources, data_dir

    data = data_dir()
    emit({"source": {**LocalSources(data).add(name, path, scale).to_document(), "name": name}, "data": str(data)})


@local_app.command("list")
def local_list() -> None:
    """The local sources added so far."""
    from sdp.adapters.local.sources import LocalSources, data_dir

    emit({name: s.to_document() for name, s in LocalSources(data_dir()).all().items()})


@local_app.command("index")
def local_index(name: Annotated[str, typer.Argument(help="The source's name")]) -> None:
    """Read every frame's header (never its pixels) into the source's index; a rerun reads only new or
    changed files. Expect a few minutes per 10,000 frames on a USB drive."""
    from sdp.adapters.local.index import FrameIndex
    from sdp.adapters.local.sources import LocalSources, data_dir

    data = data_dir()
    index = FrameIndex(LocalSources(data).get(name), data)
    started = time.monotonic()
    result = index.update(lambda count, read: typer.echo(f"{count:,} files, {read:,} headers read", err=True))
    emit(
        {
            "files": result.files,
            "headers_read": result.read,
            "unchanged": result.reused,
            "removed": result.removed,
            "unreadable": result.unreadable,
            "seconds": round(time.monotonic() - started, 1),
            "index": str(result.path),
            **{k: v for k, v in index.summary().items() if k in ("by_kind", "with_wcs")},
        }
    )


@local_app.command("status")
def local_status(name: Annotated[str, typer.Argument(help="The source's name")]) -> None:
    """What the index holds: frames by kind, targets with frame counts and exposure hours."""
    from sdp.adapters.local.index import FrameIndex
    from sdp.adapters.local.sources import LocalSources, data_dir

    data = data_dir()
    emit(FrameIndex(LocalSources(data).get(name), data).summary())


#: The Gaia reference a frame is matched against: bright stars in a cone wide enough to hold a frame
#: whose rough centre is off by about a degree
SOLVE_GAIA_COLUMNS = ("source_id", "ra", "dec", "phot_g_mean_mag")


def _gaia_reference(radius_deg: float, mag_limit: float) -> Any:
    """Gaia stars around a centre, through the normal acquisition: cached, and read from the local bulk
    copy when it is complete (#72)."""
    import numpy as np

    from sdp.adapters.local.solver import Reference
    from sdp.kernel.application.media import PARQUET, decode_artifact
    from sdp.kernel.domain.archive import CatalogueQuery, Cone, Filter
    from sdp.kernel.ports.driving import AcquireDatasetCommand

    runtime = state.runtime()

    def reference(centre: tuple[float, float]) -> Reference:
        query = CatalogueQuery(
            "gaia",
            "gaia:DR3",
            Cone(centre[0], centre[1], radius_deg),
            SOLVE_GAIA_COLUMNS,
            (Filter("phot_g_mean_mag", "<", mag_limit),),
            row_cap=200_000,
            chunk_rows=50_000,
        )
        result = runtime.kernel.acquire_dataset.execute(AcquireDatasetCommand(query, curate=False))
        warn_row_caps({"gaia": query.row_cap} if result.reached_row_cap else {})
        table = decode_artifact(runtime.store.get(result.raw.artifact_id), PARQUET)

        def column(name: str) -> Any:
            return np.asarray(table.column(name).to_numpy(zero_copy_only=False), dtype=float)

        return Reference(column("ra"), column("dec"), column("phot_g_mean_mag"), result.raw.artifact_id.value)

    return reference


def _name_resolver() -> Any:
    """SIMBAD's position for a name, or None when no resolver is wired (the names file is then the only
    source of names)."""
    use_case = state.runtime().kernel.investigate_field
    resolver = use_case.ports.resolver if use_case is not None else None
    if resolver is None:
        return None

    def centre(name: str) -> tuple[float, float] | None:
        record = resolver.by_name(name)
        if record is None or record.ra_deg is None or record.dec_deg is None:
            return None
        return float(record.ra_deg), float(record.dec_deg)

    return centre


def _add_centres(book: Any, centres: list[str] | None) -> None:
    from sdp.adapters.local.names import FROM_YOU, parse_centre

    for text in centres or ():
        book.set(*parse_centre(text), FROM_YOU)


CentreOption = Annotated[
    list[str] | None,
    typer.Option(
        "--centre",
        help="A target's position as NAME=RA,DEC in degrees, such as M31=10.68,41.27; kept in the names file",
    ),
]


@local_app.command("solve")
def local_solve(
    name: Annotated[str, typer.Argument(help="The source's name")],
    kind: Annotated[str | None, typer.Option(help="Only this kind: stack (the masters, quick) or light")] = None,
    target: Annotated[str | None, typer.Option(help="Only frames of this OBJECT or folder, e.g. M31")] = None,
    limit: Annotated[int | None, typer.Option(min=1, help="At most this many frames this run")] = None,
    resolve: Annotated[bool, typer.Option("--resolve", help="Solve again frames already solved")] = False,
    retry_refused: Annotated[bool, typer.Option("--retry-refused", help="Try refused frames again")] = False,
    gaia_radius: Annotated[float, typer.Option(help="Gaia cone radius around the rough centre, degrees")] = 2.0,
    gaia_mag: Annotated[float, typer.Option(help="Faintest Gaia G magnitude matched")] = 15.0,
    max_rms: Annotated[float, typer.Option(help="Largest accepted fit RMS, arcseconds")] = 1.5,
    min_matches: Annotated[int, typer.Option(help="Fewest matched stars accepted")] = 12,
    workers: Annotated[int, typer.Option(min=1, max=64, help="Frames solved side by side")] = 4,
    centre: CentreOption = None,
) -> None:
    """Plate-solve the source's frames with SDP's own solver: the stacks first, then the lights, unless
    --kind picks one. Each solution is written to <data>/solutions/<source>/<frame>.wcs.json; a frame that
    cannot be solved surely is refused, with its reason, in <frame>.unsolved.json. The frames are only
    read. Needs the database (for Gaia); needs no network once every target is in the names file."""
    from sdp.adapters.local.index import FrameIndex
    from sdp.adapters.local.names import NameBook
    from sdp.adapters.local.solver import SolverConfig
    from sdp.adapters.local.solving import SOLVABLE_KINDS, LocalSolver
    from sdp.adapters.local.sources import LocalSources, data_dir

    if kind is not None and kind not in ("light", "stack"):
        raise typer.BadParameter("light or stack", param_hint="--kind")
    data = data_dir()
    source = LocalSources(data).get(name)
    rows = FrameIndex(source, data).rows()
    if not rows:
        raise DomainError(f"the {name} index is empty; run sdp local index {name} first")
    names = NameBook(source, data)
    _add_centres(names, centre)
    solver = LocalSolver(
        source,
        data,
        _name_resolver(),
        _gaia_reference(gaia_radius, gaia_mag),
        SolverConfig(max_rms_arcsec=max_rms, min_matches=min_matches),
        names=names,
    )
    chosen: list[dict[str, Any]] = []
    # the stacks are few and quick, and a solved stack is a good start for nothing else, so they go first
    for each in (kind,) if kind else tuple(reversed(SOLVABLE_KINDS)):
        left = None if limit is None else limit - len(chosen)
        if left == 0:
            break
        chosen += solver.select(
            rows, kind=each, target=target, limit=left, resolve=resolve, retry_refused=retry_refused
        )
    started, done = time.monotonic(), [0]

    def report(o: Any) -> None:
        done[0] += 1
        detail = f"{o.matched} stars, RMS {o.rms_arcsec:.2f} arcsec" if o.status == "solved" else o.reason
        typer.echo(f"{done[0]}/{len(chosen)} {o.path}: {o.status}, {detail} ({o.seconds:.1f} s)", err=True)

    stacks = [r for r in chosen if r["kind"] == "stack"]
    lights = [r for r in chosen if r["kind"] != "stack"]
    outcomes = solver.run(stacks, rows, report, workers) + solver.run(lights, rows, report, workers)
    seconds = time.monotonic() - started
    run = {
        "source": source.source_id,
        "chosen": len(chosen),
        "solved": sum(1 for o in outcomes if o.status == "solved"),
        "refused": sum(1 for o in outcomes if o.status == "refused"),
        "seconds": round(seconds, 1),
        "frames_per_minute": round(60 * len(outcomes) / seconds, 1) if seconds > 0 else None,
        "workers": workers,
        "filters": {"kind": kind, "target": target, "limit": limit, "resolve": resolve},
        "config": solver.config.to_document(),
        "gaia": {"radius_deg": gaia_radius, "mag_limit": gaia_mag},
        "names": str(names.path),
        "frames": [o.__dict__ for o in outcomes],
    }
    # the run itself is kept as an artifact, like every other step's record
    record = state.runtime().store.put(json.dumps(run, sort_keys=True).encode(), "application/json")
    from sdp.adapters.local.quality import grade_source

    graded = grade_source(source, data, FrameIndex(source, data).rows())
    emit(
        {
            **{k: v for k, v in run.items() if k != "frames"},
            "graded_poor": sum(1 for g in graded if g["grade"] == "poor"),
            "run_artifact": record.value,
            **solver.status(FrameIndex(source, data)),
        }
    )


@local_app.command("names")
def local_names(
    name: Annotated[str, typer.Argument(help="The source's name")],
    centre: CentreOption = None,
    fetch: Annotated[
        bool, typer.Option("--fetch", help="Look up in SIMBAD now every OBJECT not in the file yet (needs network)")
    ] = False,
) -> None:
    """The names file: each target's position, from SIMBAD or from you. With every target in it, solving
    needs no network. Needs no database unless --fetch."""
    from sdp.adapters.local.index import FrameIndex
    from sdp.adapters.local.names import FROM_SIMBAD, NameBook
    from sdp.adapters.local.solving import SOLVABLE_KINDS, usable_name
    from sdp.adapters.local.sources import LocalSources, data_dir

    data = data_dir()
    source = LocalSources(data).get(name)
    book = NameBook(source, data)
    _add_centres(book, centre)
    used = sorted(
        {n for r in FrameIndex(source, data).rows() if r["kind"] in SOLVABLE_KINDS if (n := usable_name(r["object"]))}
    )
    unknown = [n for n in used if book.get(n) is None]
    not_found: list[str] = []
    if fetch and unknown:
        resolve = _name_resolver()
        if resolve is None:
            raise DomainError("no SIMBAD resolver is wired; give the positions with --centre NAME=RA,DEC instead")
        for each in unknown:
            found = resolve(each)
            if found is None:
                not_found.append(each)
            else:
                book.set(each, found[0], found[1], FROM_SIMBAD)
        unknown = [n for n in used if book.get(n) is None]
    emit(
        {
            "names": str(book.path),
            "known": book.entries(),
            "frames_name_these_unknown_targets": unknown,
            **({"simbad_does_not_know": not_found} if not_found else {}),
        }
    )


@local_app.command("verify")
def local_verify(
    name: Annotated[str, typer.Argument(help="The source's name")],
    quick: Annotated[
        bool, typer.Option("--quick", help="Compare sizes and dates only; hash only frames never fingerprinted")
    ] = False,
    accept: Annotated[
        bool, typer.Option("--accept", help="Take the current bytes of changed frames as their new fingerprints")
    ] = False,
) -> None:
    """Check that the source's frames are intact: each file's SHA-256 against the one first recorded for it.
    Lists frames missing, new, modified (size or date changed too) and corrupted (same size and date, other
    bytes). Reads every file in full (about an hour for 575 GB on a USB drive) unless --quick. Needs no
    database; the frames are only read."""
    from sdp.adapters.local.fingerprints import Fingerprints
    from sdp.adapters.local.sources import LocalSources, data_dir

    data = data_dir()
    prints = Fingerprints(LocalSources(data).get(name), data)
    started = time.monotonic()
    report = prints.verify(
        quick=quick,
        accept=accept,
        progress=lambda count, hashed: typer.echo(f"{count:,} frames checked, {hashed:,} hashed", err=True),
    )
    full = prints.path.with_name(f"verify-{time.strftime('%Y%m%dT%H%M%S')}.json")
    full.write_text(json.dumps(report.to_document(listed=None), indent=2) + "\n", "utf-8")
    emit(
        {
            **report.to_document(),
            "quick": quick,
            "seconds": round(time.monotonic() - started, 1),
            "fingerprints": str(prints.path),
            "report": str(full),
        }
    )
    if not report.intact:
        raise typer.Exit(1)


@local_app.command("manifest")
def local_manifest(
    name: Annotated[str, typer.Argument(help="The source's name")],
    out: Annotated[Path | None, typer.Option(help="The CSV to write; default beside the index")] = None,
) -> None:
    """Write a CSV of every fingerprinted frame's path, size and SHA-256, for a backup tool to check
    against. Run sdp local verify first so that every frame has a fingerprint. Needs no database."""
    from sdp.adapters.local.fingerprints import Fingerprints
    from sdp.adapters.local.sources import LocalSources, data_dir

    data = data_dir()
    prints = Fingerprints(LocalSources(data).get(name), data)
    target = out or prints.path.with_name("manifest.csv")
    emit({"frames": prints.manifest(target), "manifest": str(target)})


@local_app.command("solved")
def local_solved(name: Annotated[str, typer.Argument(help="The source's name")]) -> None:
    """How many frames are solved, refused (and why) and still to do. Needs no database."""
    from sdp.adapters.local.index import FrameIndex
    from sdp.adapters.local.solving import LocalSolver
    from sdp.adapters.local.sources import LocalSources, data_dir

    data = data_dir()
    source = LocalSources(data).get(name)
    solver = LocalSolver(source, data, None, lambda _c: None)  # type: ignore[arg-type,return-value]
    emit(solver.status(FrameIndex(source, data)))


@local_app.command("quality")
def local_quality(
    name: Annotated[str, typer.Argument(help="The source's name")],
    target: Annotated[str | None, typer.Option(help="Only summarise this OBJECT or top folder, e.g. M31")] = None,
) -> None:
    """Grade every solved frame good, usable or poor against the other frames of its session (its folder),
    with the reasons, and summarise each night. Solving measures the frames; this compares them. The
    grades go to <data>/quality/<source>/grades.parquet, and investigations leave out poor frames. Needs
    no database."""
    from sdp.adapters.local.index import FrameIndex
    from sdp.adapters.local.quality import GOOD, POOR, USABLE, Grades, grade_source, nights
    from sdp.adapters.local.sources import LocalSources, data_dir

    data = data_dir()
    source = LocalSources(data).get(name)
    rows = FrameIndex(source, data).rows()
    graded = grade_source(source, data, rows)
    summary = nights(graded, rows)
    if target is not None:
        wanted = target.replace(" ", "").lower()
        summary = [n for n in summary if n["target"].replace(" ", "").lower() == wanted]
    counts = {g: sum(1 for f in graded if f["grade"] == g) for g in (GOOD, USABLE, POOR)}
    emit(
        {
            "frames": len(graded),
            "by_grade": {g: n for g, n in counts.items() if n},
            "nights": summary,
            "grades": str(Grades(source, data).path),
        }
    )


def _position(source: Any, data: Path, at: str) -> tuple[float, float]:
    """``RA,DEC`` in degrees, or a name from the source's names file."""
    from sdp.adapters.local.names import NameBook

    ra_text, comma, dec_text = at.partition(",")
    if comma:
        try:
            return float(ra_text), float(dec_text)
        except ValueError:
            pass
    found = NameBook(source, data).get(at.strip())
    if found is None:
        raise DomainError(
            f"{at!r} is neither RA,DEC in degrees nor in the names file; add it with "
            f'sdp local names {source.name} --centre "{at.strip()}=RA,DEC"'
        )
    return found


@local_app.command("catalogue")
def local_catalogue(name: Annotated[str, typer.Argument(help="The source's name")]) -> None:
    """Rebuild the source's catalogue: one row per frame with its sky footprint, date, night, status and
    grade, in <data>/catalogue/<source>/frames.parquet (readable by pandas, DuckDB or a spreadsheet tool).
    Needs no database."""
    from sdp.adapters.local.catalogue import Catalogue
    from sdp.adapters.local.sources import LocalSources, data_dir

    data = data_dir()
    catalogue = Catalogue(LocalSources(data).get(name), data)
    rows = catalogue.build()
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    emit({"frames": len(rows), "by_status": dict(sorted(by_status.items())), "catalogue": str(catalogue.path)})


@local_app.command("find")
def local_find(
    name: Annotated[str, typer.Argument(help="The source's name")],
    at: Annotated[str, typer.Option(help='Where: "RA,DEC" in degrees, or a name in the names file, e.g. M31')],
    radius: Annotated[float, typer.Option(help="Also frames within this many degrees of it")] = 0.0,
    kind: Annotated[str | None, typer.Option(help="Only this kind: stack or light")] = None,
    grade: Annotated[str | None, typer.Option(help="This grade or better: good, usable or poor")] = None,
    since: Annotated[str | None, typer.Option("--from", help="First night, YYYY-MM-DD")] = None,
    until: Annotated[str | None, typer.Option("--to", help="Last night, YYYY-MM-DD")] = None,
    csv_out: Annotated[Path | None, typer.Option("--csv", help="Also write the frames to this CSV")] = None,
) -> None:
    """The solved frames that cover a place on the sky, oldest first: path, night, exposure, grade and how
    far the place is from the frame's edge. Rebuilds the catalogue first. Needs no database."""
    import csv
    from datetime import date

    from sdp.adapters.local.catalogue import Catalogue, find
    from sdp.adapters.local.quality import GOOD, POOR, USABLE
    from sdp.adapters.local.sources import LocalSources, data_dir

    if grade is not None and grade not in (GOOD, USABLE, POOR):
        raise typer.BadParameter("good, usable or poor", param_hint="--grade")
    data = data_dir()
    source = LocalSources(data).get(name)
    ra, dec = _position(source, data, at)
    found = find(
        Catalogue(source, data).build(),
        ra,
        dec,
        radius,
        kind=kind,
        grade=grade,
        since=date.fromisoformat(since) if since else None,
        until=date.fromisoformat(until) if until else None,
    )
    columns = ("path", "kind", "night", "date_obs", "exposure_s", "grade", "fwhm_arcsec", "distance_deg")
    frames = [{c: f[c] for c in columns} for f in found]
    if csv_out is not None:
        csv_out.parent.mkdir(parents=True, exist_ok=True)
        with csv_out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(frames)
    emit(
        {
            "at": {"ra_deg": ra, "dec_deg": dec, "radius_deg": radius},
            "frames": len(frames),
            "nights": len({f["night"] for f in frames if f["night"]}),
            "hours": round(sum((f["exposure_s"] or 0.0) for f in found if f["kind"] == "light") / 3600, 2),
            "found": frames[:200],
            **({"csv": str(csv_out)} if csv_out is not None else {}),
        }
    )


@local_app.command("coverage")
def local_coverage(name: Annotated[str, typer.Argument(help="The source's name")]) -> None:
    """Each target: nights, lights and stacks, solved, refused and still to do, hours of exposure, and the
    share graded good. Rebuilds the catalogue first. Needs no database."""
    from sdp.adapters.local.catalogue import Catalogue, coverage
    from sdp.adapters.local.sources import LocalSources, data_dir

    data = data_dir()
    emit({"targets": coverage(Catalogue(LocalSources(data).get(name), data).build())})


@local_app.command("sidecars")
def local_sidecars(name: Annotated[str, typer.Argument(help="The source's name")]) -> None:
    """Write a .wcs header file beside every solution that has none or an older one (frames solved before
    SDP wrote them). Solving writes them itself. Needs no database."""
    from sdp.adapters.local.sidecars import write_sidecars
    from sdp.adapters.local.solving import LocalSolver
    from sdp.adapters.local.sources import LocalSources, data_dir

    data = data_dir()
    solver = LocalSolver(LocalSources(data).get(name), data, None, lambda _c: None)  # type: ignore[arg-type,return-value]
    emit({"written": write_sidecars(solver), "solutions": str(solver.root)})


@local_app.command("export")
def local_export(
    name: Annotated[str, typer.Argument(help="The source's name")],
    kind: Annotated[str | None, typer.Option(help="Only this kind: stack or light")] = None,
    target: Annotated[str | None, typer.Option(help="Only frames of this OBJECT or folder, e.g. M31")] = None,
    to: Annotated[Path | None, typer.Option(help="Where the copies go; default <data>/exports/<source>")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Write copies that are already there again")] = False,
) -> None:
    """Copy solved frames with their WCS written into the header, for software that reads only the FITS
    header. The pixel bytes are copied unchanged; the source folder is never written to. A sub is about
    28 MB, so pick a --kind or --target. Needs no database."""
    from sdp.adapters.local.index import FrameIndex
    from sdp.adapters.local.sidecars import export
    from sdp.adapters.local.solving import SOLVABLE_KINDS, LocalSolver
    from sdp.adapters.local.sources import LocalSources, data_dir

    if kind is not None and kind not in SOLVABLE_KINDS:
        raise typer.BadParameter("light or stack", param_hint="--kind")
    data = data_dir()
    source = LocalSources(data).get(name)
    solver = LocalSolver(source, data, None, lambda _c: None)  # type: ignore[arg-type,return-value]
    rows = solver.select(FrameIndex(source, data).rows(), kind=kind, target=target, resolve=True)
    out = to or source.output_dir(data, "exports")
    result = export(source, solver, rows, out, overwrite=overwrite, progress=lambda p: typer.echo(p, err=True))
    emit(
        {
            "to": str(out),
            "written": len(result.written),
            "skipped": len(result.skipped),
            "bytes": result.bytes,
            "skipped_paths": result.skipped[:50],
        }
    )


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


@archive_app.command("bulk")
def archive_bulk(
    source: Annotated[str, typer.Option(help="Source id; gaia is the only one with publisher bulk files")] = "gaia",
    first: Annotated[int | None, typer.Option(help="Start at this file (0-based, in name order)")] = None,
    count: Annotated[int | None, typer.Option(help="Do at most this many files (try 1 first)")] = None,
    workers: Annotated[int, typer.Option(min=1, max=16, help="Files downloaded at once")] = 2,
    work_dir: Annotated[
        Path | None, typer.Option(help="Where downloads wait while being read; default beside the artifact store")
    ] = None,
    base_url: Annotated[str | None, typer.Option(help="The publisher's folder, ending in /")] = None,
    status: Annotated[bool, typer.Option("--status", help="Only report how much is stored")] = False,
) -> None:
    """Copy a whole catalogue release from the publisher's bulk files into the artifact store, one Parquet
    per file, skipping files already stored (#72). Stop it any time; run it again to resume."""
    import os

    from sdp.adapters.archives.bulk import GAIA_DR3_BULK, BulkSweep, select_files
    from sdp.adapters.archives.tap import load_sources
    from sdp.adapters.bootstrap import artifact_store

    if source != "gaia":
        raise typer.BadParameter("only gaia publishes bulk files SDP reads", param_hint="--source")
    root = os.environ.get("SDP_ARTIFACT_ROOT")
    if not root:
        raise DomainError("SDP_ARTIFACT_ROOT is not set: it names the artifact store the sweep fills")
    descriptor = load_sources()["gaia"].descriptor
    sweep = BulkSweep(
        store=artifact_store(Path(root)),
        descriptor=descriptor,
        work_dir=work_dir or Path(root).parent / "downloads",
        columns=descriptor.column_names(),
        base_url=base_url or GAIA_DR3_BULK,
        min_free_bytes=int(os.environ.get("SDP_MIN_FREE_BYTES", str(50 * 1024**3))),
    )
    files = sweep.files()
    if status:
        emit(sweep.status(files))
        return
    chosen = select_files(files, first, count)
    started, done = time.monotonic(), [0]

    def report(result: Any) -> None:
        done[0] += 1
        size = f"{result.parquet_bytes / 1e6:,.1f} MB" if result.parquet_bytes else ""
        typer.echo(
            f"{done[0]}/{len(chosen)} {result.name} {result.status} {result.rows:,} rows {size} "
            f"{result.seconds:.1f} s{' ' + result.error if result.error else ''}",
            err=True,
        )

    results = sweep.run(chosen, workers=workers, progress=report)
    summary = sweep.status(files)
    failed = [r.name for r in results if r.status == "failed"]
    emit(
        {
            **summary,
            "this_run": {"files": len(results), "failed": failed, "seconds": round(time.monotonic() - started, 1)},
        }
    )
    if failed:
        raise typer.Exit(1)


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
    from sdp.adapters.archives.registration import describe, register_defaults
    from sdp.adapters.bootstrap import archive_transport
    from sdp.adapters.postgres.capabilities import PostgresCapabilityRegistry
    from sdp.adapters.settings import Settings

    settings = Settings.from_env()
    registered = register_defaults(
        PostgresCapabilityRegistry(settings.database_url),
        lambda name: archive_transport(settings, name, 1.0),
        mast=mast,
    )
    return [describe(d) for d in registered]


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
    max_bytes: Annotated[
        str,
        typer.Option(
            help="Largest product to fetch, e.g. 500MB or 2GB (1 MB = 1024**2 bytes). Above 35MB it is a local "
            "pull: --out must be outside fixtures/, and the file is streamed to disk, not kept in a cassette"
        ),
    ] = "35MB",
) -> None:
    """Record MAST's capability descriptor and one product as a to_verify reference product (live)."""
    from sdp.adapters.archives.reference_recording import describe_mast, parse_size, record_product
    from sdp.adapters.archives.transport import CassetteTransport, PoliteTransport, UrllibTransport

    try:
        limit = parse_size(max_bytes)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--max-bytes") from None
    live = PoliteTransport(UrllibTransport(), min_seconds_between_calls=1.0)
    transport = CassetteTransport(
        cassettes / "integration" / name, "record", inner=live, recorder="integration-recording"
    )
    descriptor = describe_mast(transport, cassettes / "integration")
    recorded = record_product(transport, obs_id, filename, out / name, max_bytes=limit)
    emit(
        {
            "descriptor": {k: descriptor[k] for k in ("ivoid", "product_types", "protocols", "limits")},
            "product": {
                "directory": str(recorded.directory),
                "url": recorded.access_url,
                "bytes": recorded.size_bytes,
                "seconds": recorded.seconds,
                "sha256": recorded.sha256,
                "streamed": recorded.streamed,
            },
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


# --- known-cluster catalogues (UC-01 benchmarks) ------------------------------------------------


def _known_clusters() -> Any:
    from sdp.adapters.bootstrap import known_clusters
    from sdp.adapters.settings import Settings

    return known_clusters(Settings.from_env())


@benchmark_app.command("fetch")
def benchmark_fetch(
    catalogue: Annotated[
        list[str] | None, typer.Option("--catalogue", help="hr23 or cga20; repeat; both when omitted")
    ] = None,
    column: Annotated[
        list[str] | None,
        typer.Option(
            "--column", help="MINE=THEIRS, e.g. name=Cluster, when VizieR's labels do not pick it; one --catalogue only"
        ),
    ] = None,
    file: Annotated[
        list[str] | None,
        typer.Option(
            "--file",
            help="ID=PATH: a VOTable saved from any VizieR page or mirror, read instead of downloading; repeat",
        ),
    ] = None,
) -> None:
    """Download catalogues of known clusters from VizieR and store them as benchmark inputs.

    A candidate cluster is called potentially new only when it matches nothing in both catalogues.
    Each one is a few hundred kB and takes seconds. When CDS does not answer, VizieR's mirrors
    (Harvard, India, China, South Africa, Japan) are tried in turn. When none answers, save the table
    as a VOTable from any VizieR page in a browser and pass it with --file. Pass the printed --input
    options to `sdp experiment define uc01-stellar-clusters`.
    """
    import io

    import pyarrow.parquet as pq

    from sdp.adapters.archives.benchmarks import CATALOGUES, from_votable

    files = dict(_pair(f, "--file") for f in file or [])
    if files and catalogue:
        raise typer.BadParameter("--file names its own catalogue; leave out --catalogue", param_hint="--file")
    chosen = sorted(files) if files else catalogue or sorted(CATALOGUES, reverse=True)
    unknown = [c for c in chosen if c not in CATALOGUES]
    if unknown:
        raise typer.BadParameter(f"unknown {unknown}; known: {sorted(CATALOGUES)}", param_hint="--catalogue")
    if column and len(chosen) != 1:
        # each catalogue names its columns after its own paper, so one override fits only one of them
        raise typer.BadParameter("name exactly one --catalogue to apply column overrides to", param_hint="--column")
    overrides = dict(_pair(c, "--column") for c in column or [])
    source = None if files else _known_clusters()
    store = state.runtime().store
    out = []
    for benchmark_id in chosen:
        if benchmark_id in files:
            path = Path(files[benchmark_id])
            _say(f"Reading {benchmark_id} ({CATALOGUES[benchmark_id].description}) from {path}...")
            vizier = CATALOGUES[benchmark_id].vizier_table
            try:
                body = path.read_bytes()
            except OSError as exc:
                raise typer.BadParameter(f"cannot read {path}: {exc.strerror or exc}", param_hint="--file") from exc
            table = from_votable(benchmark_id, body, overrides, f"VizieR {vizier} from file {path.name}")
        else:
            assert source is not None
            _say(f"Fetching {benchmark_id} ({CATALOGUES[benchmark_id].description}) from VizieR...")
            table = source.fetch(benchmark_id, overrides)
        sink = io.BytesIO()
        pq.write_table(table, sink, compression="zstd")
        release = CATALOGUES[benchmark_id].release_label
        artifact = store.put(sink.getvalue(), "application/vnd.apache.parquet")
        store.link(_benchmark_path(benchmark_id, release), artifact)
        meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
        out.append(
            {
                "benchmark": benchmark_id,
                "release": release,
                "clusters": table.num_rows,
                "artifact": artifact.value,
                "columns": json.loads(meta["sdp.benchmark.columns"]),
                "input": f"benchmark:{benchmark_id}={artifact.value}",
            }
        )
    emit(out)


@benchmark_app.command("list")
def benchmark_list() -> None:
    """The catalogues SDP knows, and the stored copy of each (null until fetched)."""
    from sdp.adapters.archives.benchmarks import CATALOGUES

    store = state.runtime().store
    out = []
    for benchmark_id, c in sorted(CATALOGUES.items()):
        artifact = store.resolve(_benchmark_path(benchmark_id, c.release_label))
        out.append(
            {
                "benchmark": benchmark_id,
                "release": c.release_label,
                "vizier_table": c.vizier_table,
                "citation": c.citation,
                "description": c.description,
                "artifact": artifact.value if artifact else None,
                "input": f"benchmark:{benchmark_id}={artifact.value}" if artifact else None,
            }
        )
    emit(out)


def main() -> None:
    try:
        app()
    except (DomainError, KeyError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
