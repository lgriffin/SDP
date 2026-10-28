"""ArchiveGateway, DialectCurator, HistoricalSourceGateway and CrossMatchService (§6, §8)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

import astropy.units as u
import numpy as np
import pyarrow as pa

from sdp.kernel.domain.archive import AcquisitionHandle, CatalogueQuery, SourceDescriptor
from sdp.kernel.domain.dataset import DialectRef
from sdp.kernel.domain.historical import HistoricalDocument, TranscribedRecord, VerificationRecord


class ArchiveGateway(Protocol):
    """Describe, query and fetch from one remote archive."""

    def describe(self, source_id: str) -> SourceDescriptor:
        """Release label, column list, licence, citation, rate limits, max rows per call."""
        ...

    def query(self, q: CatalogueQuery) -> AcquisitionHandle:
        """Render the bounded query; returns a resumable handle carrying the rendered text."""
        ...

    def fetch(self, handle: AcquisitionHandle) -> Iterator[pa.RecordBatch]:
        """pyarrow batches in the source's native columns, one per chunk, starting at
        ``handle.resume_from_part``; idempotent per chunk."""
        ...


class DialectCurator(Protocol):
    """Applies a source release's dialect: native columns to the canonical astrometry schema."""

    def dialect_for(self, source_id: str, release_label: str) -> DialectRef: ...

    def curate(self, native: pa.Table, dialect: DialectRef) -> pa.Table: ...


class HistoricalSourceGateway(Protocol):
    """Register documents, serve pages, hold transcriptions and verification passes."""

    def register(self, document: HistoricalDocument, content: bytes | None = None) -> None: ...

    def document(self, document_id: str) -> HistoricalDocument | None: ...

    def page(self, document_id: str, page: int) -> bytes | None: ...

    def add_transcription(self, record: TranscribedRecord) -> None: ...

    def add_verification(self, record: VerificationRecord) -> None: ...

    def transcriptions(self, document_id: str | None = None) -> Sequence[TranscribedRecord]: ...

    def verifications(self, record_id: str | None = None) -> Sequence[VerificationRecord]: ...


@dataclass(frozen=True)
class CrossMatchResult:
    """For each left row with a match within the radius: its index, the right index and separation."""

    left_index: np.ndarray
    right_index: np.ndarray
    separation: u.Quantity


class CrossMatchService(Protocol):
    def match(
        self, left: pa.Table, right: pa.Table, radius: u.Quantity, *, epoch: u.Quantity | None = None
    ) -> CrossMatchResult:
        """Positional cross-match of two canonical frames. When ``epoch`` is given, both sides are
        propagated to it with their proper motions before matching."""
        ...
