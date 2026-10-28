"""SourceRef: where a number came from."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sdp.kernel.domain.errors import InvariantViolation

_RELEASE = re.compile(r"^[a-z0-9]+:[A-Za-z0-9._-]+$")


class SourceKind(StrEnum):
    ARCHIVE = "archive"
    DOCUMENT = "document"
    LOCAL = "local"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True)
class SourceRef:
    """Archive or document identity, release label, query text or DOI, licence and citation.

    Invariant: a release label is mandatory for archives, and a DOI (or, for works that have
    none, a bibcode) for documents.
    """

    kind: SourceKind
    identity: str
    licence: str
    citation: str
    release_label: str | None = None
    query_text: str | None = None
    doi: str | None = None
    bibcode: str | None = None

    def __post_init__(self) -> None:
        if not self.identity:
            raise InvariantViolation("a source needs an identity")
        if self.release_label is not None and not _RELEASE.match(self.release_label):
            raise InvariantViolation(f"release label must look like 'gaia:DR3', got {self.release_label!r}")
        if self.kind is SourceKind.ARCHIVE and not self.release_label:
            raise InvariantViolation(f"archive source {self.identity!r} must name its release")
        if self.kind is SourceKind.DOCUMENT and not (self.doi or self.bibcode):
            raise InvariantViolation(f"document {self.identity!r} must carry a DOI or bibcode")

    @property
    def has_licence(self) -> bool:
        return bool(self.licence and self.licence.strip())

    def with_query(self, query_text: str) -> SourceRef:
        return SourceRef(
            kind=self.kind,
            identity=self.identity,
            licence=self.licence,
            citation=self.citation,
            release_label=self.release_label,
            query_text=query_text,
            doi=self.doi,
            bibcode=self.bibcode,
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "identity": self.identity,
            "release_label": self.release_label,
            "query_text": self.query_text,
            "doi": self.doi,
            "bibcode": self.bibcode,
            "licence": self.licence,
            "citation": self.citation,
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> SourceRef:
        return cls(
            kind=SourceKind(doc["kind"]),
            identity=doc["identity"],
            licence=doc["licence"],
            citation=doc["citation"],
            release_label=doc.get("release_label"),
            query_text=doc.get("query_text"),
            doi=doc.get("doi"),
            bibcode=doc.get("bibcode"),
        )
