from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from sdp.kernel.application.acquisition import raw_part_path
from sdp.kernel.application.media import PARQUET, decode_artifact
from sdp.kernel.domain.archive import AcquisitionHandle, CatalogueQuery, Cone
from sdp.kernel.domain.dataset import ArtifactKind, DatasetStatus
from sdp.kernel.domain.errors import (
    CurationBlocked,
    ImmutableArtifact,
    IntegrityError,
    InvariantViolation,
    StorageExhausted,
)
from sdp.kernel.domain.provenance import qualified
from sdp.kernel.ports.driving import AcquireDatasetCommand
from sdp.testing.catalogues import FAKE_GAIA, fixture_catalogue
from sdp.testing.fakes import FakeArchive
from sdp.testing.synthetic import SyntheticLinearProtocol
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T4")

QUERY = CatalogueQuery(
    "fake-gaia",
    "gaia:DR3",
    Cone(56.5, 24.25, 1.0),
    ("source_id", "ra", "dec", "phot_g_mean_mag"),
    row_cap=100,
    chunk_rows=100,
)


def kernel_with(archive: FakeArchive, capacity: int | None = None, min_free_bytes: int = 0) -> InMemoryKernel:
    return build_in_memory_kernel(
        SyntheticLinearProtocol(), gateways={"fake-gaia": archive}, capacity=capacity, min_free_bytes=min_free_bytes
    )


def event_names(k: InMemoryKernel) -> list[str]:
    return [stored.event.name for stored in k.kernel.query_evidence.events()]


@pytest.mark.tier("T3")
@pytest.mark.req("DSI-002", "DSI-005")
def test_acquisition_records_query_release_licence_citation_checksum_size_and_time() -> None:
    archive = FakeArchive(FAKE_GAIA, fixture_catalogue())
    k = kernel_with(archive)
    result = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(QUERY))

    raw = result.raw
    assert raw.kind is ArtifactKind.RAW and raw.status is DatasetStatus.ACQUIRED
    assert raw.acquisition is not None
    assert raw.acquisition.rendered_query.startswith("FAKE ")
    assert raw.acquisition.source_release == "gaia:DR3"
    assert raw.source_ref.licence == FAKE_GAIA.licence and raw.source_ref.citation == FAKE_GAIA.citation
    assert raw.acquisition.checksum == raw.artifact_id
    assert raw.acquisition.byte_count == raw.byte_count == len(k.store.get(raw.artifact_id))
    assert raw.acquisition.acquired_at is not None
    assert len(raw.acquisition.parts) == 2  # 50-row chunks forced by max_rows_per_call
    assert "DatasetAcquired" in event_names(k)

    curated = result.curated
    assert curated is not None and curated.kind is ArtifactKind.CURATED
    assert curated.dialect is not None and curated.dialect.dialect_id and curated.dialect.dialect_version
    assert curated.parents == (raw.artifact_id,)
    assert qualified(raw.artifact_id.value) in k.kernel.query_evidence.lineage(curated.artifact_id.value)


@pytest.mark.tier("T3")
@pytest.mark.req("DSI-004")
def test_repeated_idempotency_key_returns_existing_artifact_without_a_remote_call() -> None:
    archive = FakeArchive(FAKE_GAIA, fixture_catalogue())
    k = kernel_with(archive)
    first = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(QUERY))
    calls = archive.fetch_calls

    again = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(QUERY))

    assert again.reused and again.raw == first.raw and again.curated == first.curated
    assert archive.fetch_calls == calls


class _OtherRoute:
    """The same archive reached another way, as the local Gaia bulk copy is: the same rows under another
    idempotency key and rendered text."""

    def __init__(self, inner: FakeArchive) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def query(self, q: CatalogueQuery) -> AcquisitionHandle:
        handle = self.inner.query(q)
        return replace(handle, rendered_text="BULK " + handle.rendered_text, idempotency_key="sha256:" + "0" * 64)


@pytest.mark.tier("T3")
@pytest.mark.req("KRN-014", "DSI-004")
def test_the_same_rows_by_another_route_reuse_the_record_already_held() -> None:
    archive = FakeArchive(FAKE_GAIA, fixture_catalogue())
    k = kernel_with(archive)
    first = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(QUERY))
    datasets = len(k.kernel.query_evidence.datasets())

    by_bulk = replace(k.kernel.acquire_dataset, gateways={"fake-gaia": _OtherRoute(archive)})
    again = by_bulk.execute(AcquireDatasetCommand(QUERY))

    assert again.reused and again.raw == first.raw and again.curated == first.curated
    assert len(k.kernel.query_evidence.datasets()) == datasets, "no second record under the same id"


