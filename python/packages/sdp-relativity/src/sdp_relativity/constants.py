"""Physical constants with their recorded version (REL-003). Nothing here hard-codes 1.75 arcsec."""

from __future__ import annotations

from dataclasses import dataclass

import astropy.constants as const
import astropy.units as u
from astropy import astronomical_constants, physical_constants


@dataclass(frozen=True)
class Constants:
    G: u.Quantity
    c: u.Quantity
    M_sun: u.Quantity
    R_sun: u.Quantity
    version: str

    @property
    def gm_over_c2(self) -> u.Quantity:
        return (self.G * self.M_sun / self.c**2).to(u.m)


def recorded_constants() -> Constants:
    """The constants in force, named by their CODATA and IAU versions for the manifest."""
    return Constants(
        G=const.G,
        c=const.c,
        M_sun=const.M_sun,
        R_sun=const.R_sun,
        version=f"{physical_constants.get()}+{astronomical_constants.get()}",
    )
