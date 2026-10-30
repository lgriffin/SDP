"""T4 for ``sdp investigate``: run a field from a YAML profile, query and triage, all as JSON."""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from sdp.cli import app as cli
from sdp.kernel.application.rocrate import build_crate_metadata, zip_crate
from sdp.kernel.domain.canonical import sha256_id
from sdp.testing.investigate import (
    FIELD_CENTRE,
    PLANTED,
    gaia_archive,
    investigate_ports,
    observation_archive,
    synthetic_catalogue,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T4")
runner = CliRunner()
REGION = f"icrs;circle({FIELD_CENTRE[0]},{FIELD_CENTRE[1]},0.5)"


@pytest.fixture
def k() -> Iterator[InMemoryKernel]:
    catalogue = synthetic_catalogue()
    kernel = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=investigate_ports(catalogue=catalogue, observations=observation_archive(catalogue)),
    )
    cli.use_runtime(lambda: kernel)
    yield kernel
    cli.use_runtime(cli._production_runtime)


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(synthetic_profile().to_document(), sort_keys=False))
    return path


def invoke(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.req("INV-001")
def test_a_field_runs_from_a_yaml_profile_and_is_queried_and_triaged(k: InMemoryKernel, profile: Path) -> None:
    run = invoke("investigate", "--region", REGION, "--profile", str(profile), "--seed", "3", "--actor", "leigh")
    assert run["stages"] == {
        "identify": "completed",
        "retrieve": "completed",
        "test": "completed",
        "candidates": "completed",
    }
    assert run["profile_hash"] == synthetic_profile().profile_hash
    assert run["label"] == "EXPLORATORY"
    assert sorted(c["candidate"] for c in run["candidates"]) == sorted(PLANTED)
    set_id = run["set_id"]

    assert [s["set_id"] for s in invoke("investigate", "sets")] == [set_id]
    manifest = invoke("investigate", "show", set_id)
    assert manifest["profile_hash"] == run["profile_hash"] and manifest["multiplicity"]["objects_examined"] == 60

    listed = invoke("investigate", "list", set_id)
    scores = [i["score"] for i in listed]
    assert scores == sorted(scores, reverse=True)
    first = listed[0]["investigation_id"]
    assert invoke("investigate", "dossier", first)["investigation_id"] == first

    parked = invoke("investigate", "park", first, "--actor", "leigh", "--reason", "revisit with DR4")
    assert parked["state"] == "parked"
    assert invoke("investigate", "reopen", first, "--actor", "leigh", "--reason", "DR4 is out")["state"] == "open"
    assert invoke("investigate", "annotate", first, "--actor", "leigh", "--note", "check the plates")["state"] == "open"
    assert (
        invoke("investigate", "close", listed[1]["investigation_id"], "--actor", "leigh", "--reason", "plate flaw")[
            "state"
        ]
        == "closed"
    )


@pytest.mark.req("INV-009")
def test_a_refused_promotion_exits_non_zero_and_is_recorded(k: InMemoryKernel, profile: Path) -> None:
    run = invoke("investigate", "--region", REGION, "--profile", str(profile))
    inv = run["candidates"][0]["investigation_id"]
    result = runner.invoke(
        cli.app, ["investigate", "promote", inv, "--actor", "leigh", "--predeclaration", "sha256:" + "0" * 64]
    )
    assert result.exit_code != 0
    assert "InvestigationPromotionRefused" in [e.event.name for e in k.db.state.events]


def test_resume_reads_completed_stages_back(k: InMemoryKernel, profile: Path) -> None:
    first = invoke("investigate", "--region", REGION, "--profile", str(profile))
    again = invoke("investigate", "--region", REGION, "--profile", str(profile), "--resume", first["set_id"])
    assert again["set_id"] == first["set_id"] and again["manifest_id"] == first["manifest_id"]


def test_without_a_region_or_profile_it_prints_help(k: InMemoryKernel) -> None:
    result = runner.invoke(cli.app, ["investigate"], env={"NO_COLOR": "1", "TERM": "dumb"})
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert result.exit_code == 2 and "--region" in plain


def test_without_investigate_ports_the_run_is_refused_but_queries_work(profile: Path) -> None:
    kernel = build_in_memory_kernel()
    cli.use_runtime(lambda: kernel)
    try:
        result = runner.invoke(cli.app, ["investigate", "--region", REGION, "--profile", str(profile)])
        assert result.exit_code != 0 and "not wired" in str(result.exception)
        assert invoke("investigate", "sets") == []
    finally:
        cli.use_runtime(cli._production_runtime)


@pytest.mark.req("DSI-012")
def test_a_run_that_fills_its_row_cap_warns_and_names_the_source(k: InMemoryKernel, tmp_path: Path) -> None:
    doc = synthetic_profile().to_document()
    doc["identify"]["row_cap"] = 20
    capped = tmp_path / "capped.yaml"
    capped.write_text(yaml.safe_dump(doc, sort_keys=False))
    result = runner.invoke(cli.app, ["investigate", "--region", REGION, "--profile", str(capped)])
    assert result.exit_code == 0, result.output
    source = doc["identify"]["source_id"]
    assert json.loads(result.stdout)["row_cap_reached"] == {source: 20}
    assert f"warning: {source} had at least 20 rows here" in result.stderr


@pytest.mark.req("INV-021")
@pytest.mark.req("INV-022")
def test_a_set_is_saved_as_a_dossier_and_reproduced_from_it(k: InMemoryKernel, profile: Path, tmp_path: Path) -> None:
    run = invoke("investigate", "--region", REGION, "--profile", str(profile), "--seed", "3")
    out = tmp_path / "dossier.zip"
    saved = invoke("investigate", "bundle", run["set_id"], "--out", str(out))
    assert saved == {"set_id": run["set_id"], "path": str(out), "bytes": out.stat().st_size}

    def no_database() -> InMemoryKernel:
        raise AssertionError("reproducing a dossier must not open the database")

    dossiers = k.kernel.set_dossier
    assert dossiers is not None
    cli.use_runtime(no_database)
    cli.use_reproduction_tools(lambda: (dossiers.executor, dossiers.ports.tests, dossiers.ports.services))
    reproduced = invoke("investigate", "reproduce", str(out))
    assert reproduced["ok"] and reproduced["differences"] == [] and reproduced["results_compared"] > 0

    with zipfile.ZipFile(out) as zf:
        files = {name: zf.read(name) for name in zf.namelist()}
    recorded = json.loads(files["stages/test.json"])
    recorded["results"][0]["p_value"] = 0.123456
    parts = {p: (d, "", "") for p, d in files.items() if p != "ro-crate-metadata.json"}
    parts["stages/test.json"] = (json.dumps(recorded).encode(), "", "")
    forged = tmp_path / "forged.zip"
    metadata = build_crate_metadata(
        name="x", description="x", licence="x", files=parts, authors=[], date_published="2026-01-01", citations=[]
    )
    forged.write_bytes(zip_crate(metadata, parts))
    refused = runner.invoke(cli.app, ["investigate", "reproduce", str(forged)])
    assert refused.exit_code == 2 and "not the test output the manifest records" in refused.stderr

    manifest = json.loads(files["manifest.json"])
    for stage in manifest["stages"]:
        if stage["stage"] == "test":
            stage["artifact_id"] = sha256_id(parts["stages/test.json"][0])
    parts["manifest.json"] = (json.dumps(manifest).encode(), "", "")
    metadata = build_crate_metadata(
        name="x", description="x", licence="x", files=parts, authors=[], date_published="2026-01-01", citations=[]
    )
    changed = tmp_path / "changed.zip"
    changed.write_bytes(zip_crate(metadata, parts))
    result = runner.invoke(cli.app, ["investigate", "reproduce", str(changed)])
    assert result.exit_code == 1 and "FAIL" in result.stderr and "p_value" in result.stderr
    assert not json.loads(result.stdout)["ok"]

    cli.use_reproduction_tools(cli._production_reproduction_tools)
    cli.use_runtime(lambda: k)
    missing = runner.invoke(cli.app, ["investigate", "bundle", "invset-missing"])
    assert missing.exit_code != 0 and "no investigation set invset-missing" in str(missing.exception)
