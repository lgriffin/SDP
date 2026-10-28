"""Physical values. astropy.units.Quantity is the only way a physical value crosses a port (ADR-010)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import astropy.units as u

from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.policy import NumericalPolicy


def require_quantity(value: Any, name: str = "value") -> u.Quantity:
    if not isinstance(value, u.Quantity):
        raise InvariantViolation(f"{name} must be an astropy Quantity, got {type(value).__name__}")
    return value


@dataclass(frozen=True)
class PhysicalQuantity:
    """The spec's ``Quantity`` value object: an astropy Quantity with a dtype and a NumericalPolicy.

    Dimensionless only when declared so: a bare number cannot sneak through as a physical value.
    """

    value: u.Quantity
    policy: NumericalPolicy = field(default_factory=NumericalPolicy)
    dimensionless: bool = False

    def __post_init__(self) -> None:
        require_quantity(self.value)
        is_dimensionless = self.value.unit.physical_type == "dimensionless"
        if is_dimensionless and not self.dimensionless:
            raise InvariantViolation("a dimensionless quantity must be declared dimensionless=True")
        if self.dimensionless and not is_dimensionless:
            raise InvariantViolation(f"declared dimensionless but carries unit {self.value.unit}")

    @property
    def dtype(self) -> str:
        return self.policy.dtype

    def to(self, unit: u.UnitBase | str) -> u.Quantity:
        return self.value.to(unit)

    def to_document(self) -> dict[str, Any]:
        return {"value": float(self.value.value), "unit": self.value.unit.to_string()}


def quantity_document(value: u.Quantity) -> dict[str, Any]:
    require_quantity(value)
    return {"value": float(value.value), "unit": value.unit.to_string()}


def quantity_from_document(doc: dict[str, Any]) -> u.Quantity:
    return u.Quantity(doc["value"], u.Unit(doc["unit"]))
