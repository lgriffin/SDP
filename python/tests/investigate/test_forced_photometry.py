"""T4: photutils behind ForcedPhotometer, on a synthetic FITS frame with stars of known flux (INT-025)."""

from __future__ import annotations

import io
import math

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from sdp.adapters.imaging.photutils_photometry import PhotutilsPhotometer, header_band, saturation_level
from sdp.kernel.ports.investigate import MASKED, MEASURED, OFF_IMAGE, SATURATED, ImageFrame, SkyPoint
from sdp.testing.contracts.investigate import ForcedPhotometerContract, PhotometerCase

pytestmark = pytest.mark.tier("T4")

SIZE, SCALE, FWHM_PX = 201, 0.05, 3.0
CENTRE = (80.0, 20.0)
FLUX = 20000.0


def wcs() -> WCS:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = list(CENTRE)
    w.wcs.crpix = [(SIZE + 1) / 2, (SIZE + 1) / 2]
    w.wcs.cdelt = [-SCALE / 3600, SCALE / 3600]
    return w


def sky(x: float, y: float) -> tuple[float, float]:
    ra, dec = wcs().pixel_to_world_values(x, y)
    return float(ra), float(dec)


STARS = {"bright": (100.0, 100.0, FLUX), "faint": (40.0, 150.0, 2000.0), "hot": (150.0, 40.0, 60000.0)}


def frame(
    noise: float = 1.0, nan_at: tuple[int, int] | None = None, header: dict[str, object] | None = None
) -> ImageFrame:
    rng = np.random.default_rng(4)
    data = rng.normal(10.0, noise, (SIZE, SIZE))
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    sigma = FWHM_PX / (2 * math.sqrt(2 * math.log(2)))
    for x, y, f in STARS.values():
        data += f / (2 * math.pi * sigma**2) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))
    if nan_at is not None:
        data[nan_at[1], nan_at[0]] = np.nan
    head = wcs().to_header()
    for k, v in {"FILTER": "F657N", "INSTRUME": "WFC3", "DATE-OBS": "2014-09-02", **(header or {})}.items():
        head[k] = v
    buffer = io.BytesIO()
    fits.PrimaryHDU(data.astype(np.float32), header=head).writeto(buffer)
    return ImageFrame("sha256:" + "a" * 64, buffer.getvalue(), "application/fits")


def point(key: str) -> SkyPoint:
    x, y, _ = STARS[key]
    return SkyPoint(key, *sky(x, y))


class TestPhotutilsPhotometer(ForcedPhotometerContract):
    @pytest.fixture
    def photometer_case(self) -> PhotometerCase:
        return PhotometerCase(
            PhotutilsPhotometer(),
            frame(),
            point("bright"),
            FLUX,
            FWHM_PX * SCALE,
            "F657N",
            SkyPoint("off", *sky(-50.0, 100.0)),
            rtol=0.01,
        )


FWHM = FWHM_PX * SCALE


@pytest.mark.req("INT-025")
def test_the_error_is_what_the_background_scatter_gives_and_matches_the_measured_scatter() -> None:
    radius = 1.5 * FWHM
    measured = [
        PhotutilsPhotometer()
        .measure(frame(noise=n), [point("faint")], radius, (3 * FWHM, 5 * FWHM), {})
        .measurements[0]
        for n in (1.0, 2.0)
    ]
    area = math.pi * (radius / SCALE) ** 2
    assert measured[0].flux_err == pytest.approx(math.sqrt(area), rel=0.15)
    assert measured[1].flux_err == pytest.approx(2 * measured[0].flux_err, rel=0.15)
    # photon noise is added when the profile gives the gain
    with_gain = PhotutilsPhotometer().measure(frame(), [point("bright")], radius, (3 * FWHM, 5 * FWHM), {"gain": 1.0})
    without = PhotutilsPhotometer().measure(frame(), [point("bright")], radius, (3 * FWHM, 5 * FWHM), {})
    assert with_gain.measurements[0].flux_err > 5 * without.measurements[0].flux_err


@pytest.mark.req("INT-025")
def test_masked_and_saturated_pixels_leave_a_position_unmeasured() -> None:
    p = PhotutilsPhotometer()
    x, y, _ = STARS["faint"]
    got = p.measure(frame(nan_at=(int(x), int(y))), [point("faint")], FWHM, (2 * FWHM, 3 * FWHM), {}).measurements
    assert got[0].status == MASKED
    hot = p.measure(frame(header={"SATURATE": 500.0}), [point("hot"), point("faint")], FWHM, (2 * FWHM, 3 * FWHM), {})
    assert [m.status for m in hot.measurements] == [SATURATED, MEASURED]
    # the profile's level wins over the header's
    assert saturation_level({"saturation": 1e9}, fits.Header({"SATURATE": 500.0})) == 1e9
    assert saturation_level({}, fits.Header()) is None


@pytest.mark.req("INT-025")
def test_an_aperture_reaching_past_the_edge_is_off_the_image() -> None:
    edge = SkyPoint("edge", *sky(2.0, 100.0))
    [m] = PhotutilsPhotometer().measure(frame(), [edge], 5 * FWHM, (6 * FWHM, 8 * FWHM), {}).measurements
    assert m.status == OFF_IMAGE


def test_the_band_is_the_filter_in_the_beam() -> None:
    assert header_band(fits.Header({"FILTER": "f657n"})) == "F657N"
    acs = fits.Header({"FILTER1": "CLEAR1L", "FILTER2": "F658N"})
    assert header_band(acs) == "F658N"
    assert header_band(fits.Header({"FILTNAM1": "F656N", "FILTNAM2": ""})) == "F656N"
    assert header_band(fits.Header({"FILTER1": "F550M", "FILTER2": "POL0V"})) == "F550M+POL0V"
    assert header_band(fits.Header(), fits.Header({"FILTER": "N/A"})) == ""


def test_the_description_reads_the_epoch_and_the_instrument() -> None:
    d = PhotutilsPhotometer().describe(frame(), [point("bright"), point("faint")], {"fwhm_px": 3.0})
    assert d.instrument == "WFC3" and d.epoch_mjd == pytest.approx(56902.0)
    assert d.pixel_arcsec == pytest.approx(SCALE)
    nothing = PhotutilsPhotometer().describe(frame(), [], {})
    assert nothing.fwhm_arcsec is None and nothing.band == "F657N"
