"""T4 for POST /investigations, the endpoint the field viewer calls to run InvestigateField (INV-012)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from sdp.api.app import create_app
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
REGION = f"icrs;circle({FIELD_CENTRE[0]},{FIELD_CENTRE[1]},0.5)"


@pytest.fixture
def k() -> InMemoryKernel:
    catalogue = synthetic_catalogue()
    return build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=investigate_ports(catalogue=catalogue, observations=observation_archive(catalogue)),
    )


@pytest.mark.req("INV-012")
def test_a_field_runs_and_returns_candidates_with_their_sky_positions(k: InMemoryKernel) -> None:
    client = TestClient(create_app(k))
    body = {"region": REGION, "profile": synthetic_profile().to_document(), "seed": 4, "label": "viewer field"}
    response = client.post("/investigations", json=body)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["label"] == "EXPLORATORY" and run["profile_hash"] == synthetic_profile().profile_hash
    assert set(run["stages"].values()) == {"completed"}
    assert sorted(c["candidate"] for c in run["candidates"]) == sorted(PLANTED)
    for c in run["candidates"]:
        [(ra, dec)] = c["positions"]
        assert abs(ra - FIELD_CENTRE[0]) < 1 and abs(dec - FIELD_CENTRE[1]) < 1
    again = client.post("/investigations", json={**body, "resume": run["set_id"]}).json()
    assert again["set_id"] == run["set_id"]


def test_a_malformed_request_is_a_400(k: InMemoryKernel) -> None:
    client = TestClient(create_app(k))
    assert client.post("/investigations", json={"profile": {}}).status_code == 400
    bad_region = {"region": "fk4;circle(1,2,3)", "profile": synthetic_profile().to_document()}
    assert client.post("/investigations", json=bad_region).status_code == 400


def test_without_investigate_ports_the_endpoint_says_so() -> None:
    client = TestClient(create_app(build_in_memory_kernel()))
    response = client.post("/investigations", json={"region": REGION, "profile": synthetic_profile().to_document()})
    assert response.status_code == 503
