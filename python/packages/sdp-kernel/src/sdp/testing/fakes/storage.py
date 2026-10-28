"""InMemoryArtifactStore and InMemoryDatasetRepository."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field

from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.domain.dataset import DatasetArtifact
from sdp.kernel.domain.errors import ImmutableArtifact, IntegrityError
from sdp.kernel.domain.ids import ArtifactId


@dataclass
class InMemoryArtifactStore:
    """Write-once, content-addressed, verified on read. ``capacity`` makes free space finite."""

    capacity: int | None = None
    _blobs: dict[str, bytes] = field(default_factory=dict)
    _media: dict[str, str] = field(default_factory=dict)
    _paths: dict[str, str] = field(default_factory=dict)

    def put(self, data: bytes, media_type: str, *, logical_path: str | None = None) -> ArtifactId:
        artifact = ArtifactId(sha256_id(data))
        if artifact.value not in self._blobs:
            self._blobs[artifact.value] = bytes(data)
            self._media[artifact.value] = media_type
        if logical_path is not None:
            self.link(logical_path, artifact)
        return artifact

    def get(self, artifact_id: ArtifactId) -> bytes:
        try:
            data = self._blobs[artifact_id.value]
        except KeyError:
            raise KeyError(f"no artifact {artifact_id}") from None
        if sha256_id(data) != artifact_id.value:
            raise IntegrityError(f"artifact {artifact_id} failed SHA-256 verification on read")
        return data

    def exists(self, artifact_id: ArtifactId) -> bool:
        return artifact_id.value in self._blobs

    def media_type(self, artifact_id: ArtifactId) -> str:
        return self._media[artifact_id.value]

    def resolve(self, logical_path: str) -> ArtifactId | None:
        value = self._paths.get(logical_path)
        return ArtifactId(value) if value else None

    def link(self, logical_path: str, artifact_id: ArtifactId) -> None:
        if artifact_id.value not in self._blobs:
            raise KeyError(f"cannot link {logical_path} to missing {artifact_id}")
        if logical_path.startswith("raw/") and self._paths.get(logical_path, artifact_id.value) != artifact_id.value:
            raise ImmutableArtifact(f"{logical_path} already names a different raw artifact (KRN-014)")
        self._paths[logical_path] = artifact_id.value

    def free_bytes(self) -> int | None:
        if self.capacity is None:
            return None
        return self.capacity - sum(len(b) for b in self._blobs.values())

    # test seam: simulate bit rot on the medium
    def corrupt(self, artifact_id: ArtifactId) -> None:
        data = bytearray(self._blobs[artifact_id.value])
        data[0] ^= 0xFF
        self._blobs[artifact_id.value] = bytes(data)


@dataclass
class InMemoryDatasetRepository:
    _items: dict[str, DatasetArtifact] = field(default_factory=dict)

    def add(self, artifact: DatasetArtifact) -> None:
        existing = self._items.get(artifact.artifact_id.value)
        if existing is not None:
            if existing.to_document() != artifact.to_document():
                raise ImmutableArtifact(
                    f"{artifact.artifact_id} is already registered with different metadata (KRN-014)"
                )
            return
        self._items[artifact.artifact_id.value] = copy.deepcopy(artifact)

    def get(self, artifact_id: ArtifactId) -> DatasetArtifact | None:
        item = self._items.get(artifact_id.value)
        return copy.deepcopy(item) if item else None

    def find_by_idempotency_key(self, key: str) -> DatasetArtifact | None:
        for item in self._items.values():
            if item.acquisition is not None and item.acquisition.idempotency_key == key:
                return copy.deepcopy(item)
        return None

    def children(self, parent: ArtifactId) -> Sequence[DatasetArtifact]:
        return [copy.deepcopy(i) for i in self._items.values() if parent in i.parents]

    def list(self) -> Sequence[DatasetArtifact]:
        return [copy.deepcopy(i) for i in self._items.values()]
