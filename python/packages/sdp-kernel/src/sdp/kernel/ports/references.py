"""ReferenceCatalogue: one catalogue a scientist can compare a candidate against (INV-024).

An adapter answers a cone with canonical rows (the names the dossier's catalogue rows use, plus whatever
the catalogue adds: ``object_type``, ``main_id``, ``references``, ``variable_type``, ``period_d``, ...)
and the archive's answer as it came, which the kernel stores so the comparison can be checked later.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ReferenceAnswer:
    rows: tuple[Mapping[str, Any], ...]
    raw: bytes
    media_type: str


class ReferenceCatalogue(Protocol):
    @property
    def source_id(self) -> str: ...

    @property
    def title(self) -> str:
        """What a scientist calls it: "SIMBAD", "Gaia DR3", "AAVSO VSX"."""
        ...

    @property
    def release_label(self) -> str: ...

    @property
    def citation(self) -> str: ...

    @property
    def description(self) -> str:
        """One plain sentence on what it tells you about a candidate."""
        ...

    def cone(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> ReferenceAnswer:
        """Every row within ``radius_arcsec`` of the position, at most a few dozen; raises on no answer."""
        ...
