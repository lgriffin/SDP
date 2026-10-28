"""Measurements with an explicit uncertainty convention (§5, §9, REL-001, REL-002)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

import astropy.units as u

from sdp.kernel.domain.errors import InvariantViolation, ProbableErrorNotConverted
from sdp.kernel.domain.quantity import require_quantity
from sdp.kernel.domain.sources import SourceRef

#: A Gaussian probable error is 0.6744897501960817 standard deviations, so one standard
#: error is about 1.4826 probable errors' worth of spread: sigma = PE / 0.67449.
PROBABLE_ERROR_TO_STANDARD = 1.0 / 0.6744897501960817


class UncertaintyConvention(StrEnum):
    STANDARD = "standard"
    PROBABLE_ERROR = "probable_error"


@dataclass(frozen=True)
class Measurement:
    """A value, its uncertainty and the convention the source used, with a locator into the source.

    A probable error is stored exactly as given; only ``to_standard_error()`` converts it.
    """

    value: u.Quantity
    uncertainty: u.Quantity
    convention: UncertaintyConvention
    source: SourceRef
    locator: str

    def __post_init__(self) -> None:
        require_quantity(self.value, "value")
        require_quantity(self.uncertainty, "uncertainty")
        if not self.value.unit.is_equivalent(self.uncertainty.unit):
            raise InvariantViolation("value and uncertainty must share a physical dimension")
        if float(self.uncertainty.value) < 0:
            raise InvariantViolation("uncertainty must be non-negative")
        if not self.locator.strip():
            raise InvariantViolation("every measurement needs a page or table locator (REL-001)")

    def to_standard_error(self) -> Measurement:
        """The one named, visible conversion from a probable error to a standard error."""
        if self.convention is UncertaintyConvention.STANDARD:
            return self
        return replace(
            self,
            uncertainty=self.uncertainty * PROBABLE_ERROR_TO_STANDARD,
            convention=UncertaintyConvention.STANDARD,
        )

    def treat_as_standard_error(self) -> Measurement:
        """Deliberately take a probable error at face value: a predeclared sensitivity scenario."""
        return replace(self, convention=UncertaintyConvention.STANDARD)


def least_squares_weight(measurement: Measurement, unit: u.UnitBase | str) -> float:
    """Inverse-variance weight in ``unit**-2``; refuses an unconverted probable error (REL-002)."""
    if measurement.convention is not UncertaintyConvention.STANDARD:
        raise ProbableErrorNotConverted(
            f"measurement at {measurement.locator!r} carries a probable error; call to_standard_error()"
        )
    sigma = float(measurement.uncertainty.to(unit).value)
    if sigma <= 0:
        raise InvariantViolation(f"zero uncertainty at {measurement.locator!r} cannot weight a fit")
    return 1.0 / sigma**2
