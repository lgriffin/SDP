"""T3: the Zenodo publisher against a fake of the deposition REST API, and its settings (NFR-009)."""

from __future__ import annotations

import hashlib
import http.client
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from sdp.adapters.bootstrap import UnconfiguredPublisher, publisher
from sdp.adapters.publication.zenodo import (
    Creator,
    PublicationError,
    PublicationOutcomeUnknown,
    Response,
    ZenodoPublisher,
    creators_from,
)
from sdp.adapters.settings import Settings
from sdp.testing.contracts.misc import PublisherContract

pytestmark = pytest.mark.tier("T3")

BASE = "https://sandbox.zenodo.org"
TOKEN = "s3cret-token"


@dataclass
class FakeZenodo:
    """The four calls of a deposition, with Zenodo's status codes and response shapes."""

    corrupt_upload: bool = False
    bucket: str = f"{BASE}/api/files/b-1"
    echo_auth: bool = False
    lose_publish_reply: bool = False
    fail_status_check: bool = False
    publish_body: bytes | None = None
    deleted: bool = False
    calls: list[tuple[str, str]] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    published: bool = False

    def request(self, method: str, url: str, headers: Mapping[str, str], body: bytes = b"") -> Response:
        self.calls.append((method, url))
        if headers.get("Authorization") != f"Bearer {TOKEN}" or self.echo_auth:
            return Response(401, f'{{"message": "not authorized: {headers.get("Authorization")}"}}'.encode())
        if method == "POST" and url == f"{BASE}/api/deposit/depositions":
            return Response(201, json.dumps({"id": 4242, "links": {"bucket": self.bucket}}).encode())
        if method == "DELETE" and url == f"{BASE}/api/deposit/depositions/4242":
            self.deleted = True
            return Response(204, b"")
        if method == "GET" and url == f"{BASE}/api/deposit/depositions/4242" and self.fail_status_check:
            raise http.client.IncompleteRead(b"")
        if method == "GET" and url == f"{BASE}/api/deposit/depositions/4242":
            doi = "10.5072/zenodo.4242"
            return Response(200, json.dumps({"id": 4242, "submitted": self.published, "doi": doi}).encode())
        if method == "PUT" and url.startswith(f"{BASE}/api/files/b-1/"):
            name = url.rsplit("/", 1)[1]
            self.files[name] = body
            digest = hashlib.md5(body + (b"x" if self.corrupt_upload else b""), usedforsecurity=False).hexdigest()
            return Response(201, json.dumps({"key": name, "checksum": f"md5:{digest}"}).encode())
        if method == "PUT" and url == f"{BASE}/api/deposit/depositions/4242":
            meta = json.loads(body)["metadata"]
            if not meta.get("creators"):
                return Response(400, b'{"errors": [{"field": "metadata.creators"}]}')
            self.metadata = meta
            return Response(200, json.dumps({"id": 4242}).encode())
        if method == "POST" and url == f"{BASE}/api/deposit/depositions/4242/actions/publish":
            if self.publish_body is not None:
                return Response(202, self.publish_body)
            self.published = True
            if self.lose_publish_reply:
                raise TimeoutError("the read operation timed out")
            doi = "10.5072/zenodo.4242"
            return Response(202, json.dumps({"id": 4242, "doi": doi, "doi_url": f"https://doi.org/{doi}"}).encode())
        return Response(404, b"{}")


def _publisher(service: FakeZenodo, token: str = TOKEN) -> ZenodoPublisher:
    return ZenodoPublisher(token, [Creator("Griffin, Leigh")], base_url=BASE, client=service)


class TestZenodoPublisher(PublisherContract):
    @pytest.fixture
    def publisher(self) -> ZenodoPublisher:
        return _publisher(FakeZenodo())


def test_a_deposition_uploads_describes_and_publishes() -> None:
    service = FakeZenodo()
    record = _publisher(service).publish(
        b"bundle-bytes", "exp-1.zip", {"title": "SDP evidence bundle exp-1", "license": "CC-BY-4.0"}
    )
    assert record.doi == "10.5072/zenodo.4242" and record.deposition_id == "4242"
    assert service.files == {"exp-1.zip": b"bundle-bytes"} and service.published
    assert service.metadata["license"] == "cc-by-4.0" and service.metadata["upload_type"] == "dataset"
    assert service.metadata["creators"] == [{"name": "Griffin, Leigh"}]
    assert [m for m, _ in service.calls] == ["POST", "PUT", "PUT", "POST"]


def test_a_checksum_mismatch_stops_before_publication() -> None:
    service = FakeZenodo(corrupt_upload=True)
    with pytest.raises(PublicationError, match="expected md5:"):
        _publisher(service).publish(b"bundle", "b.zip", {"title": "t"})
    assert not service.published


