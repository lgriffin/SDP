"""FakeArchive (fixture-backed), IdentityCurator, InMemoryHistoricalSources and AstropyCrossMatch."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

import astropy.units as u
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from astropy.coordinates import SkyCoord

from sdp.kernel.application.acquisition import CHECKSUM_METADATA_KEY, batch_checksum
from sdp.kernel.domain.archive import (
    AcquisitionHandle,
    Box,
    CatalogueQuery,
    Cone,
    SourceDescriptor,
    idempotency_key,
)
from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.domain.dataset import DialectRef
from sdp.kernel.domain.historical import HistoricalDocument, TranscribedRecord, VerificationRecord
from sdp.kernel.ports.archives import CrossMatchResult

_OPS = {
    "<": pc.less,
    "<=": pc.less_equal,
    ">": pc.greater,
    ">=": pc.greater_equal,
    "=": pc.equal,
    "!=": pc.not_equal,
}


def select_region(table: pa.Table, region: Cone | Box, ra: str = "ra", dec: str = "dec") -> pa.Table:
    ra_v = table.column(ra).to_numpy(zero_copy_only=False).astype(float)
    dec_v = table.column(dec).to_numpy(zero_copy_only=False).astype(float)
    if isinstance(region, Cone):
        c0 = SkyCoord(region.ra_deg * u.deg, region.dec_deg * u.deg)
        sep = SkyCoord(ra_v * u.deg, dec_v * u.deg).separation(c0).deg
        mask = sep <= region.radius_deg
    else:
        mask = (
            (ra_v >= region.ra_min_deg)
            & (ra_v <= region.ra_max_deg)
            & (dec_v >= region.dec_min_deg)
            & (dec_v <= region.dec_max_deg)
        )
    return table.filter(pa.array(mask))


def apply_query(table: pa.Table, q: CatalogueQuery, ra: str = "ra", dec: str = "dec") -> pa.Table:
    out = select_region(table, q.region, ra, dec)
    for f in q.filters:
        col = out.column(f.column)
        mask = pc.is_valid(col) if f.op == "is_not_null" else _OPS[f.op](col, pa.scalar(f.value))
        out = out.filter(pc.fill_null(mask, False))
    if q.order_by:
        out = out.sort_by(q.order_by)
    return out.select(list(q.columns)).slice(0, q.row_cap)


@dataclass
class FakeArchive:
    """Serves a fixture table under a descriptor. ``corrupt_parts`` maps part -> how many fetch
    attempts deliver a corrupted chunk for it (DSI-003)."""

    descriptor: SourceDescriptor
    table: pa.Table
    ra_column: str = "ra"
    dec_column: str = "dec"
    corrupt_parts: dict[int, int] = field(default_factory=dict)
    fetch_calls: int = 0
    rows_served: int = 0

    def describe(self, source_id: str) -> SourceDescriptor:
        if source_id != self.descriptor.source_id:
            raise KeyError(source_id)
        return self.descriptor

    def query(self, q: CatalogueQuery) -> AcquisitionHandle:
        rendered = "FAKE " + canonical_json(q.to_document())
        return AcquisitionHandle(
            query=q,
            rendered_text=rendered,
            idempotency_key=idempotency_key(rendered, q.release_label, q.columns),
            descriptor=self.descriptor,
        )

    def fetch(self, handle: AcquisitionHandle) -> Iterator[pa.RecordBatch]:
        self.fetch_calls += 1
        q = handle.query
        result = apply_query(self.table, q, self.ra_column, self.dec_column)
        part = handle.resume_from_part
        for offset in range(part * q.chunk_rows, result.num_rows, q.chunk_rows):
            chunk = result.slice(offset, q.chunk_rows).combine_chunks()
            batch = chunk.to_batches()[0] if chunk.num_rows else pa.RecordBatch.from_pylist([], schema=chunk.schema)
            checksum = batch_checksum(batch)
            if self.corrupt_parts.get(part, 0) > 0:
                self.corrupt_parts[part] -= 1
                checksum = "sha256:" + "f" * 64
            self.rows_served += batch.num_rows
            yield batch.replace_schema_metadata({CHECKSUM_METADATA_KEY: checksum.encode()})
            part += 1


@dataclass
class IdentityCurator:
    """For fixtures that are already in canonical columns."""

    version: str = "1.0.0"

    def dialect_for(self, source_id: str, release_label: str) -> DialectRef:
        return DialectRef(f"identity-{source_id}", self.version)

    def curate(self, native: pa.Table, dialect: DialectRef) -> pa.Table:
        return native


@dataclass
class InMemoryHistoricalSources:
    _docs: dict[str, HistoricalDocument] = field(default_factory=dict)
    _content: dict[str, bytes] = field(default_factory=dict)
    _pages: dict[tuple[str, int], bytes] = field(default_factory=dict)
    _transcriptions: dict[str, TranscribedRecord] = field(default_factory=dict)
    _verifications: list[VerificationRecord] = field(default_factory=list)

    def register(self, document: HistoricalDocument, content: bytes | None = None) -> None:
        self._docs[document.document_id] = document
        if content is not None:
            self._content[document.document_id] = content

    def add_page(self, document_id: str, page: int, content: bytes) -> None:
        self._pages[(document_id, page)] = content

    def document(self, document_id: str) -> HistoricalDocument | None:
        return self._docs.get(document_id)

    def page(self, document_id: str, page: int) -> bytes | None:
        return self._pages.get((document_id, page))

    def add_transcription(self, record: TranscribedRecord) -> None:
        if record.document_id not in self._docs:
            raise KeyError(f"unknown document {record.document_id}")
        if record.record_id in self._transcriptions:
            raise ValueError(f"record {record.record_id} already transcribed; corrections go through verification")
        self._transcriptions[record.record_id] = record

    def add_verification(self, record: VerificationRecord) -> None:
        if record.record_id not in self._transcriptions:
            raise KeyError(f"cannot verify unknown record {record.record_id}")
        self._verifications.append(record)

    def transcriptions(self, document_id: str | None = None) -> Sequence[TranscribedRecord]:
        return [r for r in self._transcriptions.values() if document_id is None or r.document_id == document_id]

    def verifications(self, record_id: str | None = None) -> Sequence[VerificationRecord]:
        return [v for v in self._verifications if record_id is None or v.record_id == record_id]


def _propagate(table: pa.Table, epoch: u.Quantity) -> tuple[np.ndarray, np.ndarray]:
    """Linear propagation in the tangent plane: adequate for decades and sub-degree motion."""
    ra = table.column("ra_deg").to_numpy(zero_copy_only=False).astype(float)
    dec = table.column("dec_deg").to_numpy(zero_copy_only=False).astype(float)
    if "pm_ra_cosdec_masyr" not in table.column_names:
        return ra, dec
    t0 = table.column("epoch_jyear").to_numpy(zero_copy_only=False).astype(float)
    pmra = np.nan_to_num(table.column("pm_ra_cosdec_masyr").to_numpy(zero_copy_only=False).astype(float))
    pmdec = np.nan_to_num(table.column("pm_dec_masyr").to_numpy(zero_copy_only=False).astype(float))
    dt = epoch.to_value(u.yr) - t0
    mas_to_deg = 1.0 / 3.6e6
    dec_new = dec + pmdec * dt * mas_to_deg
    ra_new = (ra + pmra * dt * mas_to_deg / np.cos(np.radians(dec))) % 360.0
    return ra_new, dec_new


class AstropyCrossMatch:
    """The CPU reference cross-match (and the fake): nearest neighbour on the sky within a radius."""

    def match(
        self, left: pa.Table, right: pa.Table, radius: u.Quantity, *, epoch: u.Quantity | None = None
    ) -> CrossMatchResult:
        if left.num_rows == 0 or right.num_rows == 0:
            return CrossMatchResult(np.array([], dtype=np.int64), np.array([], dtype=np.int64), np.array([]) * u.arcsec)
        if epoch is not None:
            lra, ldec = _propagate(left, epoch)
            rra, rdec = _propagate(right, epoch)
        else:
            lra = left.column("ra_deg").to_numpy(zero_copy_only=False).astype(float)
            ldec = left.column("dec_deg").to_numpy(zero_copy_only=False).astype(float)
            rra = right.column("ra_deg").to_numpy(zero_copy_only=False).astype(float)
            rdec = right.column("dec_deg").to_numpy(zero_copy_only=False).astype(float)
        lc = SkyCoord(lra * u.deg, ldec * u.deg)
        rc = SkyCoord(rra * u.deg, rdec * u.deg)
        idx, sep, _ = lc.match_to_catalog_sky(rc)
        keep = sep <= radius
        left_index = np.nonzero(keep)[0].astype(np.int64)
        return CrossMatchResult(left_index, np.asarray(idx)[keep].astype(np.int64), sep[keep].to(u.arcsec))
