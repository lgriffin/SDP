"""NumericalPolicy: how a TaskKind's numbers are compared and reproduced."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np

from sdp.kernel.domain.errors import InvariantViolation


class DeterminismClass(StrEnum):
    BITWISE = "bitwise"
    TOLERANCE = "tolerance"
    STATISTICAL = "statistical"


class SeedHandling(StrEnum):
    NONE = "none"
    EXPLICIT = "explicit"
    DERIVED = "derived"


_DTYPES = frozenset({"float32", "float64", "int64", "bool", "object"})


@dataclass(frozen=True)
class NumericalPolicy:
    """Every TaskKind names one; tests compare with it rather than with ad-hoc tolerances (§11.2)."""

    dtype: str = "float64"
    rtol: float = 1e-9
    atol: float = 0.0
    determinism: DeterminismClass = DeterminismClass.TOLERANCE
    seed_handling: SeedHandling = SeedHandling.EXPLICIT

    def __post_init__(self) -> None:
        if self.dtype not in _DTYPES:
            raise InvariantViolation(f"unknown dtype {self.dtype!r}")
        if self.rtol < 0 or self.atol < 0:
            raise InvariantViolation("tolerances must be non-negative")
        if self.determinism is DeterminismClass.BITWISE and (self.rtol or self.atol):
            raise InvariantViolation("a bitwise policy cannot carry tolerances")

    def agrees(self, actual: Any, expected: Any) -> bool:
        """True when two values agree under this policy (exact equality for bitwise)."""
        a = np.asarray(actual)
        b = np.asarray(expected)
        if a.shape != b.shape:
            return False
        if self.determinism is DeterminismClass.BITWISE or a.dtype.kind not in "fc":
            return bool(np.array_equal(a, b, equal_nan=a.dtype.kind in "fc"))
        return bool(np.allclose(a, b, rtol=self.rtol, atol=self.atol, equal_nan=True))

    def to_document(self) -> dict[str, Any]:
        return {
            "dtype": self.dtype,
            "rtol": self.rtol,
            "atol": self.atol,
            "determinism": self.determinism.value,
            "seed_handling": self.seed_handling.value,
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> NumericalPolicy:
        return cls(
            dtype=doc["dtype"],
            rtol=float(doc["rtol"]),
            atol=float(doc["atol"]),
            determinism=DeterminismClass(doc["determinism"]),
            seed_handling=SeedHandling(doc["seed_handling"]),
        )


BITWISE = NumericalPolicy(rtol=0.0, atol=0.0, determinism=DeterminismClass.BITWISE)
