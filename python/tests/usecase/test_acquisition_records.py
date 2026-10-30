"""T4: AcquireDataset in full: the exact refusal each guard gives, the events and evidence an
acquisition and a curation record, how a resumed acquisition reuses the parts already stored, and the
backoff between retries (§8, DSI-002..006, DSI-010).

The behaviour is in test_acquire_dataset.py; these pin the details mutation testing showed nothing
else checked (docs/testing.md, "Killing a survivor")."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from typing import Any

import pyarrow as pa
import pytest

from sdp.kernel.application.acquisition import (
    CHECKSUM_METADATA_KEY,
    AcquireDatasetUseCase,
    RetryPolicy,
    batch_checksum,
    raw_part_path,
)
from sdp.kernel.application.media import PARQUET, decode_artifact, parquet_bytes
from sdp.kernel.domain.archive import AcquisitionHandle, CatalogueQuery, Cone
from sdp.kernel.domain.dataset import AcquisitionPart, DatasetStatus
from sdp.kernel.domain.errors import CurationBlocked, IntegrityError, InvariantViolation, StorageExhausted
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.provenance import qualified
from sdp.kernel.ports.driving import AcquireDatasetCommand
from sdp.testing.catalogues import FAKE_GAIA, fixture_catalogue
from sdp.testing.fakes import FakeArchive, FixedClock, IdentityCurator, InMemoryArtifactStore, InMemoryDatabase

pytestmark = pytest.mark.tier("T4")

QUERY = CatalogueQuery(
    "fake-gaia",
    "gaia:DR3",
    Cone(56.5, 24.25, 1.0),
    ("source_id", "ra", "dec", "phot_g_mean_mag"),
    row_cap=100,
    chunk_rows=50,
)


@dataclass
class Rig:
    archive: FakeArchive
    store: InMemoryArtifactStore = field(default_factory=InMemoryArtifactStore)
    db: InMemoryDatabase = field(default_factory=InMemoryDatabase)
    clock: FixedClock = field(default_factory=FixedClock)
    sleeps: list[float] = field(default_factory=list)
    min_free_bytes: int = 0
    curator: Any = field(default_factory=IdentityCurator)

    @property
    def use_case(self) -> AcquireDatasetUseCase:
        return AcquireDatasetUseCase(
            uow=self.db.unit_of_work,
            gateways={"fake-gaia": self.archive},
            curator=self.curator,
            store=self.store,
            clock=self.clock,
            retry=RetryPolicy(max_attempts=3, base_delay_s=0.5, sleep=self.sleeps.append),
            min_free_bytes=self.min_free_bytes,
        )

    def run(self, query: CatalogueQuery = QUERY, *, curate: bool = True) -> Any:
        return self.use_case.execute(AcquireDatasetCommand(query, curate=curate))

    def events(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [(s.event.name, s.event.aggregate_id, s.event.payload) for s in self.db.state.events]


def rig(**changes: Any) -> Rig:
    return Rig(FakeArchive(FAKE_GAIA, fixture_catalogue(), **changes))


# --- the retry policy and part paths -------------------------------------------------------------


def test_the_backoff_doubles_from_the_base_delay() -> None:
    policy = RetryPolicy(base_delay_s=0.5)
    assert [policy.delay(a) for a in (1, 2, 3, 4)] == [0.5, 1.0, 2.0, 4.0]
    assert type(policy.delay(1)) is float
    assert RetryPolicy().max_attempts == 4 and RetryPolicy().base_delay_s == 2.0
    RetryPolicy().sleep(1.0)


def test_a_part_path_names_the_source_release_key_and_part() -> None:
    key = "sha256:" + "0123456789abcdef" * 4
    assert raw_part_path("fake-gaia", "gaia:DR3", key, 7) == (
        "raw/fake-gaia/gaia_DR3/0123456789abcdef0123456789abcdef/part-0007.parquet"
    )
    assert raw_part_path("s", "a:b:c", "plain", 12345) == "raw/s/a_b_c/plain/part-12345.parquet"


def test_a_batch_checksum_ignores_schema_metadata() -> None:
    batch = pa.RecordBatch.from_pydict({"a": [1, 2]})
    tagged = batch.replace_schema_metadata({b"k": b"v"})
    assert batch_checksum(tagged) == batch_checksum(batch)
    assert batch_checksum(batch) != batch_checksum(pa.RecordBatch.from_pydict({"a": [1, 3]}))


# --- the guards before any fetch -----------------------------------------------------------------


def test_too_little_free_space_is_refused_with_both_numbers() -> None:
    r = rig()
    r.store = InMemoryArtifactStore(capacity=1_000)
    r.min_free_bytes = 1_001
    with pytest.raises(StorageExhausted, match=r"^1000 bytes free, below the 1001 byte threshold \(DSI-010\)$"):
        r.run()
    assert r.archive.fetch_calls == 0


def test_free_space_exactly_at_the_threshold_is_enough() -> None:
    r = rig()
    r.store = InMemoryArtifactStore(capacity=10**9)
    r.min_free_bytes = r.store.free_bytes() or 0
    assert r.run().raw.status is DatasetStatus.ACQUIRED


def test_a_source_without_a_gateway_is_refused() -> None:
    r = rig()
    with pytest.raises(InvariantViolation, match=r"^no ArchiveGateway for source 'other' \(DSI-001\)$"):
        r.run(replace(QUERY, source_id="other"))


def test_a_query_pinning_another_release_is_refused() -> None:
    r = rig()
    with pytest.raises(
        InvariantViolation,
        match=r"^query pins gaia:DR2 but fake-gaia serves gaia:DR3; releases are never mixed$",
    ):
        r.run(replace(QUERY, release_label="gaia:DR2"))


def test_chunks_are_cut_to_the_archive_limit() -> None:
    r = rig()
    result = r.run(replace(QUERY, row_cap=1_000, chunk_rows=1_000))
    assert result.raw.acquisition is not None
    counts = [p.row_count for p in result.raw.acquisition.parts]
    assert len(counts) > 2 and counts[0] == 50 and max(counts) == 50


# --- what an acquisition records -----------------------------------------------------------------


def test_an_acquisition_records_its_parts_events_and_evidence() -> None:
    r = rig()
    result = r.run()
    raw, curated = result.raw, result.curated
    assert raw.acquisition is not None and curated is not None
    key = raw.acquisition.idempotency_key
    parts = raw.acquisition.parts
    assert [(p.part, p.row_count) for p in parts] == [(0, 50), (1, 50)]
    for p in parts:
        assert r.store.resolve(raw_part_path("fake-gaia", "gaia:DR3", key, p.part)) == p.artifact_id
        assert p.byte_count == len(r.store.get(p.artifact_id))
    assert raw.media_type == PARQUET and r.store.media_type(raw.artifact_id) == PARQUET
    assert curated.media_type == PARQUET and r.store.media_type(curated.artifact_id) == PARQUET
    assert raw.source_ref.doi == FAKE_GAIA.doi
    assert result.reused is False and result.rows == 100 and result.row_cap == 100

    assert r.events() == [
        ("CheckpointWritten", key, {"part": 0, "artifact_id": parts[0].artifact_id.value, "rows": 50}),
        ("CheckpointWritten", key, {"part": 1, "artifact_id": parts[1].artifact_id.value, "rows": 50}),
        ("DatasetAcquired", raw.artifact_id.value, {"source": "fake-gaia", "release": "gaia:DR3", "rows": 100}),
        (
            "DatasetAcquired",
            curated.artifact_id.value,
            {"curated_from": raw.artifact_id.value, "dialect": "identity-fake-gaia", "rows": 100},
        ),
    ]

    graph = r.db.state.graph
    acquisition = qualified(f"acquisition/{key}")
    curation = qualified(f"curation/{curated.artifact_id.value}")
    raw_id, curated_id = qualified(raw.artifact_id.value), qualified(curated.artifact_id.value)
    assert graph.activities[acquisition] == {"sdp:type": "Acquisition", "sdp:query": raw.acquisition.rendered_query}
    assert graph.activities[curation] == {
        "sdp:type": "Curation",
        "sdp:dialect": "identity-fake-gaia",
        "sdp:dialect_version": "1.0.0",
    }
    assert graph.agents[qualified("fake-gaia")] == {"sdp:type": "Archive", "sdp:release": "gaia:DR3"}
    assert graph.entities[raw_id] == {"sdp:type": "DatasetArtifact", "sdp:kind": "raw", "sdp:release": "gaia:DR3"}
    assert graph.entities[curated_id] == {"sdp:type": "DatasetArtifact", "sdp:kind": "curated"}
    assert graph.relations["wasGeneratedBy"] == [(raw_id, acquisition), (curated_id, curation)]
    assert graph.relations["wasAssociatedWith"] == [(acquisition, qualified("fake-gaia"))]
    assert graph.relations["used"] == [(curation, raw_id)]
    assert graph.relations["wasDerivedFrom"] == [(curated_id, raw_id)]


def test_the_curated_file_names_its_dialect_and_raw_parent() -> None:
    r = rig()
    result = r.run()
    assert result.curated is not None
    table = decode_artifact(r.store.get(result.curated.artifact_id), PARQUET)
    metadata = table.schema.metadata or {}
    assert metadata[b"sdp.dialect"] == b"identity-fake-gaia@1.0.0"
    assert metadata[b"sdp.raw_parent"] == result.raw.artifact_id.value.encode()


def test_without_curation_only_the_raw_artifact_is_made() -> None:
    r = rig()
    result = r.run(curate=False)
    assert result.curated is None
    assert [name for name, _, _ in r.events()] == ["CheckpointWritten", "CheckpointWritten", "DatasetAcquired"]


def test_an_empty_region_gives_an_empty_table_of_the_query_columns() -> None:
    r = rig()
    result = r.run(replace(QUERY, region=Cone(200.0, -60.0, 0.01)))
    table = decode_artifact(r.store.get(result.raw.artifact_id), PARQUET)
    assert table.num_rows == 0 and table.column_names == list(QUERY.columns)
    assert result.rows == 0 and not result.reached_row_cap


def test_a_full_row_cap_is_logged_and_one_short_is_not(caplog: pytest.LogCaptureFixture) -> None:
    r = rig()
    with caplog.at_level(logging.WARNING, logger="sdp.kernel.acquisition"):
        r.run(replace(QUERY, row_cap=100_000))
    assert not caplog.records
    with caplog.at_level(logging.WARNING, logger="sdp.kernel.acquisition"):
        r.run(replace(QUERY, row_cap=100))
    [record] = caplog.records
    assert record.getMessage() == "acquisition reached its row cap; the region may hold more rows"
    assert record.__dict__["source"] == "fake-gaia" and record.__dict__["row_cap"] == 100


# --- reuse and resume ----------------------------------------------------------------------------


def test_a_reused_acquisition_counts_its_rows_and_curates_when_asked() -> None:
    r = rig()
    first = r.run(curate=False)
    again = r.run(curate=True)
    assert again.reused is True and again.raw == first.raw
    assert again.rows == 100 and again.row_cap == 100
    assert again.curated is not None and again.curated.parents == (first.raw.artifact_id,)
    assert r.archive.fetch_calls == 1
    third = r.run(curate=True)
    assert third.curated == again.curated


def test_a_reused_acquisition_without_curation_stays_uncurated() -> None:
    r = rig()
    r.run(curate=False)
    again = r.run(curate=False)
    assert again.reused is True and again.curated is None


@dataclass
class Interrupted(FakeArchive):
    """Serves ``serve`` batches per fetch, then fails, as a dropped connection would."""

    serve: int = 1
    resumed_from: list[int] = field(default_factory=list)

    def fetch(self, handle: AcquisitionHandle) -> Iterator[pa.RecordBatch]:
        self.resumed_from.append(handle.resume_from_part)
        for n, batch in enumerate(super().fetch(handle)):
            if n == self.serve:
                raise ConnectionError("dropped")
            yield batch


def test_a_resumed_acquisition_reuses_the_parts_already_stored() -> None:
    archive = Interrupted(FAKE_GAIA, fixture_catalogue())
    r = Rig(archive)
    with pytest.raises(ConnectionError):
        r.run()
    archive.serve = 99
    result = r.run()
    assert archive.resumed_from == [0, 1]
    assert result.raw.acquisition is not None
    first, second = result.raw.acquisition.parts
    assert first == AcquisitionPart(0, first.artifact_id, len(r.store.get(first.artifact_id)), 50)
    assert second.part == 1 and second.row_count == 50
    assert decode_artifact(r.store.get(result.raw.artifact_id), PARQUET).num_rows == 100


# --- rejected chunks -----------------------------------------------------------------------------


def test_a_rejected_chunk_is_recorded_and_retried_after_a_backoff() -> None:
    r = rig(corrupt_parts={1: 2})
    result = r.run()
    key = result.raw.acquisition.idempotency_key if result.raw.acquisition else ""
    reason = "part 1: received bytes do not match the transport checksum"
    rejected = [(n, a, p) for n, a, p in r.events() if n == "DatasetRejected"]
    assert rejected == [
        ("DatasetRejected", key, {"part": 1, "reason": reason}),
        ("DatasetRejected", key, {"part": 1, "reason": reason}),
    ]
    assert r.sleeps == [0.5, 1.0]


def test_retries_end_with_the_last_rejection(caplog: pytest.LogCaptureFixture) -> None:
    r = rig(corrupt_parts={0: 10})
    with (
        caplog.at_level(logging.WARNING, logger="sdp.kernel.acquisition"),
        pytest.raises(
            IntegrityError,
            match=r"^chunk 0 failed verification 3 times: part 0: received bytes do not match the transport checksum$",
        ) as caught,
    ):
        r.run()
    assert caught.value.__cause__ is not None
    assert r.sleeps == [0.5, 1.0]
    assert [(rec.getMessage(), rec.__dict__["part"], rec.__dict__["attempt"]) for rec in caplog.records] == [
        ("chunk rejected, retrying", 0, 1),
        ("chunk rejected, retrying", 0, 2),
    ]


@dataclass
class Shaped(FakeArchive):
    """Serves batches through ``reshape``, with a checksum that matches what it serves."""

    reshape: Any = None

    def fetch(self, handle: AcquisitionHandle) -> Iterator[pa.RecordBatch]:
        for batch in super().fetch(handle):
            shaped = self.reshape(batch.replace_schema_metadata(None))
            yield shaped.replace_schema_metadata({CHECKSUM_METADATA_KEY: batch_checksum(shaped).encode()})


def _refused(reshape: Any) -> str:
    r = Rig(Shaped(FAKE_GAIA, fixture_catalogue(), reshape=reshape))
    with pytest.raises(IntegrityError) as caught:
        r.run()
    return str(caught.value)


def test_a_chunk_missing_a_column_is_refused() -> None:
    assert _refused(lambda b: b.drop_columns(["dec"])) == (
        "chunk 0 failed verification 3 times: part 0: columns ['dec'] missing from the response"
    )


def test_a_chunk_larger_than_the_chunk_size_is_refused() -> None:
    def doubled(b: pa.RecordBatch) -> pa.RecordBatch:
        return pa.Table.from_batches([b, b]).combine_chunks().to_batches()[0]

    assert _refused(doubled) == "chunk 0 failed verification 3 times: part 0: 100 rows exceeds the chunk size 50"


def test_a_chunk_without_a_transport_checksum_is_accepted() -> None:
    @dataclass
    class Unsigned(FakeArchive):
        def fetch(self, handle: AcquisitionHandle) -> Iterator[pa.RecordBatch]:
            for batch in super().fetch(handle):
                yield batch.replace_schema_metadata(None)

    r = Rig(Unsigned(FAKE_GAIA, fixture_catalogue()))
    assert r.run().raw.status is DatasetStatus.ACQUIRED


@dataclass
class Unreliable(InMemoryArtifactStore):
    """Corrupts what it stores under a part path, or what it hands back for the final artifact."""

    bad_part_reads: int = 0
    wrong_part_ids: int = 0
    tamper_final: bool = False

    def put(self, data: bytes, media_type: str, *, logical_path: str | None = None) -> ArtifactId:
        stored = super().put(data, media_type, logical_path=logical_path)
        if logical_path is not None and self.wrong_part_ids > 0:
            self.wrong_part_ids -= 1
            return super().put(data + b"!", media_type)
        return stored

    def get(self, artifact_id: ArtifactId) -> bytes:
        if self.bad_part_reads > 0 and artifact_id.value in self._paths.values():
            self.bad_part_reads -= 1
            raise IntegrityError(f"artifact {artifact_id} failed SHA-256 verification on read")
        data = super().get(artifact_id)
        if self.tamper_final and artifact_id.value not in self._paths.values():
            return data + b"!"
        return data


def test_a_part_that_fails_its_read_back_is_rejected_and_retried() -> None:
    r = rig()
    r.store = Unreliable(bad_part_reads=1)
    result = r.run()
    [rejected] = [p for n, _, p in r.events() if n == "DatasetRejected"]
    assert re.fullmatch(r"part 0: artifact sha256:[0-9a-f]{64} failed SHA-256 verification on read", rejected["reason"])
    assert result.raw.status is DatasetStatus.ACQUIRED


def test_a_part_stored_under_another_address_is_rejected() -> None:
    r = rig()
    r.store = Unreliable(wrong_part_ids=1)
    r.run()
    [rejected] = [p for n, _, p in r.events() if n == "DatasetRejected"]
    assert re.fullmatch(r"part 0: stored bytes do not hash to sha256:[0-9a-f]{64}", rejected["reason"])


def test_a_raw_artifact_that_reads_back_wrong_is_rejected() -> None:
    r = rig()
    r.store = Unreliable(tamper_final=True)
    with pytest.raises(IntegrityError, match=r"^checksum sha256:[0-9a-f]{64} differs from acquisition record"):
        r.run()
    names = [n for n, _, _ in r.events()]
    assert names[-1] == "DatasetRejected"
    assert r.events()[-1][2]["reason"].startswith("checksum ")


# --- curation ------------------------------------------------------------------------------------


def test_curation_without_a_licence_is_blocked_and_recorded() -> None:
    r = Rig(FakeArchive(replace(FAKE_GAIA, licence="  "), fixture_catalogue()))
    with pytest.raises(CurationBlocked, match=r"^fake-gaia declares no licence \(DSI-006\)$"):
        r.run()
    name, aggregate, payload = r.events()[-1]
    assert name == "DatasetRejected" and payload == {"reason": "no licence; curation blocked"}
    [(_, raw_id, _)] = [e for e in r.events() if e[0] == "DatasetAcquired"]
    assert aggregate == raw_id


def test_curation_asks_the_curator_for_the_source_release_dialect() -> None:
    asked: list[tuple[str, str]] = []

    @dataclass
    class Recording(IdentityCurator):
        def dialect_for(self, source_id: str, release_label: str) -> Any:
            asked.append((source_id, release_label))
            return super().dialect_for(source_id, release_label)

    r = rig()
    r.curator = Recording()
    r.run()
    assert asked == [("fake-gaia", "gaia:DR3")]


def test_parquet_bytes_round_trip() -> None:
    table = pa.table({"a": [1]})
    assert decode_artifact(parquet_bytes(table), PARQUET).equals(table)
