from __future__ import annotations

import astropy.units as u
import pytest

from sdp.kernel.domain.errors import InvariantViolation, ProbableErrorNotConverted
from sdp.kernel.domain.measurement import (
    PROBABLE_ERROR_TO_STANDARD,
    Measurement,
    UncertaintyConvention,
    least_squares_weight,
)
from sdp.kernel.domain.sources import SourceKind, SourceRef

pytestmark = pytest.mark.tier("T1")
DOC = SourceRef(
    SourceKind.DOCUMENT, "Dyson et al. 1920", "Public domain", "Phil. Trans. A 220", doi="10.1098/rsta.1920.0009"
)


def pe(value: float = 1.98, err: float = 0.12) -> Measurement:
    return Measurement(value * u.arcsec, err * u.arcsec, UncertaintyConvention.PROBABLE_ERROR, DOC, "p. 332")


@pytest.mark.req("REL-001")
def test_requires_convention_and_locator() -> None:
    with pytest.raises(InvariantViolation):
        Measurement(1 * u.arcsec, 0.1 * u.arcsec, UncertaintyConvention.STANDARD, DOC, "  ")


@pytest.mark.req("REL-002")
def test_probable_error_needs_explicit_conversion() -> None:
    with pytest.raises(ProbableErrorNotConverted):
        least_squares_weight(pe(), u.arcsec)
    converted = pe().to_standard_error()
    assert converted.convention is UncertaintyConvention.STANDARD
    assert converted.uncertainty.to_value(u.arcsec) == pytest.approx(0.12 * 1.482602218505602)
    assert least_squares_weight(converted, u.arcsec) == pytest.approx(1 / (0.12 * PROBABLE_ERROR_TO_STANDARD) ** 2)


def test_probable_error_is_stored_as_given() -> None:
    m = pe()
    assert m.uncertainty.to_value(u.arcsec) == 0.12
    assert m.convention is UncertaintyConvention.PROBABLE_ERROR


def test_treating_a_probable_error_as_standard_is_a_deliberate_choice() -> None:
    m = pe().treat_as_standard_error()
    assert m.uncertainty.to_value(u.arcsec) == 0.12
    assert least_squares_weight(m, u.arcsec) == pytest.approx(1 / 0.12**2)


def test_weight_is_unit_aware() -> None:
    m = Measurement(1.0 * u.arcsec, 100 * u.mas, UncertaintyConvention.STANDARD, DOC, "p. 1")
    assert least_squares_weight(m, u.arcsec) == pytest.approx(100.0)


def test_value_and_uncertainty_share_dimensions() -> None:
    with pytest.raises(InvariantViolation):
        Measurement(1 * u.arcsec, 0.1 * u.m, UncertaintyConvention.STANDARD, DOC, "p. 1")
