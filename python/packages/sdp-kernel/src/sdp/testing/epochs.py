"""A synthetic field imaged four times over 27 years by four different cameras, for multi-epoch
change detection (INT-025). Each image has its own pixel scale, PSF width, zero point (the same star
gives a different count on each) and an archival WCS that is off by a fraction of an arcsecond, so the
test must align, match resolution and calibrate before it may compare anything. Planted:

- ``e-brightens`` doubles in flux on the last two images;
- ``e-fades`` is 0.5 mag fainter on the last image;
- ``e-vanished`` is on the first three images and missing from the last;
- ``e-fast`` moves 300 mas/yr (8 arcsec over the baseline) and is steady, so it is only found steady
  when measured where it is on each date;
- ``e-pair-a`` and ``e-pair-b`` are 0.25 arcsec apart and steady: the sharp images split them and the
  coarse one does not, so they must be measured together, as one blend.

Every other star is steady. A fifth image in another band (``F200W``) has nothing to be compared with.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pyarrow as pa
from astropy.time import Time
from astropy.wcs import WCS

from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.investigation import (
    ArchiveRole,
    ArchiveSelection,
    Field,
    IdentifyRoute,
    IdentifySpec,
    NullFieldPlan,
    ObservationRecord,
    ProductRef,
    RankingPolicy,
    TestProfile,
    TestSelection,
)
from sdp.testing.fakes.investigate import FakeObservationArchive, PlacedRecord
from sdp.testing.investigate import CANONICAL_COLUMNS
from sdp.testing.jwst import GAIA, MAST, CuttingArchive, fits_image

CENTRE = (210.4, -12.3)
FIELD_ARCSEC = 24.0
BRIGHTENS, FADES, VANISHED, FAST = "e-brightens", "e-fades", "e-vanished", "e-fast"
PAIR_A, PAIR_B = "e-pair-a", "e-pair-b"
PLANTED_CHANGES = {BRIGHTENS, FADES, VANISHED}
#: Counts a g = 16 star gives on an image of zero point factor 1, in total.
FLUX_G16 = 30000.0


@dataclass(frozen=True)
class Camera:
    product_id: str
    mission: str
    instrument: str
    band: str
    date: str
    pixel_arcsec: float
    fwhm_px: float
    zp_factor: float
    wcs_error_arcsec: tuple[float, float]
    noise: float = 1.0

    @property
    def epoch_mjd(self) -> float:
        return float(Time(self.date, format="isot", scale="utc").mjd)

    @property
    def epoch_jyear(self) -> float:
        return float(Time(self.date, format="isot", scale="utc").jyear)


CAMERAS = (
    Camera(
        "hst-1995-wfpc2-f656n_drz.fits", "HST", "WFPC2", "F656N", "1995-04-01T00:00:00", 0.10, 2.8, 0.25, (0.35, -0.25)
    ),
    Camera("hst-2005-acs-f656n_drc.fits", "HST", "ACS", "F656N", "2005-06-10T00:00:00", 0.05, 2.6, 1.6, (-0.2, 0.3)),
    Camera("hst-2014-wfc3-f656n_drc.fits", "HST", "WFC3", "F656N", "2014-09-02T00:00:00", 0.04, 2.2, 0.9, (0.15, 0.1)),
    Camera(
        "jwst-2022-nircam-f656n_i2d.fits",
        "JWST",
        "NIRCAM",
        "F656N",
        "2022-08-14T00:00:00",
        0.031,
        2.0,
        2.4,
        (-0.3, -0.2),
    ),
)
OTHER_BAND = Camera(
    "jwst-2022-nircam-f200w_i2d.fits", "JWST", "NIRCAM", "F200W", "2022-08-14T00:00:00", 0.031, 2.0, 2.0, (0.0, 0.0)
)


def epoch_field() -> Field:
    return Field(Cone(CENTRE[0], CENTRE[1], 0.5 * FIELD_ARCSEC / 3600), epoch_jyear=2016.0, label="synthetic epochs")


def epoch_catalogue(seed: int = 11, stars: int = 26) -> pa.Table:
    """Gaia rows at epoch 2016: the planted six and steady stars between g 15.5 and 18.5."""
    rng = np.random.default_rng(seed)
    cosd = math.cos(math.radians(CENTRE[1]))
    rows: list[dict[str, object]] = []

    def add(key: str, x: float, y: float, pmra: float, g: float) -> None:
        rows.append(
            {
                "source_key": key,
                "ra_deg": CENTRE[0] + x / 3600 / cosd,
                "dec_deg": CENTRE[1] + y / 3600,
                "epoch_jyear": 2016.0,
                "parallax_mas": 1.0,
                "parallax_mas_err": 0.05,
                "pm_ra_cosdec_masyr": pmra,
                "pm_dec_masyr": 0.0,
                "g_mag": g,
                "bp_rp": 1.0,
            }
        )

    # the fast star runs from x = -9.8 (1995) to x = -1.5 (2022) along y = -8.5; nothing else sits on its track
    placed = [(-6.0, 6.0), (6.0, -6.0), (5.5, 6.5), (-3.5, -8.5), (2.0, 0.0), (2.25, 0.0)]
    add(BRIGHTENS, *placed[0], 0.0, 16.5)
    add(FADES, *placed[1], 0.0, 16.0)
    add(VANISHED, *placed[2], 0.0, 16.5)
    add(FAST, *placed[3], 300.0, 16.0)
    add(PAIR_A, *placed[4], 0.0, 16.5)
    add(PAIR_B, *placed[5], 0.0, 17.0)
    while len(placed) < stars:
        x, y = (float(v) for v in rng.uniform(-9.5, 9.5, 2))
        clear_of_track = not (-10.5 < x < 0.5 and abs(y + 8.5) < 2.0)
        if clear_of_track and all(math.hypot(x - a, y - b) > 2.5 for a, b in placed):
            placed.append((x, y))
            add(f"e-{len(placed):02d}", x, y, float(rng.normal(-3, 4)), float(rng.uniform(15.5, 18.5)))
    return pa.table({k: [r[k] for r in rows] for k in CANONICAL_COLUMNS})


def changed_mag(key: str, camera: Camera) -> float | None:
    """How much fainter (magnitudes) the planted world makes ``key`` on ``camera``'s image; None when
    the star is missing from it."""
    year = int(camera.date[:4])
    if key == BRIGHTENS and year >= 2014:
        return -2.5 * math.log10(2.0)
    if key == FADES and year >= 2022:
        return 0.5
    if key == VANISHED and year >= 2022:
        return None
    return 0.0


def epoch_image(camera: Camera, catalogue: pa.Table | None = None, seed: int = 0) -> bytes:
    """The camera's image of the field: every star at its position on that date, a Gaussian PSF of the
    camera's width holding ``FLUX_G16 * zp_factor * 10^(-0.4 (g - 16))`` counts, on Gaussian noise. The
    header names the filter, instrument and date, and a TAN WCS off by ``wcs_error_arcsec``."""
    rows = (catalogue or epoch_catalogue()).to_pylist()
    size = math.ceil(FIELD_ARCSEC / camera.pixel_arcsec) | 1
    rng = np.random.default_rng(seed + int(camera.date[:4]))
    data = rng.normal(0.0, camera.noise, (size, size))
    header: dict[str, str | float] = {
        "EXTNAME": "SCI",
        "CTYPE1": "RA---TAN",
        "CTYPE2": "DEC--TAN",
        "CRVAL1": CENTRE[0],
        "CRVAL2": CENTRE[1],
        "CRPIX1": (size + 1) / 2,
        "CRPIX2": (size + 1) / 2,
        "CDELT1": -camera.pixel_arcsec / 3600,
        "CDELT2": camera.pixel_arcsec / 3600,
        "CUNIT1": "deg",
        "CUNIT2": "deg",
    }
    wcs = WCS({**header, "NAXIS": 2, "NAXIS1": size, "NAXIS2": size})
    sigma = camera.fwhm_px / (2 * math.sqrt(2 * math.log(2)))
    yy, xx = np.mgrid[0:size, 0:size]
    dt = camera.epoch_jyear - 2016.0
    for r in rows:
        dimmer = changed_mag(str(r["source_key"]), camera)
        if dimmer is None:
            continue
        cosd = math.cos(math.radians(float(r["dec_deg"])))
        ra = float(r["ra_deg"]) + float(r["pm_ra_cosdec_masyr"]) * dt / 3.6e6 / cosd
        dec = float(r["dec_deg"]) + float(r["pm_dec_masyr"]) * dt / 3.6e6
        x, y = wcs.world_to_pixel_values(ra, dec)
        flux = FLUX_G16 * camera.zp_factor * 10 ** (-0.4 * (float(r["g_mag"]) + dimmer - 16.0))
        near = (np.abs(xx - float(x)) < 8 * sigma) & (np.abs(yy - float(y)) < 8 * sigma)
        data[near] += (flux / (2 * math.pi * sigma**2)) * np.exp(
            -((xx[near] - float(x)) ** 2 + (yy[near] - float(y)) ** 2) / (2 * sigma**2)
        )
    east, north = camera.wcs_error_arcsec
    header["CRVAL1"] = CENTRE[0] + east / 3600 / math.cos(math.radians(CENTRE[1]))
    header["CRVAL2"] = CENTRE[1] + north / 3600
    primary: dict[str, str | float] = {
        "TELESCOP": camera.mission,
        "INSTRUME": camera.instrument,
        "FILTER": camera.band,
        "DATE-OBS": camera.date[:10],
        "TIME-OBS": camera.date[11:],
    }
    return fits_image(primary, header, data)


def epoch_archive(
    catalogue: pa.Table | None = None, cameras: tuple[Camera, ...] = (*CAMERAS, OTHER_BAND), seed: int = 0
) -> FakeObservationArchive:
    """MAST-shaped: one level-3 image per camera over the field, under its own product id."""
    images = {c.product_id: epoch_image(c, catalogue, seed) for c in cameras}
    records = tuple(
        PlacedRecord(
            CENTRE[0],
            CENTRE[1],
            ObservationRecord(
                product=ProductRef(MAST, c.product_id, f"https://mast.invalid/{c.product_id}"),
                mission=c.mission,
                instrument=c.instrument,
                product_type="image",
                epoch_mjd=c.epoch_mjd,
                size_bytes=len(images[c.product_id]),
                licence="public",
                citation="synthetic",
            ),
        )
        for c in cameras
    )
    return FakeObservationArchive(MAST, "mast:caom", records=records, products=images)


def epoch_cutting_archive(catalogue: pa.Table | None = None) -> CuttingArchive:
    """The same archive, able to cut (each synthetic image is already smaller than any cutout)."""
    whole = epoch_archive(catalogue)
    return CuttingArchive(whole.archive_id, whole.release_label, records=whole.records, products=whole.products)


def epoch_thresholds(**overrides: object) -> dict[str, object]:
    """The test's settings for this world: align every image to Gaia, aperture one coarsest FWHM."""
    return {
        "source_id": GAIA,
        "fwhm_px": 2.5,
        "nsigma": 5.0,
        "align_wcs": True,
        "align_search_arcsec": 2.0,
        "align_separation_arcsec": 0.5,
        "align_tolerance_arcsec": 0.3,
        "anchor_sigma_arcsec": 0.01,
        "aperture_fwhm": 1.0,
        "calibrator_snr": 20.0,
        "min_calibrators": 5,
        "error_scale": "field",
        "alpha": 0.01,
        **overrides,
    }


def epoch_profile(**overrides: object) -> TestProfile:
    return TestProfile(
        profile_id="epoch-change-synthetic",
        version="1.0.0",
        description="INT-025: four cameras over 27 years, one field",
        identify=IdentifySpec(
            route=IdentifyRoute.CATALOGUE,
            source_id=GAIA,
            release_label="gaia:DR3",
            columns=CANONICAL_COLUMNS,
            match_radius_arcsec=1.0,
        ),
        archives=(
            ArchiveSelection(
                MAST,
                ArchiveRole.OBSERVATIONS,
                "mast:caom",
                match_radius_arcsec=30.0,
                missions=("HST", "JWST"),
                product_types=("image",),
            ),
        ),
        tests=(TestSelection("epoch-change", "1.0.0", epoch_thresholds(**overrides), needs_products=("image",)),),
        ranking=RankingPolicy("significance", "1.0.0", threshold=1.0, alpha=0.01),
        null_fields=NullFieldPlan(trials=5),
    )
