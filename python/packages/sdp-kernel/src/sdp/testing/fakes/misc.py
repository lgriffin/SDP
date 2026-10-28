"""NullTracker, FakeDoiMinter and InMemoryProtocolRegistry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.ports.publication import DoiRecord
from sdp.kernel.science.protocol import ScientificProtocol


class NullTracker:
    def log_params(self, experiment_id: str, params: Mapping[str, Any]) -> None:
        return None

    def log_metrics(self, experiment_id: str, metrics: Mapping[str, float]) -> None:
        return None

    def set_tags(self, experiment_id: str, tags: Mapping[str, str]) -> None:
        return None


@dataclass
class FakeDoiMinter:
    """Mints DOIs under the DataCite test prefix 10.5072, as Zenodo's sandbox does."""

    deposits: list[dict[str, Any]] = field(default_factory=list)

    def publish(self, bundle: bytes, filename: str, metadata: Mapping[str, Any]) -> DoiRecord:
        n = len(self.deposits) + 1
        self.deposits.append({"filename": filename, "sha256": sha256_id(bundle), "metadata": dict(metadata)})
        doi = f"10.5072/sdp.{n}"
        return DoiRecord(doi=doi, url=f"https://doi.org/{doi}", deposition_id=str(n))


@dataclass
class InMemoryProtocolRegistry:
    protocols: dict[str, ScientificProtocol] = field(default_factory=dict)

    @classmethod
    def of(cls, *protocols: ScientificProtocol) -> InMemoryProtocolRegistry:
        return cls({p.protocol_id.name: p for p in protocols})

    def get(self, protocol_name: str, version: str | None = None) -> ScientificProtocol:
        protocol = self.protocols[protocol_name]
        if version is not None and protocol.protocol_id.version != version:
            raise InvariantViolation(f"{protocol_name} {version} requested, {protocol.protocol_id.version} registered")
        return protocol

    def list(self) -> Sequence[ScientificProtocol]:
        return list(self.protocols.values())
