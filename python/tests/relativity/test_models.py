"""T1: deflection models, geometry and constants (REL-003, REL-G-03)."""

from __future__ import annotations

import math

import astropy.units as u
import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from sdp_relativity.constants import recorded_constants
from sdp_relativity.geometry import StarGeometry
from sdp_relativity.models import (
    FreeAmplitude,
    GRDeflection,
    HalfDeflection,
    ZeroDeflection,
    hypotheses,
    limb_deflection,
)

pytestmark = pytest.mark.tier("T1")


@pytest.mark.req("REL-003")
def test_limb_values_come_from_recorded_constants() -> None:
    k = recorded_constants()
    assert k.version.startswith("codata") and "iau" in k.version
    gr = GRDeflection().limb()
    assert gr.unit == u.arcsec
    expected = (4 * k.G * k.M_sun / (k.c**2 * k.R_sun)).decompose().value * u.rad
    assert gr.value == pytest.approx(expected.to_value(u.arcsec), rel=1e-12)
    assert 1.74 < gr.value < 1.76  # sanity, not the source of the number
    assert HalfDeflection().limb().value == pytest.approx(gr.value / 2, rel=1e-15)


@pytest.mark.req("REL-003")
def test_changing_the_constants_changes_the_prediction() -> None:
    k = recorded_constants()
    heavier = type(k)(k.G, k.c, 2 * k.M_sun, k.R_sun, "test-heavier-sun")
    assert limb_deflection(4, heavier).value == pytest.approx(2 * limb_deflection(4, k).value, rel=1e-12)


def test_four_hypotheses_in_order() -> None:
    assert [m.hypothesis_id for m in hypotheses()] == ["UC02-H0", "UC02-H1", "UC02-H2", "UC02-H3"]
    assert ZeroDeflection().limb().value == 0.0
    assert FreeAmplitude(alpha=1.2 * u.arcsec).limb() == 1.2 * u.arcsec


@given(st.floats(1.05, 50.0), st.floats(0, 2 * math.pi))
def test_deflection_scales_as_one_over_impact_parameter(rho: float, angle: float) -> None:
    radius = 15.78
    star = StarGeometry(rho * radius * math.cos(angle), rho * radius * math.sin(angle), radius)
    assert GRDeflection().deflection(star).value == pytest.approx(GRDeflection().limb().value / rho, rel=1e-9)
    b = star.impact_parameter(recorded_constants().R_sun)
    assert (b / recorded_constants().R_sun).decompose().value == pytest.approx(rho, rel=1e-9)


@given(st.floats(1.1, 20.0), st.floats(0, 2 * math.pi), st.floats(0, 2 * math.pi))
def test_rotating_the_plate_rotates_the_displacement(rho: float, angle: float, turn: float) -> None:
    """Metamorphic: the model has no preferred plate axis."""
    radius = 15.78
    x, y = rho * radius * math.cos(angle), rho * radius * math.sin(angle)
    c, s = math.cos(turn), math.sin(turn)
    dx, dy = (q.value for q in GRDeflection().displacement(StarGeometry(x, y, radius)))
    rx, ry = (q.value for q in GRDeflection().displacement(StarGeometry(c * x - s * y, s * x + c * y, radius)))
    assert np.allclose([rx, ry], [c * dx - s * dy, s * dx + c * dy], rtol=1e-9, atol=1e-12)


def test_the_displacement_points_away_from_the_sun() -> None:
    dx, dy = GRDeflection().displacement(StarGeometry(30.0, 40.0, 15.0))
    assert dx.value > 0 and dy.value > 0 and dy.value / dx.value == pytest.approx(40 / 30)


def test_a_star_on_the_disc_is_refused() -> None:
    with pytest.raises(ValueError):
        StarGeometry(3.0, 4.0, 15.0)