@pytest.mark.tier("T5")
@pytest.mark.req("DSI-003")
def test_corrupted_chunk_is_discarded_rejected_and_retried() -> None:
    archive = FakeArchive(FAKE_GAIA, fixture_catalogue(), corrupt_parts={1: 1})
    k = kernel_with(archive)
    result = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(QUERY))

    assert result.raw.status is DatasetStatus.ACQUIRED
    assert event_names(k).count("DatasetRejected") == 1
    assert archive.fetch_calls == 2
    table = decode_artifact(k.store.get(result.raw.artifact_id), PARQUET)
    assert table.num_rows == len(set(table.column("source_id").to_pylist()))


@pytest.mark.tier("T5")
@pytest.mark.req("DSI-003")
def test_retries_are_bounded() -> None:
    archive = FakeArchive(FAKE_GAIA, fixture_catalogue(), corrupt_parts={0: 10})
    k = kernel_with(archive)
    with pytest.raises(IntegrityError):
        k.kernel.acquire_dataset.execute(AcquireDatasetCommand(QUERY))
    assert event_names(k).count("DatasetRejected") == 3
    assert not k.kernel.query_evidence.datasets()


@pytest.mark.req("DSI-006")
def test_missing_licence_blocks_curation() -> None:
    archive = FakeArchive(replace(FAKE_GAIA, licence=""), fixture_catalogue())
    k = kernel_with(archive)
    with pytest.raises(CurationBlocked):
        k.kernel.acquire_dataset.execute(AcquireDatasetCommand(QUERY))
    datasets = k.kernel.query_evidence.datasets()
    assert [d.kind for d in datasets] == [ArtifactKind.RAW]


@pytest.mark.tier("T5")
@pytest.mark.req("DSI-010")
def test_low_free_space_refuses_new_acquisitions() -> None:
    archive = FakeArchive(FAKE_GAIA, fixture_catalogue())
    k = kernel_with(archive, capacity=1_000, min_free_bytes=10_000)
    with pytest.raises(StorageExhausted):
        k.kernel.acquire_dataset.execute(AcquireDatasetCommand(QUERY))
    assert archive.fetch_calls == 0


@pytest.mark.tier("T3")
@pytest.mark.req("KRN-014")
def test_raw_artifacts_cannot_be_replaced_through_workflows() -> None:
    archive = FakeArchive(FAKE_GAIA, fixture_catalogue())
    k = kernel_with(archive)
    raw = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(QUERY)).raw
    assert raw.acquisition is not None
    path = raw_part_path("fake-gaia", "gaia:DR3", raw.acquisition.idempotency_key, 0)
    assert k.store.resolve(path) == raw.acquisition.parts[0].artifact_id
    with pytest.raises(ImmutableArtifact):
        k.store.put(b"tampered", PARQUET, logical_path=path)
    with pytest.raises(InvariantViolation):
        replace(raw, status=DatasetStatus.CURATED)


@pytest.mark.tier("T3")
@pytest.mark.req("NFR-003")
def test_reads_verify_sha256() -> None:
    archive = FakeArchive(FAKE_GAIA, fixture_catalogue())
    k = kernel_with(archive)
    raw = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(QUERY)).raw
    k.store.corrupt(raw.artifact_id)
    with pytest.raises(IntegrityError):
        k.store.get(raw.artifact_id)


@pytest.mark.tier("T3")
@pytest.mark.req("DSI-012")
def test_an_acquisition_that_fills_its_row_cap_says_so() -> None:
    archive = FakeArchive(FAKE_GAIA, fixture_catalogue())
    k = kernel_with(archive)
    roomy = replace(QUERY, row_cap=100_000, chunk_rows=50)
    whole = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(roomy))
    assert 0 < whole.rows < roomy.row_cap and not whole.reached_row_cap

    capped = replace(QUERY, row_cap=whole.rows - 1, chunk_rows=50)
    cut = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(capped))
    assert cut.rows == capped.row_cap and cut.reached_row_cap

    again = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(capped))
    assert again.reused and again.rows == capped.row_cap and again.reached_row_cap
