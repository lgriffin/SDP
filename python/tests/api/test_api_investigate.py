"""T4 for the Investigate queries over GraphQL: sets, stages, investigations, dossiers (Investigate §6)."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from sdp.api.app import create_app
from sdp.kernel.ports.investigate import InvestigateFieldCommand, InvestigationRun, TriageCommand
from sdp.testing.investigate import (
    PLANTED,
    gaia_archive,
    investigate_ports,
    observation_archive,
    synthetic_catalogue,
    synthetic_field,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T4")


@pytest.fixture
def k() -> InMemoryKernel:
    catalogue = synthetic_catalogue()
    return build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=investigate_ports(catalogue=catalogue, observations=observation_archive(catalogue)),
    )


@pytest.fixture
def run(k: InMemoryKernel) -> InvestigationRun:
    assert k.kernel.investigate_field is not None
    return k.kernel.investigate_field.execute(InvestigateFieldCommand(synthetic_field(), synthetic_profile(), seed=5))


def gql(k: InMemoryKernel, query: str, **variables: Any) -> dict[str, Any]:
    response = TestClient(create_app(k)).post("/graphql", json={"query": query, "variables": variables})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "errors" not in body, body["errors"]
    data: dict[str, Any] = body["data"]
    return data


@pytest.mark.req("INV-007", "INV-008")
def test_sets_stages_and_investigations_are_queryable(k: InMemoryKernel, run: InvestigationRun) -> None:
    sets = gql(
        k,
        """{ investigationSets { id profileHash complete objectsExamined label
             stages { stage state artifactId } nullResults manifest
             investigations { id candidateKey score state label } } }""",
    )["investigationSets"]
    assert len(sets) == 1
    s = sets[0]
    assert s["id"] == run.investigation_set.set_id and s["complete"] and s["label"] == "EXPLORATORY"
    assert [st["stage"] for st in s["stages"]] == ["identify", "retrieve", "test", "candidates"]
    assert all(st["state"] == "completed" and st["artifactId"] for st in s["stages"])
    assert s["objectsExamined"] == 60 and s["nullResults"]
    assert s["manifest"]["profile_hash"] == s["profileHash"]
    keys = [i["candidateKey"] for i in s["investigations"]]
    assert sorted(keys) == sorted(PLANTED)
    scores = [i["score"] for i in s["investigations"]]
    assert scores == sorted(scores, reverse=True)
    assert {i["label"] for i in s["investigations"]} == {"EXPLORATORY"}


def test_an_investigation_carries_its_transitions_and_dossier(k: InMemoryKernel, run: InvestigationRun) -> None:
    inv = run.investigations[0]
    assert k.kernel.triage_investigation is not None
    k.kernel.triage_investigation.execute(TriageCommand(inv.investigation_id, "park", "leigh", "wait for DR4"))
    found = gql(
        k,
        """query($id: ID!) { investigation(id: $id) {
             id state rankingPolicy testResults evidenceUsed transitions { fromState toState reason actor }
             dossier } }""",
        id=inv.investigation_id,
    )["investigation"]
    assert found["state"] == "parked"
    assert [(t["fromState"], t["toState"]) for t in found["transitions"]] == [(None, "open"), ("open", "parked")]
    assert found["transitions"][-1]["reason"] == "wait for DR4"
    assert found["testResults"] and found["dossier"]["investigation_id"] == inv.investigation_id
    parked = gql(
        k,
        'query($id: ID!) { investigationSet(id: $id) { investigations(state: "parked") { id } } }',
        id=run.investigation_set.set_id,
    )["investigationSet"]["investigations"]
    assert [p["id"] for p in parked] == [inv.investigation_id]


def test_unknown_ids_return_null(k: InMemoryKernel) -> None:
    data = gql(k, '{ investigationSet(id: "set-x") { id } investigation(id: "inv-x") { id } }')
    assert data == {"investigationSet": None, "investigation": None}
