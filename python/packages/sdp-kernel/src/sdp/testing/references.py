"""A reference catalogue that answers from rows it was given (INV-024): for use-case, API and UI tests.

The rows are invented; nothing here stands for a real catalogue's contents."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sdp.kernel.domain.references import separation_arcsec
from sdp.kernel.ports.references import ReferenceAnswer


@dataclass
class FakeReferenceCatalogue:
    source_id: str
    title: str
    rows: Sequence[Mapping[str, Any]] = ()
    release_label: str = "fake:1"
    citation: str = "Invented for tests"
    description: str = "Invented rows for tests."
    #: raised instead of answering, as an archive that is down
    failure: Exception | None = None
    asked: list[tuple[float, float, float]] = field(default_factory=list)

    def cone(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> ReferenceAnswer:
        self.asked.append((ra_deg, dec_deg, radius_arcsec))
        if self.failure is not None:
            raise self.failure
        near = tuple(
            r for r in self.rows if separation_arcsec(ra_deg, dec_deg, r["ra_deg"], r["dec_deg"]) <= radius_arcsec
        )
        return ReferenceAnswer(near, json.dumps(list(near), sort_keys=True).encode(), "application/json")


def offset(ra_deg: float, dec_deg: float, east_arcsec: float = 0.0, north_arcsec: float = 0.0) -> tuple[float, float]:
    """A position ``east_arcsec`` east and ``north_arcsec`` north of another (small offsets)."""
    import math

    return ra_deg + east_arcsec / 3600.0 / math.cos(math.radians(dec_deg)), dec_deg + north_arcsec / 3600.0
