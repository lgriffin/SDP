"""StarGeometry: position relative to the Sun's centre, impact parameter and plate-axis projection.

Plate coordinates are angular offsets from the Sun's centre in arcmin. For a star at angular
separation theta, the impact parameter is b = R_sun x theta / theta_sun, so a 1/b deflection is the
limb deflection times theta_sun / theta.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import astropy.units as u


@dataclass(frozen=True)
class StarGeometry:
    x_arcmin: float
    y_arcmin: float
    solar_radius_arcmin: float

    def __post_init__(self) -> None:
        if self.separation_arcmin <= self.solar_radius_arcmin:
            raise ValueError("a star behind or on the solar disc has no deflection to measure")

    @property
    def separation_arcmin(self) -> float:
        return math.hypot(self.x_arcmin, self.y_arcmin)

    @property
    def limb_ratio(self) -> float:
        """theta_sun / theta, which equals R_sun / b."""
        return self.solar_radius_arcmin / self.separation_arcmin

    @property
    def unit_vector(self) -> tuple[float, float]:
        r = self.separation_arcmin
        return self.x_arcmin / r, self.y_arcmin / r

    def impact_parameter(self, solar_radius: u.Quantity) -> u.Quantity:
        return solar_radius / self.limb_ratio

    @property
    def response(self) -> tuple[float, float]:
        """Displacement per arcsec of limb deflection, on the x and y plate axes."""
        ux, uy = self.unit_vector
        return self.limb_ratio * ux, self.limb_ratio * uy
