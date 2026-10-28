"""DatasetRepository contract."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.domain.dataset import AcquisitionRecord, DatasetArtifact, DatasetStatus, DialectRef
from sdp.kernel.domain.errors import ImmutableArtifact
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.ports.storage import DatasetRepository

SOURCE = SourceRef(SourceKind.ARCHIVE, "esa.gaia.tap", "CC BY-SA 3.0 IGO", "Gaia DR3", release_label="gaia:DR3")


def raw_artifact(payload: bytes = b"raw", key: str = "sha256:" + "1" * 64) -> DatasetArtifact:
    artifact = ArtifactId(sha256_id(payload))
    record = AcquisitionRecord("SELECT 1", key, "gaia:DR3", datetime(2026, 10, 28, tzinfo=UTC), artifact, len(payload))
    return DatasetArtifact.acquired_raw(
        actual=artifact,
        media_type="application/vnd.apache.parquet",
        byte_count=len(payload),
        source_ref=SOURCE,
        acquisition=record,
    )


class DatasetRepositoryContract:
    def test_add_then_get_round_trips(self, dataset_repository: DatasetRepository) -> None:
        raw = raw_artifact()
        dataset_repository.add(raw)
        assert dataset_repository.get(raw.artifact_id) == raw

    @pytest.mark.req("DSI-004")
    def test_find_by_idempotency_key(self, dataset_repository: DatasetRepository) -> None:
        raw = raw_artifact(key="sha256:" + "2" * 64)
        dataset_repository.add(raw)
        assert dataset_repository.find_by_idempotency_key("sha256:" + "2" * 64) == raw
        assert dataset_repository.find_by_idempotency_key("sha256:" + "3" * 64) is None

    @pytest.mark.req("DSI-005")
    def test_curated_children_cite_their_raw_parent(self, dataset_repository: DatasetRepository) -> None:
        raw = raw_artifact()
        dataset_repository.add(raw)
        curated = raw.curate(
            curated_id=ArtifactId(sha256_id(b"curated")),
            byte_count=7,
            dialect=DialectRef("gaia-dr3", "1.0.0"),
            media_type="application/vnd.apache.parquet",
        )
        dataset_repository.add(curated)
        children = dataset_repository.children(raw.artifact_id)
        assert [c.artifact_id for c in children] == [curated.artifact_id]
        assert children[0].dialect == DialectRef("gaia-dr3", "1.0.0")
        assert children[0].status is DatasetStatus.CURATED

    @pytest.mark.req("KRN-014")
    def test_registered_raw_metadata_cannot_be_rewritten(self, dataset_repository: DatasetRepository) -> None:
        raw = raw_artifact()
        dataset_repository.add(raw)
        dataset_repository.add(raw)  # identical re-registration is harmless
        tampered = DatasetArtifact(
            artifact_id=raw.artifact_id,
            kind=raw.kind,
            status=DatasetStatus.REJECTED,
            media_type=raw.media_type,
            byte_count=raw.byte_count,
            source_ref=raw.source_ref,
            acquisition=raw.acquisition,
            rejection_reason="edited",
        )
        with pytest.raises(ImmutableArtifact):
            dataset_repository.add(tampered)
        assert dataset_repository.get(raw.artifact_id) == raw

    def test_list_returns_everything(self, dataset_repository: DatasetRepository) -> None:
        a, b = raw_artifact(b"a"), raw_artifact(b"b", "sha256:" + "4" * 64)
        dataset_repository.add(a)
        dataset_repository.add(b)
        assert {x.artifact_id for x in dataset_repository.list()} == {a.artifact_id, b.artifact_id}
