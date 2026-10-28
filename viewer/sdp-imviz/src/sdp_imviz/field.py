"""The field the viewer hands to InvestigateField: its footprint as DS9 region text in ICRS degrees."""

from __future__ import annotations

from typing import Protocol


class PixelToWorld(Protocol):
    def pixel_to_world_values(self, x: list[float], y: list[float]) -> tuple[list[float], list[float]]: ...


def footprint_region(
    wcs: PixelToWorld, x_min: float, x_max: float, y_min: float, y_max: float, nx: int, ny: int
) -> str:
    """The visible rectangle, clipped to the image, as ``icrs;polygon(...)`` (0-based pixel edges)."""
    x0, x1 = max(-0.5, min(x_min, x_max)), min(nx - 0.5, max(x_min, x_max))
    y0, y1 = max(-0.5, min(y_min, y_max)), min(ny - 0.5, max(y_min, y_max))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("the viewer shows no part of the image")
    ra, dec = wcs.pixel_to_world_values([x0, x1, x1, x0], [y0, y0, y1, y1])
    pairs = ",".join(f"{float(r) % 360.0:.7f},{float(d):.7f}" for r, d in zip(ra, dec, strict=True))
    return f"icrs;polygon({pairs})"


def circle_region(ra_deg: float, dec_deg: float, radius_deg: float) -> str:
    """A circular subset as ``icrs;circle(...)``."""
    if radius_deg <= 0:
        raise ValueError("a circle has a positive radius")
    return f"icrs;circle({ra_deg % 360.0:.7f},{dec_deg:.7f},{radius_deg:.7f})"
