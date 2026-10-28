"""ArtifactStore and DatasetRepository (§6)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sdp.kernel.domain.dataset import DatasetArtifact
from sdp.kernel.domain.ids import ArtifactId


class ArtifactStore(Protocol):
    """Content-addressed bytes, write-once. Every read verifies SHA-256 (NFR-003)."""

    def put(self, data: bytes, media_type: str, *, logical_path: str | None = None) -> ArtifactId:
        """Store bytes; idempotent for identical bytes. ``logical_path`` adds a human-browsable
        name (such as raw/<source>/<release>/<key>/part-0000.parquet) that resolves to the id."""
        ...

    def get(self, artifact_id: ArtifactId) -> bytes:
        """The bytes, verified against the id; raises IntegrityError on mismatch."""
        ...

    def exists(self, artifact_id: ArtifactId) -> bool: ...

    def media_type(self, artifact_id: ArtifactId) -> str: ...

    def resolve(self, logical_path: str) -> ArtifactId | None:
        """The artifact a logical path currently names, if any."""
        ...

    def link(self, logical_path: str, artifact_id: ArtifactId) -> None:
        """Atomically point a logical path at an existing artifact (used for mutable names such as
        an experiment's current manifest; the artifacts themselves never change)."""
        ...

    def free_bytes(self) -> int | None:
        """Free space on the backing store, or None when unbounded (DSI-010)."""
        ...


class DatasetRepository(Protocol):
    """DatasetArtifact metadata. Registering a different record under an existing id is refused (KRN-014)."""

    def add(self, artifact: DatasetArtifact) -> None: ...

    def get(self, artifact_id: ArtifactId) -> DatasetArtifact | None: ...

    def find_by_idempotency_key(self, key: str) -> DatasetArtifact | None: ...

    def children(self, parent: ArtifactId) -> Sequence[DatasetArtifact]: ...

    def list(self) -> Sequence[DatasetArtifact]: ...
