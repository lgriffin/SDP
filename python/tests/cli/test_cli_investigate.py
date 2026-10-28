"""T4 for ``sdp investigate``: run a field from a YAML profile, query and triage, all as JSON."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from sdp.cli import app as cli
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
    result = runner.invoke(cli.app, ["investigate"])
    assert result.exit_code == 2 and "--region" in result.output


def test_without_investigate_ports_the_run_is_refused_but_queries_work(profile: Path) -> None:
    kernel = build_in_memory_kernel()
    cli.use_runtime(lambda: kernel)
    try:
        result = runner.invoke(cli.app, ["investigate", "--region", REGION, "--profile", str(profile)])
        assert result.exit_code != 0 and "not wired" in str(result.exception)
        assert invoke("investigate", "sets") == []
    finally:
        cli.use_runtime(cli._production_runtime)
