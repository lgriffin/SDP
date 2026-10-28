"""T4 for the CLI: the commands drive the kernel wired to fakes and print JSON."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sdp.cli import app as cli
from sdp.testing.synthetic import make_series
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T4")
runner = CliRunner()


@pytest.fixture
def k() -> Iterator[InMemoryKernel]:
    kernel = build_in_memory_kernel()
    cli.use_runtime(lambda: kernel)
    yield kernel
    cli.use_runtime(cli._production_runtime)


def invoke(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_the_whole_signed_workflow_from_the_command_line(k: InMemoryKernel, tmp_path: Path) -> None:
    frozen = invoke("predeclaration", "freeze", "synthetic-linear", "--signer", "leigh")
    assert frozen["ledger_entry"]["kind"] == "predeclaration"

    series = tmp_path / "series.json"
    series.write_bytes(make_series())
    defined = invoke(
        "experiment", "define", "synthetic-linear",
        "--predeclaration", frozen["predeclaration_hash"],
        "--input", f"series={series}",
        "--seed", "42",
    )  # fmt: skip
    experiment_id = defined["experiment_id"]
    assert defined["state"] == "defined"

    assert invoke("experiment", "run", experiment_id)["state"] == "completed"
    validated = invoke("experiment", "validate", experiment_id)
    assert validated["verdict"] == "VERIFIED" and validated["checks"]
    assert invoke("experiment", "show", experiment_id)["verdict"]["status"] == "VERIFIED"

    dossier = invoke("dossier", "assemble", experiment_id, "--limitation", "Synthetic data only.")
    approval = invoke("approve", experiment_id, "--reviewer", "reviewer")
    assert approval["kind"] == "approval"
    published = invoke("publish", dossier["dossier_id"])
    assert published["doi"].startswith("10.5072/")

    # The bundle a reviewer downloads reproduces from it and the input alone (NFR-004).
    bundle = invoke("bundle", dossier["dossier_id"], "--out", str(tmp_path / "bundle.zip"))
    assert bundle["problems"] == []
    reproduced = invoke(
        "reproduce", bundle["bundle"], "--inputs", str(tmp_path), "--out", str(tmp_path / "reproduction")
    )
    assert reproduced["ok"] and reproduced["reproduced_verdict"] == "VERIFIED"

    assert invoke("ledger", "verify") == {"entries": 2, "problems": []}
    names = [e["name"] for e in invoke("events")]
    assert "VerdictIssued" in names and "PredeclarationFrozen" in names


def test_ledger_verify_exits_non_zero_on_tampering(k: InMemoryKernel) -> None:
    invoke("predeclaration", "freeze", "synthetic-linear", "--signer", "leigh")
    k.ledger.tamper(0, subject_id="rewritten")
    result = runner.invoke(cli.app, ["ledger", "verify"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["problems"]


def test_params_are_parsed_as_json_and_stored_inputs_are_accepted(k: InMemoryKernel) -> None:
    frozen = invoke("predeclaration", "freeze", "synthetic-linear", "--signer", "leigh")
    artifact = k.store.put(make_series(), "application/json")
    defined = invoke(
        "experiment", "define", "synthetic-linear",
        "--predeclaration", frozen["predeclaration_hash"],
        "--input", f"series={artifact.value}",
        "--param", "extra=[1, 2]",
    )  # fmt: skip
    stored = k.kernel.query_evidence.experiment(defined["experiment_id"])
    assert stored is not None and stored.definition.parameters["extra"] == [1, 2]


def test_artifacts_round_trip_through_put_and_get(k: InMemoryKernel, tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("hello")
    put = invoke("artifact", "put", str(source))
    assert put["media_type"] == "text/plain"
    out = tmp_path / "copy.txt"
    result = runner.invoke(cli.app, ["artifact", "get", put["artifact_id"], "--out", str(out)])
    assert result.exit_code == 0 and out.read_text() == "hello"


def test_bad_pairs_are_reported_as_usage_errors(k: InMemoryKernel) -> None:
    result = runner.invoke(cli.app, ["experiment", "define", "p", "--predeclaration", "h", "--input", "nopair"])
    assert result.exit_code == 2 and "NAME=VALUE" in result.output
