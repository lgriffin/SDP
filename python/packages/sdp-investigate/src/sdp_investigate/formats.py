"""Canonical product formats the tests read. They live in the kernel (``sdp.kernel.science.products``)
so the product dialects in sdp.adapters write exactly what these tests parse."""

from sdp.kernel.science.products import (
    ASTROMETRY,
    LIGHTCURVE,
    LightCurve,
    PositionHistory,
    lightcurve_csv,
    parse_lightcurve,
    parse_positions,
    positions_csv,
)

__all__ = [
    "ASTROMETRY",
    "LIGHTCURVE",
    "LightCurve",
    "PositionHistory",
    "lightcurve_csv",
    "parse_lightcurve",
    "parse_positions",
    "positions_csv",
]
