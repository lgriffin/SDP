"""Pooling of observation sets is never implicit (§5, REL-004)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sdp.kernel.domain.errors import InvariantViolation, PoolingNotJustified


@dataclass(frozen=True)
class PoolingJustification:
    """Why several observation sets may be combined into one estimate."""

    set_ids: tuple[str, ...]
    reason: str
    predeclared: bool

    def __post_init__(self) -> None:
        if len(self.set_ids) < 2:
            raise InvariantViolation("pooling needs at least two observation sets")
        if not self.reason.strip():
            raise InvariantViolation("a pooling justification needs a reason")

    def covers(self, set_ids: Sequence[str]) -> bool:
        return set(set_ids) <= set(self.set_ids)


def require_pooling_justification(
    set_ids: Sequence[str], justification: PoolingJustification | None
) -> PoolingJustification:
    """Return the justification that allows pooling ``set_ids``, or refuse."""
    if justification is None or not justification.covers(set_ids):
        raise PoolingNotJustified(f"pooling {sorted(set_ids)} needs a PoolingJustification")
    if not justification.predeclared:
        raise PoolingNotJustified("a pooling justification must be predeclared")
    return justification
