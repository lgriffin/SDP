"""T5: photutils and tweakwcs behind SourceDetector and WcsRefiner, on a fixture FITS frame.

The frame is synthesised here: Gaussian stars at known sky positions on a noisy background, with a
header WCS deliberately off by about 1.8 arcsec, so detection is checked against the truth and
refinement against the reference catalogue (Investigate §9, "fixture FITS for photutils and tweakwcs").
"""

from __future__ import annotations

import io
import math

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from sdp.adapters.imaging.photutils_detector import PhotutilsDetector
from sdp.adapters.imaging.tweakwcs_refiner import TweakwcsRefiner
from sdp.kernel.domain.errors import DomainError
from sdp.kernel.ports.investigate import ImageFrame
from sdp.testing.contracts.investigate import DetectorCase, RefinerCase, SourceDetectorContract, WcsRefinerContract

pytestmark = [pytest.mark.tier("T5"), pytest.mark.integration]

SIZE, SCALE_DEG, STARS = 256, 1 / 3600, 30
CENTRE = (120.0, -10.0)
HEADER_ERROR_ARCSEC = (1.5, -1.0)


def _wcs(ra: float, dec: float, scale_deg: float = SCALE_DEG) -> WCS:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [SIZE / 2 + 0.5, SIZE / 2 + 0.5]
    w.wcs.cd = np.array([[-scale_deg, 0.0], [0.0, scale_deg]])
    return w


def fixture_frame(seed: int = 3, scale_deg: float = SCALE_DEG) -> tuple[ImageFrame, np.ndarray, np.ndarray]:
    """The FITS bytes and the true positions of the stars in them."""
    rng = np.random.default_rng(seed)
    truth = _wcs(*CENTRE, scale_deg)
    x = rng.uniform(15, SIZE - 15, STARS)
    y = rng.uniform(15, SIZE - 15, STARS)
    flux = rng.uniform(3000, 30000, STARS)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    sigma = 3.0 / 2.3548
    image = np.full((SIZE, SIZE), 100.0)
    for xi, yi, fi in zip(x, y, flux, strict=True):
        image += fi / (2 * math.pi * sigma**2) * np.exp(-((xx - xi) ** 2 + (yy - yi) ** 2) / (2 * sigma**2))
    image += rng.normal(0.0, 5.0, image.shape)
    ra, dec = truth.pixel_to_world_values(x, y)
    off_ra, off_dec = HEADER_ERROR_ARCSEC
    header_wcs = _wcs(
        CENTRE[0] + off_ra / 3600 / math.cos(math.radians(CENTRE[1])), CENTRE[1] + off_dec / 3600, scale_deg
    )
    buffer = io.BytesIO()
    fits.PrimaryHDU(image.astype(np.float32), header=header_wcs.to_header()).writeto(buffer)
    return ImageFrame("sha256:" + "f" * 64, buffer.getvalue(), "application/fits"), np.asarray(ra), np.asarray(dec)


FRAME, TRUE_RA, TRUE_DEC = fixture_frame()


def nearest_arcsec(ra: float, dec: float) -> float:
    cosd = math.cos(math.radians(dec))
    return float(np.min(np.hypot((TRUE_RA - ra) * cosd, TRUE_DEC - dec)) * 3600)


class TestPhotutilsDetector(SourceDetectorContract):
    @pytest.fixture
    def detector_case(self) -> DetectorCase:
        return DetectorCase(PhotutilsDetector(), FRAME, expected=STARS - 2)


class TestTweakwcsRefiner(WcsRefinerContract):
    @pytest.fixture
    def refiner_case(self) -> RefinerCase:
        return RefinerCase(TweakwcsRefiner(), PhotutilsDetector(), FRAME, TRUE_RA, TRUE_DEC, max_rms_arcsec=0.3)


@pytest.mark.req("INV-003")
def test_detection_finds_the_stars_where_the_header_says_and_refinement_moves_them_onto_the_truth() -> None:
    found = PhotutilsDetector().detect(FRAME, {})
    before = [nearest_arcsec(d.ra_deg, d.dec_deg) for d in found.detections]
    assert np.median(before) == pytest.approx(math.hypot(*HEADER_ERROR_ARCSEC), abs=0.3)
    refined = TweakwcsRefiner().refine(found, TRUE_RA, TRUE_DEC, {})
    after = [nearest_arcsec(d.ra_deg, d.dec_deg) for d in refined.detections]
    assert np.median(after) < 0.2 and refined.matches >= STARS - 2
    assert refined.wcs_header != found.wcs_header


def test_the_reported_rms_is_in_arcseconds_whatever_the_pixel_scale() -> None:
    frame, ra, dec = fixture_frame(scale_deg=0.25 / 3600)
    refined = TweakwcsRefiner().refine(PhotutilsDetector().detect(frame, {}), ra, dec, {"searchrad": 10.0})
    cosd = math.cos(math.radians(CENTRE[1]))
    residuals = [float(np.min(np.hypot((ra - d.ra_deg) * cosd, dec - d.dec_deg))) * 3600 for d in refined.detections]
    measured = math.sqrt(float(np.mean(np.square(residuals))))
    assert refined.rms_arcsec == pytest.approx(measured, rel=0.6)


def test_background_and_parameters_are_reported() -> None:
    found = PhotutilsDetector().detect(FRAME, {"nsigma": 10.0})
    assert found.background == pytest.approx(100.0, abs=2.0)
    assert found.background_rms == pytest.approx(5.0, rel=0.2)
    assert found.params["nsigma"] == 10.0
    assert found.detector_id.startswith("photutils-")


def test_a_frame_wcs_overrides_the_header() -> None:
    from sdp.adapters.imaging import header_text

    truth = header_text(_wcs(*CENTRE)).encode()
    found = PhotutilsDetector().detect(ImageFrame(FRAME.artifact_id, FRAME.data, FRAME.media_type, truth), {})
    assert np.median([nearest_arcsec(d.ra_deg, d.dec_deg) for d in found.detections]) < 0.2


def test_too_few_references_leave_the_wcs_unchanged() -> None:
    found = PhotutilsDetector().detect(FRAME, {})
    refined = TweakwcsRefiner().refine(found, TRUE_RA[:2], TRUE_DEC[:2], {})
    assert refined.matches == 0 and refined.wcs_header == found.wcs_header
    assert refined.detections == found.detections


def test_non_fits_frames_are_refused() -> None:
    with pytest.raises(DomainError, match="reads FITS"):
        PhotutilsDetector().detect(ImageFrame(FRAME.artifact_id, b"#ASDF", "application/asdf"), {})
