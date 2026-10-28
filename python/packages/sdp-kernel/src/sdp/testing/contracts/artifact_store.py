"""ArtifactStore contract: content-addressed, write-once, verified on read."""

from __future__ import annotations

import pytest

from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.domain.errors import ImmutableArtifact
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.ports.storage import ArtifactStore


class ArtifactStoreContract:
    @pytest.mark.req("NFR-003")
    def test_put_returns_the_sha256_of_the_bytes(self, artifact_store: ArtifactStore) -> None:
        data = b"hello evidence"
        assert artifact_store.put(data, "text/plain") == ArtifactId(sha256_id(data))

    def test_identical_bytes_share_one_id(self, artifact_store: ArtifactStore) -> None:
        a = artifact_store.put(b"same", "text/plain")
        b = artifact_store.put(b"same", "text/plain")
        assert a == b

    @pytest.mark.req("NFR-003")
    def test_get_returns_exactly_what_was_put(self, artifact_store: ArtifactStore) -> None:
        artifact = artifact_store.put(b"\x00\x01payload", "application/octet-stream")
        assert artifact_store.get(artifact) == b"\x00\x01payload"
        assert artifact_store.media_type(artifact) == "application/octet-stream"

    def test_missing_artifact_is_reported(self, artifact_store: ArtifactStore) -> None:
        missing = ArtifactId(sha256_id(b"never stored"))
        assert not artifact_store.exists(missing)
        with pytest.raises(KeyError):
            artifact_store.get(missing)

    def test_logical_paths_resolve_to_artifacts(self, artifact_store: ArtifactStore) -> None:
        artifact = artifact_store.put(b"manifest v1", "application/json", logical_path="experiments/p/e/manifest.json")
        assert artifact_store.resolve("experiments/p/e/manifest.json") == artifact
        newer = artifact_store.put(b"manifest v2", "application/json")
        artifact_store.link("experiments/p/e/manifest.json", newer)
        assert artifact_store.resolve("experiments/p/e/manifest.json") == newer
        assert artifact_store.get(artifact) == b"manifest v1"
        assert artifact_store.resolve("nowhere") is None

    @pytest.mark.req("KRN-014")
    def test_raw_paths_are_write_once(self, artifact_store: ArtifactStore) -> None:
        first = artifact_store.put(
            b"raw part", "application/vnd.apache.parquet", logical_path="raw/s/r/k/part-0000.parquet"
        )
        artifact_store.put(b"raw part", "application/vnd.apache.parquet", logical_path="raw/s/r/k/part-0000.parquet")
        other = artifact_store.put(b"different", "application/vnd.apache.parquet")
        with pytest.raises(ImmutableArtifact):
            artifact_store.link("raw/s/r/k/part-0000.parquet", other)
        assert artifact_store.resolve("raw/s/r/k/part-0000.parquet") == first

    def test_free_bytes_is_none_or_non_negative(self, artifact_store: ArtifactStore) -> None:
        free = artifact_store.free_bytes()
        assert free is None or free >= 0
