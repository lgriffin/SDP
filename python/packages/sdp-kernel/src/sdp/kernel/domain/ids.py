"""Identities. ArtifactId is content-addressed; the others are opaque strings with a shape."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NewType

from sdp.kernel.domain.canonical import is_sha256_id, sha256_id
from sdp.kernel.domain.errors import InvariantViolation

ExperimentId = NewType("ExperimentId", str)
HypothesisId = NewType("HypothesisId", str)
PredeclarationId = NewType("PredeclarationId", str)
DossierId = NewType("DossierId", str)
LedgerEntryId = NewType("LedgerEntryId", str)

_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True, order=True)
class ArtifactId:
    """``sha256:<hex>`` of canonical bytes; two identical files share one id (ADR-008)."""

    value: str

    def __post_init__(self) -> None:
        if not is_sha256_id(self.value):
            raise InvariantViolation(f"not a sha256 artifact id: {self.value!r}")

    @classmethod
    def of_bytes(cls, data: bytes) -> ArtifactId:
        return cls(sha256_id(data))

    def __str__(self) -> str:
        return self.value


def check_semver(version: str) -> str:
    if not _SEMVER.match(version):
        raise InvariantViolation(f"not a semantic version: {version!r}")
    return version


def semver_major(version: str) -> int:
    return int(check_semver(version).split(".")[0])


@dataclass(frozen=True, order=True)
class ProtocolId:
    """A science protocol identity, e.g. ``uc02-einstein-1919`` at ``1.0.0``."""

    name: str
    version: str

    def __post_init__(self) -> None:
        if not _SLUG.match(self.name):
            raise InvariantViolation(f"protocol name must be a lowercase slug: {self.name!r}")
        check_semver(self.version)

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"
