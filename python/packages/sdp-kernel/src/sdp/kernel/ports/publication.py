"""BundlePublisher: deposit an RO-Crate bundle and mint a DOI (release phase)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class DoiRecord:
    doi: str
    url: str
    deposition_id: str


class BundlePublisher(Protocol):
    def publish(self, bundle: bytes, filename: str, metadata: Mapping[str, Any]) -> DoiRecord: ...
