"""The deflection models (§9): each maps star geometry to a predicted displacement Quantity.

alpha(b) = factor x GM / (c^2 b), with factor 0, 2 (half) or 4 (general relativity), or a free
amplitude at the solar limb. The limb value comes from recorded constants (REL-003).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import astropy.units as u

from sdp_relativity.constants import Constants, recorded_constants
from sdp_relativity.geometry import StarGeometry


def limb_deflection(factor: float, constants: Constants | None = None) -> u.Quantity:
    """factor x GM_sun / (c^2 R_sun), in arcsec."""
    k = constants or recorded_constants()
    return (factor * k.gm_over_c2 / k.R_sun * u.rad).to(u.arcsec)


@dataclass(frozen=True)
class DeflectionModel:
    """Deflection at the limb, scaled as 1/b and directed radially away from the Sun's centre."""

    model_id: str
    hypothesis_id: str
    constants: Constants = field(default_factory=recorded_constants)

    @property
    def free(self) -> bool:
        return False

    def limb(self) -> u.Quantity:
        raise NotImplementedError

    def deflection(self, star: StarGeometry) -> u.Quantity:
        return self.limb() * star.limb_ratio

    def displacement(self, star: StarGeometry) -> tuple[u.Quantity, u.Quantity]:
        """Components on the plate axes."""
        total = self.deflection(star)
        ux, uy = star.unit_vector
        return total * ux, total * uy


@dataclass(frozen=True)
class ZeroDeflection(DeflectionModel):
    model_id: str = "deflection.zero"
    hypothesis_id: str = "UC02-H0"

    def limb(self) -> u.Quantity:
        return 0.0 * u.arcsec


@dataclass(frozen=True)
class HalfDeflection(DeflectionModel):
    model_id: str = "deflection.half"
    hypothesis_id: str = "UC02-H1"

    def limb(self) -> u.Quantity:
        return limb_deflection(2, self.constants)


@dataclass(frozen=True)
class GRDeflection(DeflectionModel):
    model_id: str = "deflection.gr"
    hypothesis_id: str = "UC02-H2"

    def limb(self) -> u.Quantity:
        return limb_deflection(4, self.constants)


@dataclass(frozen=True)
class FreeAmplitude(DeflectionModel):
    alpha: u.Quantity = field(default_factory=lambda: 0.0 * u.arcsec)
    model_id: str = "deflection.free"
    hypothesis_id: str = "UC02-H3"

    @property
    def free(self) -> bool:
        return True

    def limb(self) -> u.Quantity:
        return self.alpha.to(u.arcsec)


def hypotheses(constants: Constants | None = None) -> tuple[DeflectionModel, ...]:
    k = constants or recorded_constants()
    return (
        ZeroDeflection(constants=k),
        HalfDeflection(constants=k),
        GRDeflection(constants=k),
        FreeAmplitude(constants=k),
    )
