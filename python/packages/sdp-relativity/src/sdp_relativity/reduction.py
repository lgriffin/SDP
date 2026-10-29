"""ReductionScenario and ErrorModel: the predeclared reduction choices (§9, REL-006).

Each axis is a choice the 1920 authors made one way; the sweep makes each one explicit and asks
whether the conclusion survives the other way.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

#: The predeclared axes and their values, in the order the predeclaration lists them.
AXES: Mapping[str, tuple[Any, ...]] = {
    "error_convention": ("probable_as_standard", "converted"),
    "plate_scale": ("solved", "solved_affine", "fixed_from_check_plates"),
    "astrographic": ("included", "excluded", "downweighted"),
    "error_correlation": ("independent", "per_plate"),
    "likelihood": ("gaussian", "student_t"),
    "prior_width_arcsec": (0.5, 1.0, 2.0),
}

#: Axes that change a single instrument's fit; the others act on pooling or the Bayesian comparison.
FIT_AXES = ("error_convention", "plate_scale", "error_correlation", "likelihood")


@dataclass(frozen=True)
class FitScenario:
    """The choices that determine one instrument's fit."""

    error_convention: str = "converted"
    plate_scale: str = "solved"
    error_correlation: str = "independent"
    likelihood: str = "gaussian"

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if value not in AXES[name]:
                raise ValueError(f"{name} must be one of {AXES[name]}, got {value!r}")

    @property
    def key(self) -> str:
        return "|".join(f"{k}={v}" for k, v in asdict(self).items())

    def to_document(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class Scenario:
    """A full point of the sweep: a fit scenario plus the pooling and prior choices."""

    fit: FitScenario
    astrographic: str
    prior_width_arcsec: float

    @property
    def key(self) -> str:
        return f"{self.fit.key}|astrographic={self.astrographic}|prior_width_arcsec={self.prior_width_arcsec}"

    def to_document(self) -> dict[str, Any]:
        return {
            **self.fit.to_document(),
            "astrographic": self.astrographic,
            "prior_width_arcsec": self.prior_width_arcsec,
        }


#: The 1920 reduction: probable errors used as quoted, six plate constants solved from each plate.
HISTORICAL = FitScenario("probable_as_standard", "solved_affine", "independent", "gaussian")
#: Where the 1920 reduction of an instrument differed: at Principe the scale came from the check
#: plates and the orientation from each plate's x measures (pp. 321-325), so both are fixed.
HISTORICAL_BY_INSTRUMENT: Mapping[str, FitScenario] = {
    "principe-astro": FitScenario("probable_as_standard", "fixed_from_check_plates", "independent", "gaussian"),
}
BASELINE = FitScenario()


def historical(instrument: str) -> FitScenario:
    """The scenario that repeats the 1920 reduction of one instrument (REL-G-04)."""
    return HISTORICAL_BY_INSTRUMENT.get(instrument, HISTORICAL)


def axes_from(parameters: Mapping[str, Any]) -> dict[str, tuple[Any, ...]]:
    """The predeclared axes, checked against the values this module implements."""
    declared = parameters.get("scenario_axes", AXES)
    out: dict[str, tuple[Any, ...]] = {}
    for name, values in AXES.items():
        got = tuple(declared.get(name, ()))
        if set(got) - set(values):
            raise ValueError(f"axis {name}: values {sorted(set(got) - set(values))} are not implemented")
        out[name] = got
    return out


def fit_scenarios(axes: Mapping[str, Sequence[Any]] = AXES) -> list[FitScenario]:
    return [FitScenario(*combo) for combo in itertools.product(*(axes[a] for a in FIT_AXES))]


def scenarios(axes: Mapping[str, Sequence[Any]] = AXES) -> Iterator[Scenario]:
    for fit in fit_scenarios(axes):
        for astro in axes["astrographic"]:
            for width in axes["prior_width_arcsec"]:
                yield Scenario(fit, astro, float(width))
