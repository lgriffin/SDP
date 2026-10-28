"""T1: uncertainty conventions (REL-002)."""

from __future__ import annotations

import pytest

from sdp_relativity.measurement import (
    PROBABLE_TO_STANDARD,
    ConventionError,
    ProbableError,
    StandardError,
    probable_as_standard,
    probable_to_standard,
    to_standard,
    uncertainty,
    weight,
)

pytestmark = pytest.mark.tier("T1")


@pytest.mark.req("REL-002")
def test_a_probable_error_cannot_become_a_weight_without_conversion() -> None:
    with pytest.raises(ConventionError, match="probable_to_standard"):
        weight(ProbableError(0.12))
    with pytest.raises(ConventionError):
        weight(0.12)


@pytest.mark.req("REL-002")
def test_the_conversion_is_explicit_and_named() -> None:
    assert pytest.approx(1.4826, abs=5e-5) == PROBABLE_TO_STANDARD
    assert probable_to_standard(ProbableError(0.1)).arcsec == pytest.approx(0.14826, abs=5e-6)
    assert probable_as_standard(ProbableError(0.1)).arcsec == 0.1
    assert weight(probable_to_standard(ProbableError(0.1))) == pytest.approx(1 / 0.14826**2, rel=1e-4)


def test_scenarios_choose_the_conversion() -> None:
    assert to_standard(ProbableError(0.2), "converted").arcsec == pytest.approx(0.2 * PROBABLE_TO_STANDARD)
    assert to_standard(ProbableError(0.2), "probable_as_standard").arcsec == 0.2
    assert to_standard(StandardError(0.2), "converted").arcsec == 0.2
    with pytest.raises(ValueError):
        to_standard(ProbableError(0.2), "guess")


def test_conventions_are_parsed_and_uncertainties_positive() -> None:
    assert isinstance(uncertainty(0.1, "probable_error"), ProbableError)
    assert isinstance(uncertainty(0.1, "standard"), StandardError)
    with pytest.raises(ValueError):
        StandardError(0.0)
