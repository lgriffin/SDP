"""Clock, IdGenerator and Hasher: the determinism seams (§6)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware UTC now."""
        ...


class IdGenerator(Protocol):
    def new(self, prefix: str) -> str:
        """A fresh identifier such as ``exp-01J...``."""
        ...


class Hasher(Protocol):
    def sha256(self, data: bytes) -> str:
        """``sha256:<hex>`` of the bytes."""
        ...
