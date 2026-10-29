"""T4 for the planner's REST commands and GraphQL queries, in the shape agreed with the evidence UI
(ADR-020): POST /plans, /plans/{id}/approve, /refuse and /execute; ``plans`` and ``plan``."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from sdp.api.app import create_app
from sdp.kernel.application.planning import DEFAULT_BUDGET
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
REGION = f"icrs;circle({FIELD_CENTRE[0]},{FIELD_CENTRE[1]},0.5)"
NEEDS = ("lightcurve",)
PLAN = "id setId state document execution"


@pytest.fixture
def k() -> InMemoryKernel:
    catalogue = synthetic_catalogue()
    return build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=investigate_ports(
            ProperMotionExcessTest(needs_products=NEEDS),
            catalogue=catalogue,
            observations=observation_archive(catalogue),
        ),
        discovery=planning_discovery(),
    )


def body(**extra: Any) -> dict[str, Any]:
    return {"region": REGION, "profile": synthetic_profile(needs_products=NEEDS).to_document(), "seed": 3, **extra}


def graphql(client: TestClient, query: str, **variables: Any) -> Any:
    response = client.post("/graphql", json={"query": query, "variables": variables})
    assert response.status_code == 200 and "errors" not in response.json(), response.text
    return response.json()["data"]


@pytest.mark.req("INT-013")
@pytest.mark.req("INT-015")
def test_a_plan_is_drafted_approved_and_executed_and_the_ui_reads_each_state(k: InMemoryKernel) -> None:
    client = TestClient(create_app(k))
    drafted = client.post("/plans", json=body(actor="leigh"))
    assert drafted.status_code == 200, drafted.text
    plan_id = drafted.json()["plan_id"]
    assert drafted.json()["state"] == "drafted" and drafted.json()["id"] == plan_id
    assert drafted.json()["document"]["constraints"]["budget"] == DEFAULT_BUDGET.to_document()
    [listed] = graphql(client, f"query {{ plans {{ {PLAN} }} }}")["plans"]
    assert listed["id"] == plan_id and listed["state"] == "drafted" and listed["setId"] is None

    approved = client.post(f"/plans/{plan_id}/approve", json={"actor": "leigh"})
    assert approved.status_code == 200 and approved.json()["state"] == "auto_approved"

    executed = client.post(f"/plans/{plan_id}/execute", json={})
    assert executed.status_code == 200, executed.text
    plan = graphql(client, f"query P($id: ID!) {{ plan(id: $id) {{ {PLAN} }} }}", id=plan_id)["plan"]
    assert plan["state"] == "executed" and plan["setId"] == executed.json()["setId"]
    assert plan["execution"]["plan_id"] == plan_id and plan["execution"]["unavailable"] == []
    sets = graphql(client, "{ investigationSets { id planArtifactId } }")["investigationSets"]
    assert sets == [{"id": plan["setId"], "planArtifactId": plan_id}]
    assert client.post(f"/plans/{plan_id}/execute").status_code == 409


@pytest.mark.req("INT-014")
def test_above_the_threshold_approval_is_refused_here_and_points_at_the_cli(k: InMemoryKernel) -> None:
    client = TestClient(create_app(k))
    budget = {**DEFAULT_BUDGET.to_document(), "approval_threshold_bytes": 1000}
    plan_id = client.post("/plans", json=body(budget=budget)).json()["plan_id"]
    response = client.post(f"/plans/{plan_id}/approve", json={"actor": "leigh"})
    assert response.status_code == 409
    assert f"sdp plan approve {plan_id} --actor leigh" in response.json()["error"]
    assert client.post(f"/plans/{plan_id}/execute").status_code == 409
    k.ledger.append(kind="approval", subject_id=plan_id, subject_hash=plan_id, signer="leigh", payload={})
    approved = client.post(f"/plans/{plan_id}/approve", json={"actor": "leigh"})
    assert approved.status_code == 200 and approved.json()["state"] == "approved"


@pytest.mark.req("INT-014")
def test_a_refusal_needs_a_reason_and_is_final(k: InMemoryKernel) -> None:
    client = TestClient(create_app(k))
    plan_id = client.post("/plans", json=body()).json()["plan_id"]
    assert client.post(f"/plans/{plan_id}/refuse", json={"actor": "leigh"}).status_code == 400
    refused = client.post(f"/plans/{plan_id}/refuse", json={"actor": "leigh", "reason": "wrong field"})
    assert refused.status_code == 200 and refused.json()["state"] == "refused"
    assert refused.json()["document"]["approval"]["reason"] == "wrong field"
    assert client.post(f"/plans/{plan_id}/approve", json={"actor": "leigh"}).status_code == 409
    assert client.post(f"/plans/{plan_id}/execute").status_code == 409


def test_unknown_plans_malformed_requests_and_an_unwired_planner(k: InMemoryKernel) -> None:
    client = TestClient(create_app(k))
    missing = "sha256:" + "0" * 64
    assert client.post(f"/plans/{missing}/approve", json={"actor": "leigh"}).status_code == 404
    assert client.post(f"/plans/{missing}/execute").status_code == 404
    assert client.post("/plans", json={"profile": {}}).status_code == 400
    malformed: Any
    for malformed in ([], None, "plan", 3):
        assert client.post("/plans", json=malformed).status_code == 400
        assert client.post(f"/plans/{missing}/approve", json=malformed).status_code == 400
    for actor in ("", "  ", None, 7):
        assert client.post("/plans", json=body(actor=actor)).status_code == 400
        assert client.post(f"/plans/{missing}/refuse", json={"actor": actor, "reason": "r"}).status_code == 400
        assert client.post(f"/plans/{missing}/execute", json={"actor": actor}).status_code == 400
    assert graphql(client, f'{{ plan(id: "{missing}") {{ id }} }}')["plan"] is None
    bare = TestClient(create_app(build_in_memory_kernel()))
    unwired = bare.post("/plans", json=body())
    assert unwired.status_code == 503 and unwired.json()["type"] == "PlannerUnavailable"
    assert graphql(bare, "{ plans { id } }")["plans"] == []


@pytest.mark.req("INT-014")
def test_a_request_may_tighten_the_servers_budget_but_never_relax_it(k: InMemoryKernel) -> None:
    client = TestClient(create_app(k))
    server = DEFAULT_BUDGET.to_document()
    for name, looser in (
        ("approval_threshold_bytes", server["approval_threshold_bytes"] + 1),
        ("max_bytes", server["max_bytes"] * 2),
        ("max_requests_per_source", server["max_requests_per_source"] + 1),
        ("max_wall_seconds", server["max_wall_seconds"] + 1),
        ("min_nas_headroom_bytes", 0),
    ):
        response = client.post("/plans", json=body(budget={**server, name: looser}))
        assert response.status_code == 400 and name in response.json()["error"], name
    assert client.post("/plans", json=body(budget={})).status_code == 400
    assert client.post("/plans", json=body(budget=None)).status_code == 400
    tighter = {**server, "max_bytes": server["max_bytes"] // 2}
    drafted = client.post("/plans", json=body(budget=tighter))
    assert drafted.status_code == 200 and drafted.json()["document"]["constraints"]["budget"] == tighter
