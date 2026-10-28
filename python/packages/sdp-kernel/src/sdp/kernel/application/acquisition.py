"""AcquireDataset: bounded, checkpointed, verified acquisition and curation (§8, DSI-002..006, DSI-010)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

import pyarrow as pa

from sdp.kernel.application.media import PARQUET, decode_artifact, parquet_bytes
from sdp.kernel.domain import events as ev
from sdp.kernel.domain.archive import AcquisitionHandle
from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.domain.dataset import AcquisitionPart, AcquisitionRecord, DatasetArtifact, DatasetStatus
from sdp.kernel.domain.errors import CurationBlocked, IntegrityError, InvariantViolation, StorageExhausted
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.provenance import ProvGraph
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.ports.archives import ArchiveGateway, DialectCurator
from sdp.kernel.ports.determinism import Clock
from sdp.kernel.ports.driving import AcquireDatasetCommand, AcquisitionResult
from sdp.kernel.ports.repositories import UnitOfWork
from sdp.kernel.ports.storage import ArtifactStore

log = logging.getLogger("sdp.kernel.acquisition")

CHECKSUM_METADATA_KEY = b"sdp.sha256"


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff (§8)."""

    max_attempts: int = 4
    base_delay_s: float = 2.0
    sleep: Callable[[float], None] = field(default=lambda _s: None, compare=False)

    def delay(self, attempt: int) -> float:
        return float(self.base_delay_s * 2 ** (attempt - 1))


def batch_checksum(batch: pa.RecordBatch) -> str:
    """SHA-256 over the Arrow IPC encoding of a batch's columns (schema metadata excluded)."""
    plain = batch.replace_schema_metadata(None)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, plain.schema) as writer:
        writer.write_batch(plain)
    return sha256_id(sink.getvalue().to_pybytes())


def raw_part_path(source_id: str, release_label: str, key: str, part: int) -> str:
    release = release_label.replace(":", "_")
    return f"raw/{source_id}/{release}/{key.removeprefix('sha256:')[:32]}/part-{part:04d}.parquet"


class ChunkRejected(Exception):
    pass


