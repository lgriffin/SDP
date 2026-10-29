"""T4 for ``sdp plan``: draft, approve (signing the ledger above the threshold), refuse and execute."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from sdp.cli import app as cli
from sdp.kernel.application.kernel import Kernel
from sdp.kernel.domain.errors import DomainError
from sdp.kernel.domain.integration import AcquisitionBudget
from sdp.kernel.ports.ledger import Ledger
from sdp.kernel.ports.storage import ArtifactStore
from sdp.testing.investigate import (
    FIELD_CENTRE,
    ProperMotionExcessTest,
    gaia_archive,
    investigate_ports,
    observation_archive,
    planning_discovery,
    synthetic_catalogue,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T4")
runner = CliRunner()
REGION = f"icrs;circle({FIELD_CENTRE[0]},{FIELD_CENTRE[1]},0.5)"
NEEDS = ("lightcurve",)


@dataclass
class BudgetedRuntime:
    """An in-memory kernel with the budget a deployment would read from its settings."""

    k: InMemoryKernel
    budget: AcquisitionBudget

    @property
    def kernel(self) -> Kernel:
        return self.k.kernel

    @property
    def store(self) -> ArtifactStore:
        return self.k.store

    @property
    def ledger(self) -> Ledger:
        return self.k.ledger


def runtime(threshold: int) -> BudgetedRuntime:
    catalogue = synthetic_catalogue()
    k = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=investigate_ports(
            ProperMotionExcessTest(needs_products=NEEDS),
            catalogue=catalogue,
            observations=observation_archive(catalogue),
        ),
        discovery=planning_discovery(),
    )
    budget = AcquisitionBudget(1_000_000_000, 50, 3600.0, 0, threshold)
    return BudgetedRuntime(k, budget)


@pytest.fixture
def signed() -> Iterator[BudgetedRuntime]:
    rt = runtime(threshold=1000)
    cli.use_runtime(lambda: rt)
    yield rt
    cli.use_runtime(cli._production_runtime)


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(synthetic_profile(needs_products=NEEDS).to_document(), sort_keys=False))
    return path


def invoke(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.req("INT-014")
def test_above_the_threshold_approve_signs_the_ledger_then_execute_runs_the_plan(
    signed: BudgetedRuntime, profile: Path
) -> None:
    drafted = invoke("plan", "draft", "--region", REGION, "--profile", str(profile), "--seed", "2")
    plan_id = drafted["plan_id"]
    assert drafted["state"] == "drafted" and drafted["requires_signed_approval"] and drafted["items"] == 7
    assert [p["plan_id"] for p in invoke("plan", "list")] == [plan_id]
    approved = invoke("plan", "approve", plan_id, "--actor", "leigh")
    assert approved["state"] == "approved"
    [entry] = [e for e in signed.ledger.entries() if e.kind == "approval"]
    assert entry.subject_hash == plan_id and entry.signer == "leigh"
    assert invoke("plan", "show", plan_id)["document"]["approval"]["ledger_entry_id"] == entry.entry_id
    executed = invoke("plan", "execute", plan_id, "--actor", "leigh")
    assert executed["state"] == "executed" and executed["set_id"]
    assert len(executed["execution"]["acquired"]) == 7


@pytest.mark.req("INT-014")
def test_a_refused_plan_cannot_be_executed(signed: BudgetedRuntime, profile: Path) -> None:
    plan_id = invoke("plan", "draft", "--region", REGION, "--profile", str(profile))["plan_id"]
    assert invoke("plan", "refuse", plan_id, "--actor", "leigh", "--reason", "too much")["state"] == "refused"
    result = runner.invoke(cli.app, ["plan", "execute", plan_id])
    assert result.exit_code != 0 and "not approved" in str(result.exception or result.output)
    assert not [e for e in signed.ledger.entries() if e.kind == "approval"]


def test_below_the_threshold_approval_is_automatic_and_signs_nothing(profile: Path) -> None:
    rt = runtime(threshold=500_000_000)
    cli.use_runtime(lambda: rt)
    try:
        plan_id = invoke("plan", "draft", "--region", REGION, "--profile", str(profile))["plan_id"]
        assert invoke("plan", "approve", plan_id, "--actor", "leigh")["state"] == "auto_approved"
        assert not [e for e in rt.ledger.entries() if e.kind == "approval"]
    finally:
        cli.use_runtime(cli._production_runtime)


@pytest.mark.req("INT-014")
def test_a_retried_approve_reuses_the_actors_signed_approval(signed: BudgetedRuntime, profile: Path) -> None:
    plan_id = invoke("plan", "draft", "--region", REGION, "--profile", str(profile))["plan_id"]
    earlier = signed.ledger.append(
        kind="approval", subject_id=plan_id, subject_hash=plan_id, signer="leigh", payload={}
    )
    assert invoke("plan", "approve", plan_id, "--actor", "leigh")["state"] == "approved"
    assert [e.entry_id for e in signed.ledger.entries() if e.kind == "approval"] == [earlier.entry_id]


@pytest.mark.req("INT-014")
def test_an_approval_entry_that_does_not_verify_approves_nothing(signed: BudgetedRuntime, profile: Path) -> None:
    plan_id = invoke("plan", "draft", "--region", REGION, "--profile", str(profile))["plan_id"]
    entry = signed.ledger.append(kind="approval", subject_id=plan_id, subject_hash=plan_id, signer="leigh", payload={})
    signed.k.ledger.tamper(entry.sequence, signature="forged")
    result = runner.invoke(cli.app, ["plan", "approve", plan_id, "--actor", "leigh"])
    assert result.exit_code != 0 and "does not verify" in str(result.exception)
    assert invoke("plan", "show", plan_id)["document"]["approval"] is None


def test_a_ledger_that_cannot_sign_is_a_handled_error(
    signed: BudgetedRuntime, profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_id = invoke("plan", "draft", "--region", REGION, "--profile", str(profile))["plan_id"]

    def cannot_sign(**_: Any) -> None:
        raise RuntimeError("no signing key configured and require_signatures is set")

    monkeypatch.setattr(signed.k.ledger, "append", cannot_sign)
    result = runner.invoke(cli.app, ["plan", "approve", plan_id, "--actor", "leigh"])
    assert result.exit_code != 0 and isinstance(result.exception, DomainError)
    assert "no signing key configured" in str(result.exception)
