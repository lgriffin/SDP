"""Epoch propagation shared by both association methods (INT-010): T1 examples, T2 properties,
and agreement with astropy's SkyCoord.apply_space_motion."""

from __future__ import annotations

import math

import astropy.units as u
import pytest
from astropy.coordinates import SkyCoord
from astropy.time import Time
from hypothesis import given, settings
from hypothesis import strategies as st

from sdp.kernel.domain.association import CatalogueSource, Motion, PositionCovariance
from sdp.kernel.science.association import MAX_EPOCH_GAP_WITHOUT_MOTION_YR, offset_arcsec, propagate


def star(ra: float, dec: float, pmra: float, pmdec: float, *, sig_pm: float = 0.1) -> CatalogueSource:
    return CatalogueSource(
        "s",
        "gaia",
        "DR3",
        "1",
        ra,
        dec,
        PositionCovariance(0.02, 0.03, 0.2),
        2016.0,
        Motion(pmra, pmdec, None, sig_pm, sig_pm),
    )


@pytest.mark.tier("T1")
@pytest.mark.req("INT-010")
def test_covariance_grows_with_the_baseline() -> None:
    p = propagate(star(10.0, 20.0, 100.0, -50.0, sig_pm=0.5), 2066.0)
    assert p is not None
    assert p.covariance.sigma_ra_arcsec == pytest.approx(math.hypot(0.02, 0.5 * 50 / 1000))
    assert p.covariance.sigma_dec_arcsec == pytest.approx(math.hypot(0.03, 0.5 * 50 / 1000))
    assert abs(p.covariance.rho) < 0.2


@pytest.mark.tier("T1")
@pytest.mark.req("INT-010")
def test_a_source_without_motion_is_not_compared_across_a_long_baseline() -> None:
    s = CatalogueSource("s", "2mass", "PSC", "1", 10.0, 20.0, PositionCovariance(0.1, 0.1), 2000.0)
    assert propagate(s, 2000.0 + MAX_EPOCH_GAP_WITHOUT_MOTION_YR / 2) is not None
    assert propagate(s, 2023.0) is None


@pytest.mark.tier("T2")
@pytest.mark.req("INT-010")
@settings(max_examples=60, deadline=None)
@given(st.floats(0, 359.9), st.floats(-80, 80), st.floats(-2000, 2000), st.floats(-2000, 2000), st.floats(1900, 2100))
def test_propagation_is_invertible_within_tolerance(
    ra: float, dec: float, pmra: float, pmdec: float, epoch: float
) -> None:
    s = star(ra, dec, pmra, pmdec)
    there = propagate(s, epoch)
    assert there is not None
    back = propagate(
        CatalogueSource("s", "gaia", "DR3", "1", there.ra_deg, there.dec_deg, s.covariance, epoch, s.motion), 2016.0
    )
    assert back is not None
    dx, dy = offset_arcsec(ra, dec, back.ra_deg, back.dec_deg)
    # A proper motion is a tangent vector at its own position; reusing it at the far end leaves a
    # second-order residual of about s^2 tan(dec) / 206265 arcsec for a displacement of s arcsec.
    shift = math.hypot(pmra, pmdec) * abs(epoch - 2016.0) / 1000.0
    tolerance = 1e-6 + 2 * shift * shift * (1 + abs(math.tan(math.radians(dec)))) / 206264.806
    assert math.hypot(dx, dy) <= tolerance


@pytest.mark.tier("T2")
@pytest.mark.req("INT-010")
@settings(max_examples=30, deadline=None)
@given(st.floats(0, 359.9), st.floats(-70, 70), st.floats(-1000, 1000), st.floats(-1000, 1000), st.floats(1950, 2060))
def test_propagation_agrees_with_astropy_apply_space_motion(
    ra: float, dec: float, pmra: float, pmdec: float, epoch: float
) -> None:
    ours = propagate(star(ra, dec, pmra, pmdec), epoch)
    assert ours is not None
    c = SkyCoord(
        ra=ra * u.deg,
        dec=dec * u.deg,
        pm_ra_cosdec=pmra * u.mas / u.yr,
        pm_dec=pmdec * u.mas / u.yr,
        distance=1 * u.kpc,
        radial_velocity=0 * u.km / u.s,
        obstime=Time(2016.0, format="jyear"),
    )
    theirs = c.apply_space_motion(new_obstime=Time(epoch, format="jyear"))
    dx, dy = offset_arcsec(theirs.ra.deg, theirs.dec.deg, ours.ra_deg, ours.dec_deg)
    assert math.hypot(dx, dy) < 1e-4, "more than 0.1 mas from astropy"
