"""Discovers ScientificProtocol plugins through the sdp.protocols entry-point group (ADR-001).

The kernel never imports a science module by name (KRN-011); it only loads what is registered.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.metadata import entry_points

from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.science.protocol import ENTRY_POINT_GROUP, ScientificProtocol


@dataclass
class EntryPointProtocolRegistry:
    group: str = ENTRY_POINT_GROUP
    _loaded: dict[str, ScientificProtocol] = field(default_factory=dict)

    def _load(self) -> dict[str, ScientificProtocol]:
        if not self._loaded:
            for ep in entry_points(group=self.group):
                factory = ep.load()
                protocol = factory()
                if not isinstance(protocol, ScientificProtocol):
                    raise InvariantViolation(f"entry point {ep.name} does not implement ScientificProtocol")
                self._loaded[protocol.protocol_id.name] = protocol
        return self._loaded

    def get(self, protocol_name: str, version: str | None = None) -> ScientificProtocol:
        protocols = self._load()
        if protocol_name not in protocols:
            raise KeyError(f"no ScientificProtocol registered as {protocol_name!r}; installed: {sorted(protocols)}")
        protocol = protocols[protocol_name]
        if version is not None and protocol.protocol_id.version != version:
            raise InvariantViolation(
                f"{protocol_name} {version} requested but {protocol.protocol_id.version} is installed"
            )
        return protocol

    def list(self) -> Sequence[ScientificProtocol]:
        return list(self._load().values())
