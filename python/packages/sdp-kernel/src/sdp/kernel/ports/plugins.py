"""ProtocolRegistry: how the kernel finds ScientificProtocol plugins without importing them (KRN-011)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sdp.kernel.science.protocol import ScientificProtocol


class ProtocolRegistry(Protocol):
    def get(self, protocol_name: str, version: str | None = None) -> ScientificProtocol: ...

    def list(self) -> Sequence[ScientificProtocol]: ...
