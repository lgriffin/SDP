"""InMemoryLedger: hash-chained entries signed with a test HMAC key."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.ports.determinism import Clock
from sdp.kernel.ports.ledger import LedgerEntry, LedgerKind
from sdp.testing.fakes.determinism import FixedClock


def hmac_signature(key: bytes, entry: LedgerEntry) -> str:
    return hmac.new(key, canonical_json(entry.unsigned_document()).encode(), hashlib.sha256).hexdigest()


@dataclass
class InMemoryLedger:
    clock: Clock = field(default_factory=FixedClock)
    key: bytes = b"sdp-test-ledger-key"
    _entries: list[LedgerEntry] = field(default_factory=list)

    def append(
        self, *, kind: LedgerKind, subject_id: str, subject_hash: str, signer: str, payload: Mapping[str, Any]
    ) -> LedgerEntry:
        sequence = len(self._entries)
        previous = self._entries[-1].entry_hash() if self._entries else None
        unsigned = LedgerEntry(
            entry_id=f"led-{sequence + 1:06d}",
            sequence=sequence,
            kind=kind,
            subject_id=subject_id,
            subject_hash=subject_hash,
            recorded_at=self.clock.now(),
            signer=signer,
            signature_method="test-hmac",
            signature="",
            previous_entry_hash=previous,
            payload=dict(payload),
        )
        entry = LedgerEntry(**{**unsigned.__dict__, "signature": hmac_signature(self.key, unsigned)})
        self._entries.append(entry)
        return entry

    def find(self, subject_hash: str, kind: LedgerKind = "predeclaration") -> LedgerEntry | None:
        for entry in self._entries:
            if entry.subject_hash == subject_hash and entry.kind == kind:
                return entry
        return None

    def entries(self) -> Sequence[LedgerEntry]:
        return list(self._entries)

    def verify(self) -> list[str]:
        problems: list[str] = []
        previous: str | None = None
        for i, entry in enumerate(self._entries):
            if entry.sequence != i:
                problems.append(f"{entry.entry_id}: sequence {entry.sequence} at position {i}")
            if entry.previous_entry_hash != previous:
                problems.append(f"{entry.entry_id}: chain broken")
            if not hmac.compare_digest(entry.signature, hmac_signature(self.key, entry)):
                problems.append(f"{entry.entry_id}: bad signature")
            previous = entry.entry_hash()
        return problems

    # test seam: tamper with history the way a careless edit would
    def tamper(self, index: int, **changes: Any) -> None:
        self._entries[index] = LedgerEntry(**{**self._entries[index].__dict__, **changes})
