"""TapArchiveAdapter: one ArchiveGateway for every TAP service, configured per source by YAML (§8).

A source file (``sources/<source_id>.yaml``) declares the service URL, table, position and key
columns, the native column list with types, the licence and citation, the archive's row and rate
limits, the dialect that maps it to canonical astrometry, and how the service pages: ``offset``
(ADQL 2.1 ``OFFSET``) or ``single`` (one call, so ``chunk_rows`` must cover ``row_cap``).
Adding a source or a release is a new file, not code.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import pyarrow as pa
import yaml

from sdp.adapters.archives import adql, votable
from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.kernel.application.acquisition import CHECKSUM_METADATA_KEY, batch_checksum
from sdp.kernel.domain.archive import (
    AcquisitionHandle,
    CatalogueQuery,
    ColumnDescriptor,
    SourceDescriptor,
    idempotency_key,
)


@dataclass(frozen=True)
class TapSource:
    descriptor: SourceDescriptor
    service_url: str
    ra_column: str
    dec_column: str
    key_column: str
    paging: str = "offset"

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> TapSource:
        columns = tuple(
            ColumnDescriptor(str(c["name"]), str(c["datatype"]), c.get("unit"), str(c.get("description", "")))
            for c in doc["columns"]
        )
        descriptor = SourceDescriptor(
            source_id=str(doc["source_id"]),
            release_label=str(doc["release_label"]),
            table=str(doc["table"]),
            columns=columns,
            licence=str(doc.get("licence") or ""),
            citation=str(doc["citation"]),
            max_rows_per_call=int(doc["max_rows_per_call"]),
            min_seconds_between_calls=float(doc["min_seconds_between_calls"]),
            dialect_id=str(doc["dialect_id"]),
            doi=doc.get("doi"),
        )
        paging = str(doc.get("paging", "offset"))
        if paging not in ("offset", "single"):
            raise ValueError(f"{descriptor.source_id}: paging must be offset or single, got {paging!r}")
        return cls(
            descriptor,
            str(doc["service_url"]).rstrip("/"),
            str(doc["ra_column"]),
            str(doc["dec_column"]),
            str(doc["key_column"]),
            paging,
        )


def load_sources(directory: Path | None = None) -> dict[str, TapSource]:
    root = Path(directory) if directory else Path(str(files("sdp.adapters.archives").joinpath("sources")))
    out: dict[str, TapSource] = {}
    for path in sorted(root.glob("*.yaml")):
        source = TapSource.from_document(yaml.safe_load(path.read_text("utf-8")))
        if source.descriptor.source_id in out:
            raise ValueError(f"{path.name}: duplicate source {source.descriptor.source_id}")
        out[source.descriptor.source_id] = source
    return out


@dataclass
class TapArchiveAdapter:
    """Serves the sources it is given through one transport (a cassette in CI, the network in production)."""

    sources: Mapping[str, TapSource]
    transport: Transport

    def _source(self, source_id: str) -> TapSource:
        if source_id not in self.sources:
            raise KeyError(f"unknown source {source_id!r}; configured: {sorted(self.sources)}")
        return self.sources[source_id]

    def describe(self, source_id: str) -> SourceDescriptor:
        return self._source(source_id).descriptor

    def _check(self, q: CatalogueQuery, source: TapSource) -> None:
        d = source.descriptor
        if q.release_label != d.release_label:
            raise ValueError(f"{d.source_id} serves {d.release_label}, not {q.release_label}")
        unknown = set(q.columns) - set(d.column_names())
        if unknown:
            raise ValueError(f"{d.source_id}: columns {sorted(unknown)} are not in the declared column list")
        if q.chunk_rows > d.max_rows_per_call:
            raise ValueError(
                f"{d.source_id}: chunk_rows {q.chunk_rows} exceeds the archive's {d.max_rows_per_call} per call"
            )
        if source.paging == "single" and q.chunk_rows < q.row_cap:
            raise ValueError(f"{d.source_id} cannot page: chunk_rows must equal row_cap")

    def query(self, q: CatalogueQuery) -> AcquisitionHandle:
        source = self._source(q.source_id)
        self._check(q, source)
        rendered = adql.render(
            q,
            table=source.descriptor.table,
            ra=source.ra_column,
            dec=source.dec_column,
            order_by=q.order_by or source.key_column,
            top=q.row_cap,
        )
        return AcquisitionHandle(q, rendered, idempotency_key(rendered, q.release_label, q.columns), source.descriptor)

    def chunk_request(self, handle: AcquisitionHandle, part: int) -> HttpRequest:
        q = handle.query
        source = self._source(q.source_id)
        offset = part * q.chunk_rows
        text = adql.render(
            q,
            table=source.descriptor.table,
            ra=source.ra_column,
            dec=source.dec_column,
            order_by=q.order_by or source.key_column,
            top=min(q.chunk_rows, q.row_cap - offset),
            offset=offset,
        )
        body = urllib.parse.urlencode(
            {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "votable", "MAXREC": str(q.chunk_rows), "QUERY": text}
        ).encode()
        return HttpRequest(
            "POST", f"{source.service_url}/sync", body, {"Content-Type": "application/x-www-form-urlencoded"}
        )

    def fetch(self, handle: AcquisitionHandle) -> Iterator[pa.RecordBatch]:
        q = handle.query
        part = handle.resume_from_part
        while part * q.chunk_rows < q.row_cap:
            response = self.transport.send(self.chunk_request(handle, part))
            if response.status != 200:
                raise TransportError(f"{q.source_id} part {part}: HTTP {response.status}", response.status)
            table = votable.parse(response.body)
            missing = set(q.columns) - set(table.column_names)
            if missing:
                raise TransportError(f"{q.source_id} part {part}: response lacks {sorted(missing)}")
            chunk = table.select(list(q.columns)).combine_chunks()
            batches = chunk.to_batches()
            batch = batches[0] if batches else pa.RecordBatch.from_pylist([], schema=chunk.schema)
            if batch.num_rows == 0 and part > handle.resume_from_part:
                return
            yield batch.replace_schema_metadata({CHECKSUM_METADATA_KEY: batch_checksum(batch).encode()})
            if batch.num_rows < q.chunk_rows:
                return
            part += 1
