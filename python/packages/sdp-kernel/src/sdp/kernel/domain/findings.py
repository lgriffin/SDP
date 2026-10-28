"""Findings: the scientific statements interpret() makes, and the oracle status they rest on."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from sdp.kernel.domain.errors import InvariantViolation

FindingValue = float | int | bool | str | list[Any] | dict[str, Any] | None


@dataclass(frozen=True)
class Finding:
    """A named value with an optional uncertainty and unit string."""

    name: str
    value: FindingValue
    uncertainty: float | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise InvariantViolation("a finding needs a name")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise InvariantViolation(f"finding {self.name!r} is not finite")

    def to_document(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "uncertainty": self.uncertainty, "unit": self.unit}

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Finding:
        return cls(doc["name"], doc["value"], doc.get("uncertainty"), doc.get("unit"))


@dataclass(frozen=True)
class OracleStatus:
    """Whether an oracle value has a second-pass verification record (§9, §11.2)."""

    oracle_id: str
    verified: bool
    note: str = ""


@dataclass(frozen=True)
class Findings:
    """Everything a validate() call may look at: named findings plus the oracles they used."""

    items: Mapping[str, Finding] = field(default_factory=dict)
    oracles: Mapping[str, OracleStatus] = field(default_factory=dict)
    narrative: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def of(
        cls,
        *findings: Finding,
        oracles: Mapping[str, OracleStatus] | None = None,
        narrative: Mapping[str, Any] | None = None,
    ) -> Findings:
        items: dict[str, Finding] = {}
        for f in findings:
            if f.name in items:
                raise InvariantViolation(f"duplicate finding {f.name!r}")
            items[f.name] = f
        return cls(items, dict(oracles or {}), dict(narrative or {}))

    def __iter__(self) -> Iterator[Finding]:
        return iter(self.items.values())

    def __contains__(self, name: object) -> bool:
        return name in self.items

    def get(self, name: str) -> Finding | None:
        return self.items.get(name)

    def value(self, name: str) -> FindingValue:
        return self.items[name].value

    def oracle_verified(self, oracle_id: str) -> bool:
        status = self.oracles.get(oracle_id)
        return bool(status and status.verified)