@dataclass
class AcquireDatasetUseCase:
    uow: Callable[[], UnitOfWork]
    gateways: Mapping[str, ArchiveGateway]
    curator: DialectCurator
    store: ArtifactStore
    clock: Clock
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    min_free_bytes: int = 50 * 1024**3

    def execute(self, command: AcquireDatasetCommand) -> AcquisitionResult:
        q = command.query
        free = self.store.free_bytes()
        if free is not None and free < self.min_free_bytes:
            raise StorageExhausted(f"{free} bytes free, below the {self.min_free_bytes} byte threshold (DSI-010)")
        gateway = self.gateways.get(q.source_id)
        if gateway is None:
            raise InvariantViolation(f"no ArchiveGateway for source {q.source_id!r} (DSI-001)")
        descriptor = gateway.describe(q.source_id)
        if descriptor.release_label != q.release_label:
            raise InvariantViolation(
                f"query pins {q.release_label} but {q.source_id} serves {descriptor.release_label}; releases are never mixed"
            )
        if q.chunk_rows > descriptor.max_rows_per_call:
            q = replace(q, chunk_rows=descriptor.max_rows_per_call)
        handle = gateway.query(q)

        with self.uow() as uow:
            existing = uow.datasets.find_by_idempotency_key(handle.idempotency_key)
            if existing is not None and existing.status is DatasetStatus.ACQUIRED:
                curated = next(iter(uow.datasets.children(existing.artifact_id)), None)
                uow.commit()
                if curated is None and command.curate:
                    curated = self._curate(existing, descriptor.licence)
                return AcquisitionResult(raw=existing, curated=curated, reused=True)
            uow.commit()

        source = SourceRef(
            kind=SourceKind.ARCHIVE,
            identity=descriptor.source_id,
            licence=descriptor.licence,
            citation=descriptor.citation,
            release_label=descriptor.release_label,
            query_text=handle.rendered_text,
            doi=descriptor.doi,
        )
        parts = self._fetch_parts(gateway, handle)
        tables = [decode_artifact(self.store.get(p.artifact_id), PARQUET) for p in parts]
        table = pa.concat_tables(tables) if tables else pa.table({c: pa.array([], pa.null()) for c in q.columns})
        data = parquet_bytes(table)
        written = self.store.put(data, PARQUET)
        actual = ArtifactId.of_bytes(self.store.get(written))
        record = AcquisitionRecord(
            rendered_query=handle.rendered_text,
            idempotency_key=handle.idempotency_key,
            source_release=descriptor.release_label,
            acquired_at=self.clock.now(),
            checksum=written,
            byte_count=len(data),
            parts=tuple(parts),
        )
        raw = DatasetArtifact.acquired_raw(
            actual=actual,
            media_type=PARQUET,
            byte_count=len(data),
            source_ref=source,
            acquisition=record,
        )
        with self.uow() as uow:
            uow.datasets.add(raw)
            graph = ProvGraph()
            activity = graph.activity(
                f"acquisition/{handle.idempotency_key}", type="Acquisition", query=handle.rendered_text
            )
            graph.agent(descriptor.source_id, type="Archive", release=descriptor.release_label)
            graph.entity(raw.artifact_id.value, type="DatasetArtifact", kind="raw", release=descriptor.release_label)
            graph.relate("wasGeneratedBy", raw.artifact_id.value, activity)
            graph.relate("wasAssociatedWith", activity, descriptor.source_id)
            uow.evidence.record(graph)
            event: ev.DomainEvent
            if raw.status is DatasetStatus.REJECTED:
                event = ev.DatasetRejected(raw.artifact_id.value, self.clock.now(), {"reason": raw.rejection_reason})
            else:
                event = ev.DatasetAcquired(
                    raw.artifact_id.value,
                    self.clock.now(),
                    {"source": descriptor.source_id, "release": descriptor.release_label, "rows": table.num_rows},
                )
            uow.events.append([event])
            uow.commit()
        if raw.status is DatasetStatus.REJECTED:
            raise IntegrityError(raw.rejection_reason or "raw artifact rejected")
        curated = self._curate(raw, descriptor.licence) if command.curate else None
        return AcquisitionResult(raw=raw, curated=curated, reused=False)

    def _fetch_parts(self, gateway: ArchiveGateway, handle: AcquisitionHandle) -> list[AcquisitionPart]:
        """Fetch chunk by chunk, resuming after the last stored part; verify every chunk."""
        q = handle.query
        parts: list[AcquisitionPart] = []
        while True:
            existing = self.store.resolve(
                raw_part_path(q.source_id, q.release_label, handle.idempotency_key, len(parts))
            )
            if existing is None:
                break
            table = decode_artifact(self.store.get(existing), PARQUET)
            parts.append(AcquisitionPart(len(parts), existing, len(self.store.get(existing)), table.num_rows))
        attempt = 1
        while True:
            try:
                for batch in gateway.fetch(handle.resumed_at(len(parts))):
                    parts.append(self._store_part(handle, len(parts), batch))
                return parts
            except ChunkRejected as rejected:
                with self.uow() as uow:
                    uow.events.append(
                        [
                            ev.DatasetRejected(
                                handle.idempotency_key, self.clock.now(), {"part": len(parts), "reason": str(rejected)}
                            )
                        ]
                    )
                    uow.commit()
                if attempt >= self.retry.max_attempts:
                    raise IntegrityError(
                        f"chunk {len(parts)} failed verification {attempt} times: {rejected}"
                    ) from rejected
                log.warning("chunk rejected, retrying", extra={"part": len(parts), "attempt": attempt})
                self.retry.sleep(self.retry.delay(attempt))
                attempt += 1

    def _store_part(self, handle: AcquisitionHandle, part: int, batch: pa.RecordBatch) -> AcquisitionPart:
        q = handle.query
        declared = (batch.schema.metadata or {}).get(CHECKSUM_METADATA_KEY)
        if declared is not None and declared.decode() != batch_checksum(batch):
            raise ChunkRejected(f"part {part}: received bytes do not match the transport checksum")
        missing = [c for c in q.columns if c not in batch.schema.names]
        if missing:
            raise ChunkRejected(f"part {part}: columns {missing} missing from the response")
        if batch.num_rows > q.chunk_rows:
            raise ChunkRejected(f"part {part}: {batch.num_rows} rows exceeds the chunk size {q.chunk_rows}")
        data = parquet_bytes(pa.Table.from_batches([batch.replace_schema_metadata(None)]))
        expected = ArtifactId.of_bytes(data)
        stored = self.store.put(
            data, PARQUET, logical_path=raw_part_path(q.source_id, q.release_label, handle.idempotency_key, part)
        )
        try:
            readback = self.store.get(stored)
        except IntegrityError as exc:
            raise ChunkRejected(f"part {part}: {exc}") from exc
        if stored != expected or ArtifactId.of_bytes(readback) != expected:
            raise ChunkRejected(f"part {part}: stored bytes do not hash to {expected}")
        with self.uow() as uow:
            uow.events.append(
                [
                    ev.CheckpointWritten(
                        handle.idempotency_key,
                        self.clock.now(),
                        {"part": part, "artifact_id": stored.value, "rows": batch.num_rows},
                    )
                ]
            )
            uow.commit()
        return AcquisitionPart(part, stored, len(data), batch.num_rows)

    def _curate(self, raw: DatasetArtifact, licence: str) -> DatasetArtifact:
        if not licence.strip():
            with self.uow() as uow:
                uow.events.append(
                    [
                        ev.DatasetRejected(
                            raw.artifact_id.value, self.clock.now(), {"reason": "no licence; curation blocked"}
                        )
                    ]
                )
                uow.commit()
            raise CurationBlocked(f"{raw.source_ref.identity} declares no licence (DSI-006)")
        release = raw.source_ref.release_label or ""
        dialect = self.curator.dialect_for(raw.source_ref.identity, release)
        native = decode_artifact(self.store.get(raw.artifact_id), PARQUET)
        canonical = self.curator.curate(native, dialect)
        # The file itself names its dialect and raw parent (DSI-005), so a curated artifact never
        # shares a content address with its raw parent even when the dialect renames nothing.
        canonical = canonical.replace_schema_metadata(
            {
                **(canonical.schema.metadata or {}),
                b"sdp.dialect": f"{dialect.dialect_id}@{dialect.dialect_version}".encode(),
                b"sdp.raw_parent": raw.artifact_id.value.encode(),
            }
        )
        data = parquet_bytes(canonical)
        curated_id = self.store.put(data, PARQUET)
        curated = raw.curate(curated_id=curated_id, byte_count=len(data), dialect=dialect, media_type=PARQUET)
        with self.uow() as uow:
            uow.datasets.add(curated)
            graph = ProvGraph()
            activity = graph.activity(
                f"curation/{curated_id.value}",
                type="Curation",
                dialect=dialect.dialect_id,
                dialect_version=dialect.dialect_version,
            )
            graph.entity(curated_id.value, type="DatasetArtifact", kind="curated")
            graph.entity(raw.artifact_id.value)
            graph.relate("used", activity, raw.artifact_id.value)
            graph.relate("wasGeneratedBy", curated_id.value, activity)
            graph.relate("wasDerivedFrom", curated_id.value, raw.artifact_id.value)
            uow.evidence.record(graph)
            uow.events.append(
                [
                    ev.DatasetAcquired(
                        curated_id.value,
                        self.clock.now(),
                        {
                            "curated_from": raw.artifact_id.value,
                            "dialect": dialect.dialect_id,
                            "rows": canonical.num_rows,
                        },
                    )
                ]
            )
            uow.commit()
        return curated
