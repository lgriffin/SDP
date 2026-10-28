"""DatasetArtifact: content-addressed dataset metadata (§5, ADR-008)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sdp.kernel.domain.errors import CurationBlocked, IntegrityError, InvariantViolation
from sdp.kernel.domain.ids import ArtifactId, check_semver
from sdp.kernel.domain.sources import SourceRef

SCHEMA_VERSION = "1.0.0"


class ArtifactKind(StrEnum):
    RAW = "raw"
    CURATED = "curated"
    DERIVED = "derived"


class DatasetStatus(StrEnum):
    ACQUIRED = "acquired"
    REJECTED = "rejected"
    CURATED = "curated"


@dataclass(frozen=True)
class DialectRef:
    dialect_id: str
    dialect_version: str

    def __post_init__(self) -> None:
        check_semver(self.dialect_version)


@dataclass(frozen=True)
class AcquisitionPart:
    part: int
    artifact_id: ArtifactId
    byte_count: int
    row_count: int


@dataclass(frozen=True)
class AcquisitionRecord:
    """Rendered query, idempotency key, release, time, checksum, bytes and parts (DSI-002)."""

    rendered_query: str
    idempotency_key: str
    source_release: str
    acquired_at: datetime
    checksum: ArtifactId
    byte_count: int
    parts: tuple[AcquisitionPart, ...] = ()


@dataclass(frozen=True)
class DatasetArtifact:
    artifact_id: ArtifactId
    kind: ArtifactKind
    status: DatasetStatus
    media_type: str
    byte_count: int
    source_ref: SourceRef
    dialect: DialectRef | None = None
    parents: tuple[ArtifactId, ...] = ()
    acquisition: AcquisitionRecord | None = None
    rejection_reason: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind is ArtifactKind.CURATED:
            if self.dialect is None or not self.parents:
                raise InvariantViolation("curated datasets carry a dialect and their raw parent (DSI-005)")
            if self.status is not DatasetStatus.CURATED:
                raise InvariantViolation("a curated artifact has status curated")
        if self.kind is ArtifactKind.RAW and self.status is DatasetStatus.CURATED:
            raise InvariantViolation("raw artifacts are never curated in place; curation derives a new artifact")
        if self.status is DatasetStatus.REJECTED and not self.rejection_reason:
            raise InvariantViolation("a rejected artifact records why")

    @classmethod
    def acquired_raw(
        cls,
        *,
        actual: ArtifactId,
        media_type: str,
        byte_count: int,
        source_ref: SourceRef,
        acquisition: AcquisitionRecord,
    ) -> DatasetArtifact:
        """A raw artifact whose checksum differs from its acquisition record is Rejected, never Curated."""
        if actual != acquisition.checksum:
            return cls(
                artifact_id=actual,
                kind=ArtifactKind.RAW,
                status=DatasetStatus.REJECTED,
                media_type=media_type,
                byte_count=byte_count,
                source_ref=source_ref,
                acquisition=acquisition,
                rejection_reason=f"checksum {actual} differs from acquisition record {acquisition.checksum}",
            )
        return cls(
            artifact_id=actual,
            kind=ArtifactKind.RAW,
            status=DatasetStatus.ACQUIRED,
            media_type=media_type,
            byte_count=byte_count,
            source_ref=source_ref,
            acquisition=acquisition,
        )

    def curate(
        self, *, curated_id: ArtifactId, byte_count: int, dialect: DialectRef, media_type: str
    ) -> DatasetArtifact:
        if self.status is not DatasetStatus.ACQUIRED or self.kind is not ArtifactKind.RAW:
            raise IntegrityError(f"{self.artifact_id} is {self.status}; only acquired raw data can be curated")
        if not self.source_ref.has_licence:
            raise CurationBlocked(f"{self.source_ref.identity} declares no licence (DSI-006)")
        return DatasetArtifact(
            artifact_id=curated_id,
            kind=ArtifactKind.CURATED,
            status=DatasetStatus.CURATED,
            media_type=media_type,
            byte_count=byte_count,
            source_ref=self.source_ref,
            dialect=dialect,
            parents=(self.artifact_id,),
        )

    def to_document(self) -> dict[str, Any]:
        acq = self.acquisition
        return {
            "schema_version": SCHEMA_VERSION,
            "artifact_id": self.artifact_id.value,
            "kind": self.kind.value,
            "status": self.status.value,
            "media_type": self.media_type,
            "byte_count": self.byte_count,
            "source_ref": self.source_ref.to_document(),
            "dialect": (
                {"dialect_id": self.dialect.dialect_id, "dialect_version": self.dialect.dialect_version}
                if self.dialect
                else None
            ),
            "parents": [p.value for p in self.parents],
            "acquisition": (
                {
                    "rendered_query": acq.rendered_query,
                    "idempotency_key": acq.idempotency_key,
                    "source_release": acq.source_release,
                    "acquired_at": acq.acquired_at.isoformat().replace("+00:00", "Z"),
                    "checksum": acq.checksum.value,
                    "byte_count": acq.byte_count,
                    "parts": [
                        {
                            "part": p.part,
                            "artifact_id": p.artifact_id.value,
                            "byte_count": p.byte_count,
                            "row_count": p.row_count,
                        }
                        for p in acq.parts
                    ],
                }
                if acq
                else None
            ),
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> DatasetArtifact:
        acq = doc.get("acquisition")
        dialect = doc.get("dialect")
        return cls(
            artifact_id=ArtifactId(doc["artifact_id"]),
            kind=ArtifactKind(doc["kind"]),
            status=DatasetStatus(doc["status"]),
            media_type=doc["media_type"],
            byte_count=int(doc["byte_count"]),
            source_ref=SourceRef.from_document(doc["source_ref"]),
            dialect=DialectRef(dialect["dialect_id"], dialect["dialect_version"]) if dialect else None,
            parents=tuple(ArtifactId(p) for p in doc.get("parents", [])),
            acquisition=(
                AcquisitionRecord(
                    rendered_query=acq["rendered_query"],
                    idempotency_key=acq["idempotency_key"],
                    source_release=acq["source_release"],
                    acquired_at=datetime.fromisoformat(acq["acquired_at"].replace("Z", "+00:00")),
                    checksum=ArtifactId(acq["checksum"]),
                    byte_count=int(acq["byte_count"]),
                    parts=tuple(
                        AcquisitionPart(p["part"], ArtifactId(p["artifact_id"]), p["byte_count"], p["row_count"])
                        for p in acq.get("parts", [])
                    ),
                )
                if acq
                else None
            ),
            rejection_reason=doc.get("rejection_reason"),
        )
