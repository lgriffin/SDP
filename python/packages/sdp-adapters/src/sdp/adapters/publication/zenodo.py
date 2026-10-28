"""ZenodoPublisher: the BundlePublisher on Zenodo's deposition REST API.

Create a deposition, upload the bundle into its bucket (checking Zenodo's MD5 against ours), set
the metadata, publish, and return the minted DOI. The default service is the Zenodo sandbox, whose
DOIs are test DOIs; the production URL is a deliberate setting because a published Zenodo record
cannot be deleted. The access token comes from the environment or a mounted secret file and is
sent only in the Authorization header (NFR-009).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sdp.kernel.ports.publication import DoiRecord

SANDBOX_URL = "https://sandbox.zenodo.org"
PRODUCTION_URL = "https://zenodo.org"

#: SPDX-style licence names the kernel uses -> Zenodo licence ids
LICENCES = {"CC-BY-4.0": "cc-by-4.0", "CC0-1.0": "cc-zero", "MIT": "mit", "Apache-2.0": "apache-2.0"}


class PublicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body or b"{}")


class HttpClient(Protocol):
    def request(self, method: str, url: str, headers: Mapping[str, str], body: bytes = b"") -> Response: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib forwards the Authorization header on a redirect, so a redirect is an error here."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class UrllibClient:
    def __init__(self, timeout_s: float = 120.0) -> None:
        self.timeout_s = timeout_s
        self._opener = urllib.request.build_opener(_NoRedirect())

    def request(self, method: str, url: str, headers: Mapping[str, str], body: bytes = b"") -> Response:
        req = urllib.request.Request(url, data=body or None, method=method, headers=dict(headers))
        try:
            with self._opener.open(req, timeout=self.timeout_s) as r:
                return Response(r.status, r.read())
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read())


@dataclass(frozen=True)
class Creator:
    name: str  # "Family, Given"
    orcid: str | None = None
    affiliation: str | None = None

    def to_document(self) -> dict[str, str]:
        doc = {"name": self.name}
        if self.orcid:
            doc["orcid"] = self.orcid
        if self.affiliation:
            doc["affiliation"] = self.affiliation
        return doc


@dataclass
class ZenodoPublisher:
    token: str = field(repr=False)
    creators: Sequence[Creator]
    base_url: str = SANDBOX_URL
    client: HttpClient = field(default_factory=UrllibClient)
    community: str | None = None

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise PublicationError("a Zenodo access token is required")

    @property
    def sandbox(self) -> bool:
        return self.base_url.rstrip("/") == SANDBOX_URL

    def _headers(self, content_type: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": content_type, "Accept": "application/json"}

    def _origin(self, url: str) -> tuple[str, str]:
        parts = urllib.parse.urlsplit(url)
        return parts.scheme, parts.netloc.lower()

    def _call(self, method: str, url: str, body: bytes, content_type: str, ok: tuple[int, ...]) -> Any:
        # the token goes only to the configured service, whatever a response says the next URL is
        if self._origin(url) != self._origin(self.base_url) or self._origin(url)[0] != "https":
            raise PublicationError(f"refusing to send the Zenodo token to {self._origin(url)[1] or url!r}")
        try:
            response = self.client.request(method, url, self._headers(content_type), body)
        except (OSError, urllib.error.URLError) as exc:
            raise PublicationError(f"Zenodo {method} {urllib.parse.urlsplit(url).path}: {exc}") from exc
        if response.status not in ok:
            detail = response.body[:500].decode("utf-8", "replace").replace(self.token, "[token]")
            raise PublicationError(
                f"Zenodo {method} {urllib.parse.urlsplit(url).path}: HTTP {response.status}: {detail}"
            )
        return response.json()

    def metadata(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        licence = str(metadata.get("license", "CC-BY-4.0"))
        doc: dict[str, Any] = {
            "upload_type": metadata.get("upload_type", "dataset"),
            "title": metadata["title"],
            "description": metadata.get(
                "description", "Evidence bundle (RO-Crate) from the Scientific Discovery Platform."
            ),
            "creators": [c.to_document() for c in self.creators],
            "access_right": "open",
            "license": LICENCES.get(licence, licence.lower()),
        }
        if self.community:
            doc["communities"] = [{"identifier": self.community}]
        for key in ("keywords", "related_identifiers", "version"):
            if key in metadata:
                doc[key] = metadata[key]
        return doc

    def publish(self, bundle: bytes, filename: str, metadata: Mapping[str, Any]) -> DoiRecord:
        if not self.creators:
            raise PublicationError("Zenodo needs at least one creator; set SDP_ZENODO_CREATORS")
        base = self.base_url.rstrip("/")
        deposition = self._call("POST", f"{base}/api/deposit/depositions", b"{}", "application/json", (201,))
        dep_id = str(deposition["id"])
        dep_url = f"{base}/api/deposit/depositions/{dep_id}"
        try:
            bucket = deposition["links"]["bucket"]
            uploaded = self._call(
                "PUT", f"{bucket}/{urllib.parse.quote(filename)}", bundle, "application/octet-stream", (200, 201)
            )
            expected = "md5:" + hashlib.md5(bundle, usedforsecurity=False).hexdigest()
            if uploaded.get("checksum") != expected:
                raise PublicationError(f"Zenodo stored {uploaded.get('checksum')} for {filename}, expected {expected}")
            body = json.dumps({"metadata": self.metadata(metadata)}).encode()
            self._call("PUT", dep_url, body, "application/json", (200,))
        except PublicationError as exc:
            # nothing is published yet, so the draft goes rather than lingering as an orphan
            with contextlib.suppress(PublicationError):
                self._call("DELETE", dep_url, b"", "application/json", (201, 204))
            raise PublicationError(f"deposition {dep_id}: {exc}") from exc
        try:
            published = self._call("POST", f"{dep_url}/actions/publish", b"", "application/json", (202,))
        except PublicationError as exc:
            # a lost reply may hide a publication that happened: ask before anyone retries and mints a second DOI
            state = self._call("GET", dep_url, b"", "application/json", (200,))
            if not (state.get("submitted") and state.get("doi")):
                raise PublicationError(f"deposition {dep_id} is a draft and was not published: {exc}") from exc
            published = state
        doi = str(published.get("doi") or "")
        if not doi:
            raise PublicationError(f"Zenodo published deposition {dep_id} without a DOI")
        url = str(published.get("doi_url") or f"https://doi.org/{doi}")
        return DoiRecord(doi=doi, url=url, deposition_id=dep_id)


def creators_from(text: str) -> list[Creator]:
    """``"Family, Given|0000-0002-...; Other, Name"``: creators separated by ``;``, ORCID after ``|``."""
    out = []
    for item in (part.strip() for part in text.split(";")):
        if not item:
            continue
        name, _, orcid = item.partition("|")
        out.append(Creator(name.strip(), orcid.strip() or None))
    return out
