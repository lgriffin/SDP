"""The ``sdp`` command line (ADR-016): commands that change evidence, each signed through the ledger.

Every command prints JSON on stdout so scripts and the harness can read it. The application is
built lazily from the environment (``sdp.adapters.settings``); tests swap ``runtime_factory`` for
an in-memory kernel.
"""

from __future__ import annotations

import json
import mimetypes
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Protocol

import typer

from sdp.kernel.application.investigate import QueryInvestigationsUseCase
from sdp.kernel.application.kernel import Kernel
from sdp.kernel.domain.errors import DomainError
from sdp.kernel.domain.investigation import Field, Investigation, InvestigationSet, TestProfile, parse_region
from sdp.kernel.ports.driving import (
    ApproveVerdictCommand,
    AssembleDossierCommand,
    DefineExperimentCommand,
    FreezePredeclarationCommand,
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
    ("integration", integration_app),
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
    }


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
    result = state.runtime().kernel.acquire_dataset.execute(AcquireDatasetCommand(query, curate=curate))
    emit(
        {
            "raw": result.raw.artifact_id.value,
            "curated": result.curated.artifact_id.value if result.curated else None,
            "reused": result.reused,
            "query": result.raw.source_ref.query_text,
        }
    )


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


def main() -> None:
    try:
        app()
    except (DomainError, KeyError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
