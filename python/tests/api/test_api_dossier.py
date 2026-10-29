"""T4 for saving a field investigation's evidence dossier from the app (INV-021) and checking that it
reproduces (INV-022), over the synthetic JWST slice."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from starlette.testclient import TestClient
from tests.usecase.test_jwst_slice import run_slice, slice_kernel

from sdp.api.app import create_app
from sdp.api.jobs import Jobs, reproduce_job
from sdp.kernel.application.rocrate import read_crate, verify_crate
from sdp.kernel.domain.investigation import Stage, StageState
from sdp.testing.wiring import build_in_memory_kernel

pytestmark = pytest.mark.tier("T4")


@pytest.mark.req("INV-021")
@pytest.mark.req("INV-022")
def test_the_app_downloads_a_sets_dossier_and_checks_it_reproduces() -> None:
    k = slice_kernel()
    _, run = run_slice(k)
    set_id = run["set"].set_id
    spawned: list[Callable[[], None]] = []
    client = TestClient(create_app(k, jobs=Jobs(spawn=spawned.append)))

    saved = client.get(f"/investigations/{set_id}/dossier.zip")
    assert saved.status_code == 200 and saved.headers["content-type"] == "application/zip"
    assert saved.headers["content-disposition"] == f'attachment; filename="{set_id}-dossier.zip"'
    assert verify_crate(saved.content) == [] and "stages/test.json" in read_crate(saved.content)[1]

    assert client.get(f"/investigations/{set_id}/reproduce").json() == {"reproduction": None}
    started = client.post(f"/investigations/{set_id}/reproduce")
    assert started.status_code == 202 and started.json()["reproduction"]["state"] == "running"
    again = client.post(f"/investigations/{set_id}/reproduce")
    assert again.status_code == 200 and len(spawned) == 1, "a running check is not started twice"
    spawned.pop()()
    run = client.get(f"/investigations/{set_id}/reproduce").json()["reproduction"]
    assert run["state"] == "finished" and run["result"]["ok"] and run["result"]["results_compared"] == 12

    jobs = Jobs(spawn=spawned.append)
    busy = TestClient(create_app(k, jobs=jobs))
    jobs.start(reproduce_job("invset-other"), lambda: None)
    refused = busy.post(f"/investigations/{set_id}/reproduce")
    assert refused.status_code == 409 and "invset-other is being reproduced" in refused.json()["error"]
    assert busy.get(f"/investigations/{set_id}/reproduce").json() == {"reproduction": None}, "nothing started"
    spawned.pop()()
    assert busy.post(f"/investigations/{set_id}/reproduce").status_code == 202, "free again once it finished"

    assert client.get("/investigations/invset-missing/dossier.zip").status_code == 404
    assert client.post("/investigations/invset-missing/reproduce").status_code == 404
    assert client.get("/investigations/invset-missing/reproduce").status_code == 404


def test_a_set_whose_test_stage_never_ran_cannot_be_reproduced() -> None:
    k = slice_kernel()
    _, run = run_slice(k)
    s = run["set"]
    s.stages[Stage.TEST].state = StageState.FAILED
    with k.db.unit_of_work() as uow:
        uow.investigations.save_set(s)
        uow.commit()
    client = TestClient(create_app(k, jobs=Jobs(spawn=lambda work: work())))
    assert client.post(f"/investigations/{s.set_id}/reproduce").status_code == 202
    run = client.get(f"/investigations/{s.set_id}/reproduce").json()["reproduction"]
    assert run["state"] == "failed" and "no stages/test.json" in run["error"] and "result" not in run


def test_without_investigate_the_dossier_is_unavailable() -> None:
    client = TestClient(create_app(build_in_memory_kernel()))
    assert client.get("/investigations/x/dossier.zip").status_code == 503
    assert client.post("/investigations/x/reproduce").status_code == 503
