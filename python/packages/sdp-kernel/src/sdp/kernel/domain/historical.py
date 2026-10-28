"""Historical sources: documents, transcriptions and second-pass verifications (§9)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.sources import SourceKind, SourceRef


@dataclass(frozen=True)
class HistoricalDocument:
    document_id: str
    source: SourceRef
    title: str
    pages: int | None = None

    def __post_init__(self) -> None:
        if self.source.kind is not SourceKind.DOCUMENT:
            raise InvariantViolation("historical documents have a document SourceRef")


@dataclass(frozen=True)
class TranscribedRecord:
    """One row transcribed from a page or table, exactly as printed."""

    record_id: str
    document_id: str
    locator: str
    fields: Mapping[str, Any]
    transcriber: str
    transcribed_at: datetime
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.locator.strip():
            raise InvariantViolation(f"record {self.record_id} needs a page or table locator (REL-001)")


@dataclass(frozen=True)
class VerificationRecord:
    """A second pass by someone other than the transcriber, against the same locator."""

    record_id: str
    verifier: str
    verified_at: datetime
    matches: bool
    corrections: Mapping[str, Any] = field(default_factory=dict)
    notes: str = ""


def is_verified(record: TranscribedRecord, verifications: tuple[VerificationRecord, ...]) -> bool:
    """A record is verified when an independent second pass found it matching."""
    return any(
        v.record_id == record.record_id and v.matches and v.verifier != record.transcriber for v in verifications
    )
