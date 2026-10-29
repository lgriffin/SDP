"""HTTP transport for the archive adapters, and the cassettes that replace it in CI (ADR-015, DSI-007).

Every archive adapter talks HTTP only through a ``Transport``. Production uses ``UrllibTransport``
behind ``PoliteTransport`` (the source's rate limit and bounded exponential backoff, DSI-003); tests
and CI use ``CassetteTransport`` in ``replay`` mode, which answers from committed recordings and
raises ``CassetteMissing`` rather than touch the network. ``record`` mode calls the inner transport
and writes what it got, which is how the weekly live job refreshes the cassettes.

A cassette is ``<dir>/<sha256 of method, url and body>.json``: the request, the response, when it
was recorded and by what.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

USER_AGENT = "sdp-archives/0.1 (+https://github.com/lgriffin/SDP)"


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)

    def key(self) -> str:
        """Stable across runs: headers other than the content type and a byte range do not change the
        answer. A request without a range keys as it always has, so existing recordings still match."""
        h = hashlib.sha256()
        for part in (self.method.upper(), self.url, self.headers.get("Content-Type", "")):
            h.update(part.encode())
            h.update(b"\0")
        h.update(self.body)
        if self.headers.get("Range"):
            h.update(b"\0range\0" + self.headers["Range"].encode())
        return h.hexdigest()


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class TransportError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class CassetteMissing(TransportError):
    """Replay mode found no recording for a request; the adapter never falls back to the network."""


class Transport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...


@dataclass
class UrllibTransport:
    timeout_s: float = 120.0
    user_agent: str = USER_AGENT

    def send(self, request: HttpRequest) -> HttpResponse:
        req = urllib.request.Request(
            request.url,
            data=request.body or None,
            method=request.method.upper(),
            headers={"User-Agent": self.user_agent, **request.headers},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read()
                declared = resp.headers.get("Content-Length")
                if declared is not None and int(declared) != len(body):
                    raise TransportError(f"truncated response: {len(body)} of {declared} bytes")
                return HttpResponse(resp.status, body, dict(resp.headers.items()))
        except urllib.error.HTTPError as exc:
            return HttpResponse(exc.code, exc.read(), dict(exc.headers.items()))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise TransportError(f"{request.method} {request.url}: {exc}") from exc


RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})


@dataclass
class PoliteTransport:
    """Honours the archive's minimum interval between calls and retries transient failures with
    bounded exponential backoff; it never retries a 4xx other than 408 and 429."""

    inner: Transport
    min_seconds_between_calls: float = 1.0
    retries: int = 4
    backoff_s: float = 2.0
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    _last: float | None = field(default=None, init=False)

    def send(self, request: HttpRequest) -> HttpResponse:
        attempt = 0
        while True:
            if self._last is not None:
                wait = self.min_seconds_between_calls - (self.monotonic() - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.monotonic()
            try:
                response = self.inner.send(request)
            except CassetteMissing:
                raise
            except TransportError:
                if attempt >= self.retries:
                    raise
            else:
                if response.status not in RETRYABLE or attempt >= self.retries:
                    return response
            self.sleep(self.backoff_s * 2**attempt)
            attempt += 1


Mode = Literal["replay", "record"]


@dataclass
class CassetteTransport:
    directory: Path
    mode: Mode = "replay"
    inner: Transport | None = None
    recorder: str = "sdp"
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)
        if self.mode == "record" and self.inner is None:
            raise ValueError("record mode needs an inner transport")

    def path(self, request: HttpRequest) -> Path:
        return self.directory / f"{request.key()}.json"

    def send(self, request: HttpRequest) -> HttpResponse:
        path = self.path(request)
        if self.mode == "replay":
            if not path.exists():
                raise CassetteMissing(
                    f"no cassette for {request.method} {request.url} ({path.name}); "
                    "record it with SDP_ARCHIVE_MODE=record, CI never calls a live archive"
                )
            return read_cassette(path)
        assert self.inner is not None
        response = self.inner.send(request)
        if response.status == 200:
            write_cassette(path, request, response, recorded_at=self.clock(), recorder=self.recorder)
        return response


def _encode(body: bytes) -> dict[str, str]:
    try:
        return {"text": body.decode("utf-8")}
    except UnicodeDecodeError:
        return {"base64": base64.b64encode(body).decode()}


def _decode(doc: Mapping[str, str]) -> bytes:
    return doc["text"].encode("utf-8") if "text" in doc else base64.b64decode(doc["base64"])


_KEPT_HEADERS = ("Content-Type",)


def write_cassette(
    path: Path, request: HttpRequest, response: HttpResponse, *, recorded_at: datetime, recorder: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "request": {
            "method": request.method,
            "url": request.url,
            "headers": dict(request.headers),
            "body": _encode(request.body),
        },
        "response": {
            "status": response.status,
            "headers": {k: v for k, v in response.headers.items() if k in _KEPT_HEADERS},
            "body": _encode(response.body),
            "sha256": hashlib.sha256(response.body).hexdigest(),
        },
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        "recorder": recorder,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n", "utf-8")
    tmp.replace(path)


def read_cassette(path: Path) -> HttpResponse:
    doc = json.loads(path.read_text("utf-8"))
    body = _decode(doc["response"]["body"])
    if hashlib.sha256(body).hexdigest() != doc["response"]["sha256"]:
        raise TransportError(f"{path.name}: cassette body does not match its recorded checksum")
    return HttpResponse(int(doc["response"]["status"]), body, dict(doc["response"].get("headers", {})))
