"""Investigate's registered field tests (Investigate §5). Installed through the
``sdp.investigation_tests`` entry point; the kernel never imports this package by name."""

from __future__ import annotations

from sdp.kernel.science.investigation import InvestigationTest
from sdp_investigate.astrometry import AstrometricCoherence, LongBaselineMotion, ParallaxConsistency
from sdp_investigate.imaging import ImageCounterpart
from sdp_investigate.photometry import ColourMagnitudeCoherence, PhotometricCorroboration, Variability

__all__ = [
    "AstrometricCoherence",
    "ColourMagnitudeCoherence",
    "ImageCounterpart",
    "LongBaselineMotion",
    "ParallaxConsistency",
    "PhotometricCorroboration",
    "Variability",
    "registered_tests",
]


def registered_tests() -> tuple[InvestigationTest, ...]:
    return (
        ParallaxConsistency(),
        ColourMagnitudeCoherence(),
        LongBaselineMotion(),
        Variability(),
        PhotometricCorroboration(),
        AstrometricCoherence(),
        ImageCounterpart(),
    )
