"""EventLog: append-only domain events with sequence numbers (§6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sdp.kernel.domain.events import DomainEvent


@dataclass(frozen=True)
class StoredEvent:
    sequence: int
    event: DomainEvent


class EventLog(Protocol):
    def append(self, events: Sequence[DomainEvent]) -> Sequence[int]:
        """Append in order inside the current unit of work; returns the sequence numbers."""
        ...

    def read(self, after: int = 0, limit: int | None = None) -> Sequence[StoredEvent]: ...

    def for_aggregate(self, aggregate_id: str) -> Sequence[StoredEvent]: ...
