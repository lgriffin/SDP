"""Fixed clock, sequential ids, real SHA-256 (§6 determinism seams)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sdp.kernel.domain.canonical import sha256_id


@dataclass
class FixedClock:
    """Starts at a fixed instant and advances by ``step`` on every call, so ordering is visible."""

    start: datetime = field(default_factory=lambda: datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC))
    step: timedelta = timedelta(seconds=1)
    _calls: int = 0

    def now(self) -> datetime:
        value = self.start + self.step * self._calls
        self._calls += 1
        return value


@dataclass
class SequentialIds:
    _counters: dict[str, int] = field(default_factory=dict)

    def new(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}-{n:06d}"


class Sha256Hasher:
    def sha256(self, data: bytes) -> str:
        return sha256_id(data)
