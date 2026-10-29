"""T4 for the long commands sdp-api runs as jobs: a plan's execution (INV-018) and registering the
archives a new installation plans against (INV-019)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from starlette.testclient import TestClient
from tests.api.test_api_plan_commands import PLAN, body, graphql, k  # noqa: F401  (k is a fixture)

from sdp.api.app import create_app
from sdp.api.jobs import FAILED, FINISHED, RUNNING, Job, JobBusy, Jobs
from sdp.testing.wiring import InMemoryKernel

pytestmark = pytest.mark.tier("T4")
T0 = datetime(2026, 10, 29, 22, 0, tzinfo=UTC)
RUN = "run"


class Held:
    """A spawn that keeps the work until the test lets it run, so a running job can be observed."""

    def __init__(self) -> None:
        self.waiting: list[Callable[[], None]] = []

    def __call__(self, work: Callable[[], None]) -> None:
        self.waiting.append(work)

    def release(self) -> None:
        work, self.waiting = self.waiting, []
        for w in work:
            w()


def clock() -> Callable[[], datetime]:
    ticks = iter(range(1000))
    return lambda: T0.replace(minute=next(ticks))


class FakeArchives:
    def __init__(self, fail: bool = False) -> None:
        self.sources: list[dict[str, Any]] = []
        self.fail = fail
        self.calls = 0

    def registered(self) -> list[dict[str, Any]]:
        return list(self.sources)

    def register(self) -> list[dict[str, Any]]:
        self.calls += 1
        if self.fail:
            raise TimeoutError("MAST did not answer")
        self.sources = [
            {
                "source_id": "gaia",
                "origin": "hand-written",
                "verified_on": "2026-10-29",
                "product_types": ["catalogue"],
            },
            {"source_id": "mast", "origin": "registry", "verified_on": "2026-10-29", "product_types": ["image"]},
        ]
        return self.sources


def approved_plan(client: TestClient) -> str:
    plan_id: str = client.post("/plans", json=body(actor="leigh")).json()["plan_id"]
    assert client.post(f"/plans/{plan_id}/approve", json={"actor": "leigh"}).status_code == 200
    return plan_id


def plan_run(client: TestClient, plan_id: str) -> Any:
    return graphql(client, f"query P($id: ID!) {{ plan(id: $id) {{ {PLAN} run }} }}", id=plan_id)["plan"]


@pytest.mark.req("INV-018")
def test_a_background_execution_answers_at_once_and_the_plan_reports_it_running_then_finished(
    k: InMemoryKernel,  # noqa: F811
) -> None:
    held = Held()
    client = TestClient(create_app(k, jobs=Jobs(held, clock())))
    plan_id = approved_plan(client)
    assert plan_run(client, plan_id)["run"] is None

    started = client.post(f"/plans/{plan_id}/execute", json={"background": True, "actor": "leigh"})
    assert started.status_code == 202, started.text
    assert started.json()["state"] == "auto_approved" and started.json()["execution"] is None
    assert started.json()[RUN] == {
        "state": RUNNING,
        "started_at": "2026-10-29T22:00:00+00:00",
        "finished_at": None,
        "error": None,
    }
    # Asking again while it runs starts nothing new.
    again = client.post(f"/plans/{plan_id}/execute", json={"background": True})
    assert again.status_code == 200 and again.json()[RUN]["started_at"] == "2026-10-29T22:00:00+00:00"
    assert len(held.waiting) == 1
    assert plan_run(client, plan_id)["run"]["state"] == RUNNING

    held.release()
    plan = plan_run(client, plan_id)
    assert plan["state"] == "executed" and plan["setId"] is not None
    assert plan["run"] == {
        "state": FINISHED,
        "started_at": "2026-10-29T22:00:00+00:00",
        "finished_at": "2026-10-29T22:01:00+00:00",
        "error": None,
    }
    [listed] = graphql(client, "{ plans { id run } }")["plans"]
    assert listed["run"]["state"] == FINISHED
    # An executed plan is not run twice, in the background or not.
    assert client.post(f"/plans/{plan_id}/execute", json={"background": True}).status_code == 409


@pytest.mark.req("INV-018")
def test_a_background_execution_that_fails_reports_the_error_and_can_be_started_again(
    k: InMemoryKernel,  # noqa: F811
) -> None:
    held = Held()
    client = TestClient(create_app(k, jobs=Jobs(held, clock())))
    plan_id = approved_plan(client)
    real = k.kernel.execute_plan
    assert real is not None

    class Broken:
        def execute(self, command: Any) -> Any:
            raise ConnectionError("MAST answered 504")

    k.kernel.execute_plan = Broken()  # type: ignore[assignment]
    assert client.post(f"/plans/{plan_id}/execute", json={"background": True}).status_code == 202
    held.release()
    failed = plan_run(client, plan_id)
    assert failed["state"] == "auto_approved"
    assert failed["run"]["state"] == FAILED and failed["run"]["error"] == "ConnectionError: MAST answered 504"

    k.kernel.execute_plan = real
    assert client.post(f"/plans/{plan_id}/execute", json={"background": True}).status_code == 202
    held.release()
    assert plan_run(client, plan_id)["run"]["state"] == FINISHED


@pytest.mark.req("INV-018")
def test_only_an_approved_plan_starts_in_the_background(k: InMemoryKernel) -> None:  # noqa: F811
    held = Held()
    client = TestClient(create_app(k, jobs=Jobs(held, clock())))
    plan_id = client.post("/plans", json=body()).json()["plan_id"]
    drafted = client.post(f"/plans/{plan_id}/execute", json={"background": True})
    assert drafted.status_code == 409
    assert drafted.json()["error"] == f"plan {plan_id} is drafted; only an approved plan runs"
    missing = "sha256:" + "0" * 64
    assert client.post(f"/plans/{missing}/execute", json={"background": True}).status_code == 404
    for wrong in ("yes", 1, None):
        response = client.post(f"/plans/{plan_id}/execute", json={"background": wrong})
        assert response.status_code == 400 and "background is true or false" in response.json()["error"]
    assert held.waiting == []


@pytest.mark.req("INV-019")
def test_the_archives_are_listed_and_registered_as_a_job(k: InMemoryKernel) -> None:  # noqa: F811
    held = Held()
    archives = FakeArchives()
    client = TestClient(create_app(k, archives=archives, jobs=Jobs(held, clock())))
    assert client.get("/archives").json() == {"sources": [], "registration": None}
    query = "{ archives { sources { sourceId origin verifiedOn productTypes } registration } }"
    assert graphql(client, query)["archives"] == {"sources": [], "registration": None}

    started = client.post("/archives/register")
    assert started.status_code == 202 and started.json()["registration"]["state"] == RUNNING
    assert client.post("/archives/register").status_code == 200  # already running: nothing new starts
    held.release()
    assert archives.calls == 1
    listed = graphql(client, query)["archives"]
    assert listed["registration"]["state"] == FINISHED
    assert listed["sources"] == [
        {"sourceId": "gaia", "origin": "hand-written", "verifiedOn": "2026-10-29", "productTypes": ["catalogue"]},
        {"sourceId": "mast", "origin": "registry", "verifiedOn": "2026-10-29", "productTypes": ["image"]},
    ]


@pytest.mark.req("INV-019")
def test_a_failed_registration_says_why_and_a_server_without_archives_says_so(k: InMemoryKernel) -> None:  # noqa: F811
    client = TestClient(create_app(k, archives=FakeArchives(fail=True), jobs=Jobs(lambda w: w(), clock())))
    assert client.post("/archives/register").status_code == 202
    registration = client.get("/archives").json()["registration"]
    assert registration["state"] == FAILED and registration["error"] == "TimeoutError: MAST did not answer"

    bare = TestClient(create_app(k))
    assert graphql(bare, "{ archives { sources { sourceId } } }")["archives"] is None
    for response in (bare.get("/archives"), bare.post("/archives/register")):
        assert response.status_code == 503
        assert response.json() == {"error": "archive registration is not wired on this server"}


def test_a_job_document_and_a_real_thread() -> None:
    assert Job("k", T0).to_document() == {
        "state": RUNNING,
        "started_at": "2026-10-29T22:00:00+00:00",
        "finished_at": None,
        "error": None,
    }
    jobs = Jobs()
    done: list[int] = []
    job, started = jobs.start("k", lambda: done.append(1))
    assert started and jobs.get("k") is job and jobs.get("other") is None
    for _ in range(500):
        if job.state != RUNNING:
            break
        time.sleep(0.01)
    assert done == [1] and job.state == FINISHED and job.finished_at is not None


@pytest.mark.req("INV-018")
def test_one_field_run_at_a_time_so_a_download_keeps_its_plans_gate(k: InMemoryKernel) -> None:  # noqa: F811
    held = Held()
    client = TestClient(create_app(k, jobs=Jobs(held, clock())))
    plan_id = approved_plan(client)
    assert client.post(f"/plans/{plan_id}/execute", json={"background": True}).status_code == 202
    other = client.post("/investigations", json=body())
    assert other.status_code == 409 and other.json()["type"] == "RunInProgress", other.text
    again = client.post(f"/plans/{plan_id}/execute", json={})
    assert again.status_code == 200 and again.json()["run"]["state"] == RUNNING, "no second download of the plan"
    assert len(held.waiting) == 1
    held.release()
    assert plan_run(client, plan_id)["state"] == "executed"
    ran = client.post("/investigations", json=body())
    assert ran.status_code == 200, "the run let go of the server when it finished"


@pytest.mark.req("INV-018")
def test_a_failed_background_run_lets_the_next_run_start(k: InMemoryKernel) -> None:  # noqa: F811
    held = Held()
    client = TestClient(create_app(k, jobs=Jobs(held, clock())))
    plan_id = approved_plan(client)
    real = k.kernel.execute_plan

    class Broken:
        def execute(self, command: Any) -> Any:
            raise ConnectionError("MAST answered 504")

    k.kernel.execute_plan = Broken()  # type: ignore[assignment]
    client.post(f"/plans/{plan_id}/execute", json={"background": True})
    held.release()
    k.kernel.execute_plan = real
    assert client.post("/investigations", json=body()).status_code == 200


def test_a_kind_that_runs_alone_refuses_a_second_key_while_one_runs() -> None:
    held = Held()
    jobs = Jobs(spawn=held, now=clock())
    first, started = jobs.start("kind:a", lambda: 1, alone_among="kind:")
    assert started and jobs.start("kind:a", lambda: 1, alone_among="kind:") == (first, False)
    with pytest.raises(JobBusy, match="kind:a is running") as busy:
        jobs.start("kind:b", lambda: 2, alone_among="kind:")
    assert busy.value.running == "kind:a" and jobs.get("kind:b") is None
    assert jobs.start("other:c", lambda: 3, alone_among="other:")[1], "another kind is not held up"
    held.release()
    assert jobs.start("kind:b", lambda: 2, alone_among="kind:")[1]
