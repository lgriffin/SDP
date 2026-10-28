"""T4 for POST /investigations/{id}/triage, the unsigned triage steps the evidence UI sends (INV-011)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from sdp.api.app import create_app
from sdp.testing.investigate import (
    FIELD_CENTRE,
    gaia_archive,
    investigate_ports,
    observation_archive,
    synthetic_catalogue,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T4")
REGION = f"icrs;circle({FIELD_CENTRE[0]},{FIELD_CENTRE[1]},0.5)"


@pytest.fixture
def client() -> TestClient:
    catalogue = synthetic_catalogue()
    k: InMemoryKernel = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=investigate_ports(catalogue=catalogue, observations=observation_archive(catalogue)),
    )
    return TestClient(create_app(k))


def first_candidate(client: TestClient) -> str:
    run = client.post("/investigations", json={"region": REGION, "profile": synthetic_profile().to_document()})
    assert run.status_code == 200, run.text
    return str(run.json()["candidates"][0]["investigation_id"])


def triage(client: TestClient, investigation_id: str, **body: str) -> tuple[int, dict[str, object]]:
    response = client.post(f"/investigations/{investigation_id}/triage", json=body)
    return response.status_code, response.json()


@pytest.mark.req("INV-014")
def test_park_reopen_annotate_and_close_record_each_step(client: TestClient) -> None:
    inv = first_candidate(client)
    assert triage(client, inv, action="park", actor="ada", reason="later") == (
        200,
        {"investigation_id": inv, "state": "parked", "transitions": 2},
    )
    assert triage(client, inv, action="reopen", actor="ada", reason="DR4 is out")[1]["state"] == "open"
    assert triage(client, inv, action="annotate", actor="ada", reason="bright neighbour")[1]["state"] == "open"
    assert triage(client, inv, action="close", actor="ada", reason="the neighbour explains it")[1]["state"] == "closed"
    query = "query($id: ID!) { investigation(id: $id) { state transitions { toState actor reason } notes } }"
    doc = client.post("/graphql", json={"query": query, "variables": {"id": inv}}).json()["data"]["investigation"]
    assert [t["toState"] for t in doc["transitions"]] == ["open", "parked", "open", "closed"]
    assert doc["transitions"][-1] == {"toState": "closed", "actor": "ada", "reason": "the neighbour explains it"}
    assert doc["notes"][-1]["text"] == "bright neighbour"


@pytest.mark.req("INV-014")
def test_an_illegal_or_unexplained_step_is_a_409(client: TestClient) -> None:
    inv = first_candidate(client)
    assert triage(client, inv, action="reopen", actor="ada", reason="x")[0] == 409
    assert triage(client, inv, action="park", actor="ada", reason=" ")[0] == 409
    assert triage(client, inv, action="close", actor="ada", reason="done")[0] == 200
    status, body = triage(client, inv, action="reopen", actor="ada", reason="again")
    assert status == 409 and "closed" in str(body["error"])


@pytest.mark.req("INV-014")
def test_promotion_and_malformed_requests_are_refused(client: TestClient) -> None:
    inv = first_candidate(client)
    status, body = triage(client, inv, action="promote", actor="ada", reason="x")
    assert status == 400 and "sdp investigate promote" in str(body["error"])
    assert triage(client, inv, action="explode", actor="ada", reason="x")[0] == 400
    assert client.post(f"/investigations/{inv}/triage", json={"action": "park"}).status_code == 400
    assert triage(client, "inv-missing", action="park", actor="ada", reason="x")[0] == 404


def test_a_kernel_without_investigations_answers_404() -> None:
    client = TestClient(create_app(build_in_memory_kernel()))
    body = {"action": "park", "actor": "a", "reason": "r"}
    assert client.post("/investigations/inv-1/triage", json=body).status_code == 404
