"""Ledger: signed, append-only, hash-chained entries (§6, ADR-004)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from sdp.kernel.domain.canonical import hash_document

LedgerKind = Literal["predeclaration", "approval"]


@dataclass(frozen=True)
class LedgerEntry:
    entry_id: str
    sequence: int
    kind: LedgerKind
    subject_id: str
    subject_hash: str
    recorded_at: datetime
    signer: str
    signature_method: str
    signature: str
    previous_entry_hash: str | None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def unsigned_document(self) -> dict[str, Any]:
        """The canonical document a signature covers (everything but the signature)."""
        return {
            "schema_version": "1.0.0",
            "entry_id": self.entry_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "subject_id": self.subject_id,
            "subject_hash": self.subject_hash,
            "recorded_at": self.recorded_at.isoformat().replace("+00:00", "Z"),
            "signer": self.signer,
            "previous_entry_hash": self.previous_entry_hash,
            "payload": dict(self.payload),
        }

    def to_document(self) -> dict[str, Any]:
        doc = self.unsigned_document()
        doc["signature"] = {"method": self.signature_method, "value": self.signature}
        return doc

    def entry_hash(self) -> str:
        return hash_document(self.to_document())

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> LedgerEntry:
        return cls(
            entry_id=doc["entry_id"],
            sequence=int(doc["sequence"]),
            kind=doc["kind"],
            subject_id=doc["subject_id"],
            subject_hash=doc["subject_hash"],
            recorded_at=datetime.fromisoformat(doc["recorded_at"].replace("Z", "+00:00")),
            signer=doc["signer"],
            signature_method=doc["signature"]["method"],
            signature=doc["signature"]["value"],
            previous_entry_hash=doc["previous_entry_hash"],
            payload=dict(doc.get("payload", {})),
        )


class Ledger(Protocol):
    def append(
        self, *, kind: LedgerKind, subject_id: str, subject_hash: str, signer: str, payload: Mapping[str, Any]
    ) -> LedgerEntry:
        """Sign and append an entry chained to the previous one; returns it once durable."""
        ...

    def find(self, subject_hash: str, kind: LedgerKind = "predeclaration") -> LedgerEntry | None: ...

    def entries(self) -> Sequence[LedgerEntry]: ...

    def verify(self) -> list[str]:
        """Problems with the chain or signatures; empty when intact."""
        ...
