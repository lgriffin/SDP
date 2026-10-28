"""T8: the sensitivity sweep covers the six predeclared axes (REL-006)."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from sdp_relativity.reduction import AXES, BASELINE, HISTORICAL, FitScenario, axes_from, fit_scenarios, scenarios

pytestmark = pytest.mark.tier("T8")

REPO = Path(__file__).resolve().parents[3]


def predeclared() -> Any:
    return json.loads((REPO / "experiments" / "uc02-einstein-1919" / "predeclaration.json").read_text())


@pytest.mark.req("REL-006")
def test_the_axes_are_the_six_predeclared_axes() -> None:
    declared = predeclared()["parameters"]["scenario_axes"]
    assert list(AXES) == list(declared)
    assert axes_from({"scenario_axes": declared}) == {k: tuple(v) for k, v in declared.items()}


@pytest.mark.req("REL-006")
def test_the_sweep_is_the_full_cross_product() -> None:
    every = list(scenarios())
    assert len(every) == 2 * 2 * 3 * 2 * 2 * 3 == 144
    assert len({s.key for s in every}) == 144
    assert len(fit_scenarios()) == 16
    for axis, values in AXES.items():
        assert {s.to_document()[axis] for s in every} == set(values)


@pytest.mark.req("REL-006")
def test_an_axis_value_the_module_does_not_implement_is_refused() -> None:
    with pytest.raises(ValueError, match="not implemented"):
        axes_from({"scenario_axes": {**AXES, "likelihood": ["gaussian", "cauchy"]}})
    with pytest.raises(ValueError, match="likelihood"):
        FitScenario(likelihood="cauchy")


def test_the_1920_reduction_and_the_baseline_are_named_points_of_the_sweep() -> None:
    keys = {s.key for s in fit_scenarios()}
    assert HISTORICAL.key in keys and BASELINE.key in keys
    assert HISTORICAL.error_convention == "probable_as_standard"
    assert BASELINE.error_convention == "converted"


def test_the_packaged_predeclaration_is_the_experiment_file() -> None:
    packaged = json.loads(files("sdp_relativity").joinpath("predeclaration.json").read_text())
    assert packaged == predeclared()
