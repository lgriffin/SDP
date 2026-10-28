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

from sdp.kernel.application.kernel import Kernel
from sdp.kernel.domain.errors import DomainError
from sdp.kernel.ports.driving import (
    ApproveVerdictCommand,
    AssembleDossierCommand,
    DefineExperimentCommand,
    FreezePredeclarationCommand,
)
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
for name, sub in (
    ("predeclaration", predeclaration_app),
    ("experiment", experiment_app),
    ("dossier", dossier_app),
    ("ledger", ledger_app),
    ("db", db_app),
    ("artifact", artifact_app),
    ("telemetry", telemetry_app),
    ("archive", archive_app),
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


@app.command("publish")
def publish(dossier_id: str) -> None:
    """Build the RO-Crate bundle for a dossier and deposit it for a DOI."""
    record = state.runtime().kernel.publish_bundle.publish(dossier_id)
    emit({"doi": record.doi, "url": record.url, "deposition_id": record.deposition_id})


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
    emit([r.to_document() for r in results])
    if any(r.problems for r in results):
        raise SystemExit(1)


def main() -> None:
    try:
        app()
    except (DomainError, KeyError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
