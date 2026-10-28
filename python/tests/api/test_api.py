"""T4 for the ASGI app over the in-memory kernel, and the GraphQL schema drift check (CTR-005)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient
from tests.usecase.conftest import Harness

from sdp.api.app import create_app
from sdp.api.schema import export_schema
from sdp.kernel.ports.driving import ApproveVerdictCommand
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T4")
SCHEMA = Path(__file__).resolve().parents[3] / "contracts" / "graphql" / "schema.graphql"


@pytest.fixture
def k() -> InMemoryKernel:
    return build_in_memory_kernel()


@pytest.fixture
def client(k: InMemoryKernel) -> TestClient:
    return TestClient(create_app(k))


def gql(client: TestClient, query: str, **variables: Any) -> dict[str, Any]:
    response = client.post("/graphql", json={"query": query, "variables": variables})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "errors" not in body, body["errors"]
    data: dict[str, Any] = body["data"]
    return data


def _validated(k: InMemoryKernel) -> str:
    from sdp.testing.synthetic import SyntheticLinearProtocol

    harness = Harness(k, SyntheticLinearProtocol())
    experiment = harness.run()
    k.kernel.validate_experiment.execute(experiment.experiment_id)
    return experiment.experiment_id


@pytest.mark.req("CTR-005")
@pytest.mark.tier("T6")
def test_committed_schema_matches_the_app() -> None:
    assert SCHEMA.read_text() == export_schema(), (
        "contracts/graphql/schema.graphql is stale: run `uv run sdp-api --export-schema > ../contracts/graphql/schema.graphql`"
        " and `contracts/tools/generate.sh`"
    )


def test_health_reports_each_dependency(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["checks"]["database"] == "ok" and body["checks"]["ledger"] == "ok"


def test_health_degrades_when_the_ledger_is_tampered(k: InMemoryKernel, client: TestClient) -> None:
    _validated(k)
    k.ledger.tamper(0, subject_id="rewritten")
    response = client.get("/health")
    assert response.status_code == 503 and response.json()["status"] == "degraded"


def test_experiments_query_returns_verdict_checks_and_manifest(k: InMemoryKernel, client: TestClient) -> None:
    experiment_id = _validated(k)
    data = gql(
        client,
        """query($id: ID!) { experiment(id: $id) {
             id state protocol verdict { status reasons } checks { gateId outcome severity }
             tasks { taskId status } manifest } }""",
        id=experiment_id,
    )
    e = data["experiment"]
    assert e["id"] == experiment_id and e["state"] == "validated" and e["verdict"]["status"] == "VERIFIED"
    assert e["checks"] and all(c["outcome"] for c in e["checks"])
    assert e["manifest"]["verdict"]["status"] == "VERIFIED"
    listed = gql(client, '{ experiments(state: "validated") { id } }')["experiments"]
    assert [x["id"] for x in listed] == [experiment_id]


def test_events_ledger_and_lineage_queries(k: InMemoryKernel, client: TestClient) -> None:
    experiment_id = _validated(k)
    k.kernel.publish_bundle.approve(ApproveVerdictCommand(experiment_id, "reviewer"))
    data = gql(client, "{ events(limit: 500) { sequence name aggregateId } ledger { kind signer signatureMethod } }")
    names = [e["name"] for e in data["events"]]
    assert "VerdictIssued" in names and "ApprovalRecorded" in names
    assert [e["kind"] for e in data["ledger"]] == ["predeclaration", "approval"]
    manifest_id = gql(client, "query($id: ID!) { experiment(id: $id) { manifestId } }", id=experiment_id)
    lineage = gql(client, "query($e: ID!) { lineage(entityId: $e) }", e=f"experiment/{experiment_id}")
    assert isinstance(lineage["lineage"], list) and manifest_id


def test_command_endpoints_run_and_validate(k: InMemoryKernel, client: TestClient) -> None:
    from sdp.testing.synthetic import SyntheticLinearProtocol

    experiment = Harness(k, SyntheticLinearProtocol()).define()
    ran = client.post(f"/experiments/{experiment.experiment_id}/run")
    assert ran.status_code == 200 and ran.json()["state"] == "completed"
    validated = client.post(f"/experiments/{experiment.experiment_id}/validate")
    assert validated.json()["verdict"] == "VERIFIED"
    again = client.post(f"/experiments/{experiment.experiment_id}/run")
    assert again.status_code == 409
    assert client.post("/experiments/exp-missing/run").status_code == 404


def test_there_are_no_mutations() -> None:
    assert "type Mutation" not in export_schema()
