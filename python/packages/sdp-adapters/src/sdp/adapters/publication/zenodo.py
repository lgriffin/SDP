"""ZenodoPublisher: the BundlePublisher on Zenodo's deposition REST API.

Create a deposition, upload the bundle into its bucket (checking Zenodo's MD5 against ours), set
the metadata, publish, and return the minted DOI. The default service is the Zenodo sandbox, whose
DOIs are test DOIs; the production URL is a deliberate setting because a published Zenodo record
cannot be deleted. The access token comes from the environment or a mounted secret file and is
sent only in the Authorization header (NFR-009).
"""

from __future__ import annotations

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


class UrllibClient:
    def __init__(self, timeout_s: float = 120.0) -> None:
        self.timeout_s = timeout_s

    def request(self, method: str, url: str, headers: Mapping[str, str], body: bytes = b"") -> Response:
        req = urllib.request.Request(url, data=body or None, method=method, headers=dict(headers))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
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
        if not self.creators:
            raise PublicationError("Zenodo needs at least one creator; set SDP_ZENODO_CREATORS")

    @property
    def sandbox(self) -> bool:
        return self.base_url.rstrip("/") == SANDBOX_URL

    def _headers(self, content_type: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": content_type, "Accept": "application/json"}

    def _call(self, method: str, url: str, body: bytes, content_type: str, ok: tuple[int, ...]) -> Any:
        response = self.client.request(method, url, self._headers(content_type), body)
        if response.status not in ok:
            detail = response.body[:500].decode("utf-8", "replace")
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
        base = self.base_url.rstrip("/")
        deposition = self._call("POST", f"{base}/api/deposit/depositions", b"{}", "application/json", (201,))
        dep_id = str(deposition["id"])
        bucket = deposition["links"]["bucket"]
        uploaded = self._call(
            "PUT", f"{bucket}/{urllib.parse.quote(filename)}", bundle, "application/octet-stream", (200, 201)
        )
        expected = "md5:" + hashlib.md5(bundle, usedforsecurity=False).hexdigest()
        if uploaded.get("checksum") != expected:
            raise PublicationError(f"Zenodo stored {uploaded.get('checksum')} for {filename}, expected {expected}")
        body = json.dumps({"metadata": self.metadata(metadata)}).encode()
        self._call("PUT", f"{base}/api/deposit/depositions/{dep_id}", body, "application/json", (200,))
        published = self._call(
            "POST", f"{base}/api/deposit/depositions/{dep_id}/actions/publish", b"", "application/json", (202,)
        )
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
