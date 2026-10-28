"""Uncertainty conventions as types, so a probable error can never become a weight by accident (REL-002).

The 1920 report quotes probable errors. For a Gaussian, the probable error is 0.6745 standard
deviations, so a standard error is 1.4826 probable errors. The conversion is one named function,
applied once and visibly; treating a probable error as if it were a standard error is a second named
function, used only by the scenario that reproduces that historical choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from scipy.stats import norm

#: Standard errors per probable error: 1 / Phi^-1(0.75).
PROBABLE_TO_STANDARD = float(1.0 / norm.ppf(0.75))


class Convention(StrEnum):
    STANDARD = "standard"
    PROBABLE_ERROR = "probable_error"


class ConventionError(TypeError):
    """An uncertainty reached a least-squares weight without an explicit conversion."""


@dataclass(frozen=True)
class StandardError:
    arcsec: float

    def __post_init__(self) -> None:
        if not self.arcsec > 0:
            raise ValueError(f"a standard error must be positive, got {self.arcsec}")


@dataclass(frozen=True)
class ProbableError:
    arcsec: float

    def __post_init__(self) -> None:
        if not self.arcsec > 0:
            raise ValueError(f"a probable error must be positive, got {self.arcsec}")


Uncertainty = StandardError | ProbableError


def uncertainty(value: float, convention: Convention | str) -> Uncertainty:
    return StandardError(value) if Convention(convention) is Convention.STANDARD else ProbableError(value)


def probable_to_standard(error: ProbableError) -> StandardError:
    """The conversion: sigma = 1.4826 x probable error."""
    return StandardError(error.arcsec * PROBABLE_TO_STANDARD)


def probable_as_standard(error: ProbableError) -> StandardError:
    """The historical shortcut, kept as a scenario: the probable error used as if it were sigma."""
    return StandardError(error.arcsec)


def weight(error: object) -> float:
    """Inverse variance. Only a StandardError has one; anything else raises (REL-002)."""
    if not isinstance(error, StandardError):
        raise ConventionError(
            f"{type(error).__name__} cannot weight a fit; convert it with probable_to_standard() first"
        )
    return 1.0 / error.arcsec**2


def to_standard(error: Uncertainty, convention_scenario: str) -> StandardError:
    """Apply the scenario's convention choice to one uncertainty."""
    if isinstance(error, StandardError):
        return error
    if convention_scenario == "converted":
        return probable_to_standard(error)
    if convention_scenario == "probable_as_standard":
        return probable_as_standard(error)
    raise ValueError(f"unknown error convention scenario {convention_scenario!r}")
