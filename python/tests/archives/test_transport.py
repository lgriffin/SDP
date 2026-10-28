"""T5: cassettes and the polite live transport (ADR-015, DSI-003, DSI-007)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sdp.adapters.archives.transport import (
    CassetteMissing,
    CassetteTransport,
    HttpRequest,
    HttpResponse,
    PoliteTransport,
    TransportError,
)

pytestmark = pytest.mark.tier("T5")

REQUEST = HttpRequest(
    "POST", "https://example.invalid/tap/sync", b"QUERY=SELECT", {"Content-Type": "application/x-www-form-urlencoded"}
)


@dataclass
class Scripted:
    replies: list[HttpResponse | Exception]
    calls: int = 0

    def send(self, request: HttpRequest) -> HttpResponse:
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        return reply


@dataclass
class FakeTime:
    now: float = 0.0
    slept: list[float] = field(default_factory=list)

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s

    def monotonic(self) -> float:
        return self.now


def test_record_then_replay_returns_the_same_bytes(tmp_path: Path) -> None:
    live = Scripted([HttpResponse(200, b"<VOTABLE/>", {"Content-Type": "text/xml", "Set-Cookie": "x"})])
    at = datetime(2026, 10, 28, tzinfo=UTC)
    CassetteTransport(tmp_path, "record", inner=live, clock=lambda: at).send(REQUEST)
    replayed = CassetteTransport(tmp_path, "replay").send(REQUEST)
    assert replayed.body == b"<VOTABLE/>" and replayed.status == 200
    doc = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert doc["recorded_at"] == "2026-10-28T00:00:00Z"
    assert "Set-Cookie" not in doc["response"]["headers"]


def test_failed_live_calls_are_not_recorded(tmp_path: Path) -> None:
    CassetteTransport(tmp_path, "record", inner=Scripted([HttpResponse(500, b"down")])).send(REQUEST)
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.req("DSI-007")
def test_a_missing_cassette_is_an_error_not_a_live_call(tmp_path: Path) -> None:
    with pytest.raises(CassetteMissing):
        CassetteTransport(tmp_path, "replay").send(REQUEST)


def test_an_edited_cassette_is_detected(tmp_path: Path) -> None:
    CassetteTransport(tmp_path, "record", inner=Scripted([HttpResponse(200, b"rows")])).send(REQUEST)
    path = next(tmp_path.glob("*.json"))
    doc = json.loads(path.read_text())
    doc["response"]["body"]["text"] = "other rows"
    path.write_text(json.dumps(doc))
    with pytest.raises(TransportError, match="checksum"):
        CassetteTransport(tmp_path, "replay").send(REQUEST)


def test_the_key_ignores_headers_other_than_content_type() -> None:
    assert (
        REQUEST.key()
        == HttpRequest(REQUEST.method, REQUEST.url, REQUEST.body, {**REQUEST.headers, "X-Trace": "1"}).key()
    )
    assert REQUEST.key() != HttpRequest(REQUEST.method, REQUEST.url, b"QUERY=SELECT 1", REQUEST.headers).key()


@pytest.mark.req("DSI-003")
def test_transient_failures_retry_with_bounded_exponential_backoff() -> None:
    t = FakeTime()
    inner = Scripted([HttpResponse(503, b""), TransportError("reset"), HttpResponse(200, b"ok")])
    polite = PoliteTransport(
        inner, min_seconds_between_calls=0.0, retries=4, backoff_s=2.0, sleep=t.sleep, monotonic=t.monotonic
    )
    assert polite.send(REQUEST).body == b"ok"
    assert t.slept == [2.0, 4.0]


@pytest.mark.req("DSI-003")
def test_retries_stop_at_the_bound() -> None:
    t = FakeTime()
    inner = Scripted([HttpResponse(503, b"")])
    polite = PoliteTransport(
        inner, min_seconds_between_calls=0.0, retries=3, backoff_s=1.0, sleep=t.sleep, monotonic=t.monotonic
    )
    assert polite.send(REQUEST).status == 503
    assert inner.calls == 4 and t.slept == [1.0, 2.0, 4.0]


def test_client_errors_are_not_retried() -> None:
    inner = Scripted([HttpResponse(400, b"bad ADQL")])
    t = FakeTime()
    assert PoliteTransport(inner, sleep=t.sleep, monotonic=t.monotonic).send(REQUEST).status == 400
    assert inner.calls == 1


def test_the_archives_minimum_interval_is_honoured() -> None:
    t = FakeTime()
    polite = PoliteTransport(
        Scripted([HttpResponse(200, b"")]), min_seconds_between_calls=1.5, sleep=t.sleep, monotonic=t.monotonic
    )
    polite.send(REQUEST)
    t.now += 0.5
    polite.send(REQUEST)
    assert t.slept == [1.0]
