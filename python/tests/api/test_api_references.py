"""T4 for POST /investigations/{id}/references and the GraphQL fields the comparison page reads (INV-024)."""

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
from sdp.testing.references import FakeReferenceCatalogue
from sdp.testing.wiring import build_in_memory_kernel

pytestmark = [pytest.mark.tier("T4"), pytest.mark.req("INV-024")]
REGION = f"icrs;circle({FIELD_CENTRE[0]},{FIELD_CENTRE[1]},0.5)"
QUERY = (
    "query($id: ID!) { referenceCatalogues { sourceId title releaseLabel citation description }"
    " investigation(id: $id) { state references } }"
)


def make_client(**catalogues: FakeReferenceCatalogue) -> TestClient:
    catalogue = synthetic_catalogue()
    k = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=investigate_ports(catalogue=catalogue, observations=observation_archive(catalogue)),
        references=catalogues,
    )
    return TestClient(create_app(k))


def first_candidate(client: TestClient) -> str:
    run = client.post("/investigations", json={"region": REGION, "profile": synthetic_profile().to_document()})
    assert run.status_code == 200, run.text
    return str(run.json()["candidates"][0]["investigation_id"])


def test_a_pull_answers_per_catalogue_and_graphql_shows_it() -> None:
    client = make_client(
        simbad=FakeReferenceCatalogue("simbad", "SIMBAD"),
        ps1=FakeReferenceCatalogue("ps1", "Pan-STARRS DR2", failure=TimeoutError("no answer")),
    )
    inv = first_candidate(client)
    response = client.post(
        f"/investigations/{inv}/references", json={"sources": ["simbad", "ps1"], "actor": "ada", "radius_arcsec": 8}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "investigation_id": inv,
        "pulls": [
            {"source_id": "simbad", "status": "none", "matches": 0},
            {"source_id": "ps1", "status": "failed", "matches": 0},
        ],
    }
    data = client.post("/graphql", json={"query": QUERY, "variables": {"id": inv}}).json()["data"]
    assert [c["sourceId"] for c in data["referenceCatalogues"]] == ["ps1", "simbad"]
    assert data["referenceCatalogues"][1]["title"] == "SIMBAD"
    pulls = data["investigation"]["references"]
    assert [(p["source_id"], p["radius_arcsec"], p["actor"]) for p in pulls] == [
        ("simbad", 8.0, "ada"),
        ("ps1", 8.0, "ada"),
    ]
    assert pulls[1]["error"] == "TimeoutError: no answer"


def test_malformed_unknown_and_unwired_pulls_are_refused() -> None:
    client = make_client(simbad=FakeReferenceCatalogue("simbad", "SIMBAD"))
    inv = first_candidate(client)
    post = client.post
    assert post(f"/investigations/{inv}/references", json={"sources": "simbad", "actor": "a"}).status_code == 400
    assert post(f"/investigations/{inv}/references", json={"sources": ["simbad"]}).status_code == 400
    refused = post(f"/investigations/{inv}/references", json={"sources": ["vsx"], "actor": "a"})
    assert refused.status_code == 409 and "unknown reference catalogue" in refused.json()["error"]
    assert post("/investigations/inv-x/references", json={"sources": ["simbad"], "actor": "a"}).status_code == 404
    bare = TestClient(create_app(build_in_memory_kernel()))
    assert bare.post("/investigations/inv-1/references", json={"sources": ["simbad"], "actor": "a"}).status_code == 503
    assert bare.post("/graphql", json={"query": "{ referenceCatalogues { sourceId } }"}).json()["data"] == {
        "referenceCatalogues": []
    }