def test_an_http_error_names_the_call_and_never_the_token() -> None:
    with pytest.raises(PublicationError, match="HTTP 401") as info:
        _publisher(FakeZenodo(), token="wrong-token").publish(b"b", "b.zip", {"title": "t"})
    assert "wrong-token" not in str(info.value)


@pytest.mark.req("NFR-009")
def test_a_bucket_on_another_host_never_receives_the_token_and_the_draft_is_deleted() -> None:
    service = FakeZenodo(bucket="https://elsewhere.example/api/files/b-1")
    with pytest.raises(PublicationError, match=r"refusing to send the Zenodo token to 'elsewhere\.example'"):
        _publisher(service).publish(b"bundle", "b.zip", {"title": "t"})
    assert not any("elsewhere" in url for _, url in service.calls)
    assert service.deleted and not service.published


@pytest.mark.req("NFR-009")
def test_an_error_body_that_echoes_the_token_is_redacted() -> None:
    with pytest.raises(PublicationError, match=r"\[token\]") as info:
        _publisher(FakeZenodo(echo_auth=True)).publish(b"b", "b.zip", {"title": "t"})
    assert TOKEN not in str(info.value)


def test_a_lost_reply_to_publish_returns_the_doi_zenodo_minted() -> None:
    service = FakeZenodo(lose_publish_reply=True)
    record = _publisher(service).publish(b"b", "b.zip", {"title": "t"})
    assert record.doi == "10.5072/zenodo.4242" and service.calls[-1][0] == "GET"


def test_when_the_state_cannot_be_read_after_a_lost_reply_the_outcome_is_unknown() -> None:
    service = FakeZenodo(lose_publish_reply=True, fail_status_check=True)
    with pytest.raises(PublicationOutcomeUnknown, match=r"deposition 4242.*before retrying"):
        _publisher(service).publish(b"b", "b.zip", {"title": "t"})


def test_a_reply_that_is_not_json_does_not_count_as_a_failed_publication() -> None:
    # a proxy page in place of Zenodo's reply: the deposition is still a draft, but may not stay one
    service = FakeZenodo(publish_body=b"<html>gateway</html>")
    with pytest.raises(PublicationOutcomeUnknown, match="not JSON"):
        _publisher(service).publish(b"b", "b.zip", {"title": "t"})


@pytest.mark.req("NFR-009")
def test_the_token_never_appears_in_a_repr() -> None:
    assert TOKEN not in repr(_publisher(FakeZenodo()))
    settings = Settings.from_env(
        {"SDP_DATABASE_URL": "postgresql://x", "SDP_ARTIFACT_ROOT": "/tmp", "SDP_ZENODO_TOKEN": TOKEN}
    )
    assert TOKEN not in repr(settings) and settings.zenodo_token == TOKEN


@pytest.mark.req("NFR-009")
def test_credentials_can_come_from_a_mounted_secret_file(tmp_path: Path) -> None:
    secret = tmp_path / "zenodo_token"
    secret.write_text(TOKEN + "\n")
    settings = Settings.from_env(
        {"SDP_DATABASE_URL": "postgresql://x", "SDP_ARTIFACT_ROOT": "/tmp", "SDP_ZENODO_TOKEN_FILE": str(secret)}
    )
    assert settings.zenodo_token == TOKEN


@pytest.mark.req("NFR-009")
def test_the_database_url_can_come_from_a_secret_file_and_stays_out_of_reprs(tmp_path: Path) -> None:
    url = "postgresql://sdp:not-a-real-password@db/sdp"
    secret = tmp_path / "database_url"
    secret.write_text(url + "\n")
    settings = Settings.from_env({"SDP_DATABASE_URL_FILE": str(secret), "SDP_ARTIFACT_ROOT": "/tmp"})
    assert settings.database_url == url and "not-a-real-password" not in repr(settings)


def test_the_composition_root_refuses_without_a_token_and_defaults_to_the_sandbox() -> None:
    env = {"SDP_DATABASE_URL": "postgresql://x", "SDP_ARTIFACT_ROOT": "/tmp"}
    assert isinstance(publisher(Settings.from_env(env)), UnconfiguredPublisher)
    # a missing creator list fails publication, not every command that builds the composition root
    without_creators = publisher(Settings.from_env({**env, "SDP_ZENODO_TOKEN": TOKEN}))
    with pytest.raises(PublicationError, match="SDP_ZENODO_CREATORS"):
        without_creators.publish(b"b", "b.zip", {"title": "t"})
    configured = publisher(
        Settings.from_env({**env, "SDP_ZENODO_TOKEN": TOKEN, "SDP_ZENODO_CREATORS": "Griffin, Leigh"})
    )
    assert isinstance(configured, ZenodoPublisher) and configured.sandbox


def test_creators_parse_names_and_orcids() -> None:
    assert creators_from("Griffin, Leigh|0000-0000-0000-0000; Other, A") == [
        Creator("Griffin, Leigh", "0000-0000-0000-0000"),
        Creator("Other, A"),
    ]
