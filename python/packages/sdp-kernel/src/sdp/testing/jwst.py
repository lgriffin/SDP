"""A synthetic JWST field for the v0.4.0 vertical slice (§7): Gaia rows, a NIRCam-like i2d image, and
the MAST-shaped archive that serves it. The world is planted so the slice exercises every part:

- ``j-vanished`` is in Gaia but not on the image, so the image test flags it (p = 0);
- ``j-pair-a`` and ``j-pair-b`` are 0.12 arcsec apart, closer than the image resolution, so their one
  blended detection keeps two hypotheses with their posteriors (a retained ambiguity);
- ``j-fast`` moves 400 mas/yr in RA and is found only once propagated to the image epoch.
"""

from __future__ import annotations

import math

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
    ResolverRecord,
    TestProfile,
    TestSelection,
)
from sdp.testing.fakes.investigate import FakeObservationArchive, FixtureResolver, PlacedRecord
from sdp.testing.investigate import CANONICAL_COLUMNS

JWST_CENTRE = (150.1, 2.2)
IMAGE_MJD = 60000.0
PIXEL_ARCSEC = 0.063
IMAGE_PIXELS = 320
FWHM_PX = 3.0
MAST = "synthetic-mast"
GAIA = "synthetic-gaia"
PRODUCT_ID = "jw-synth-o001_t001_nircam_clear-f200w/jw-synth-o001_t001_nircam_clear-f200w_i2d.fits"
VANISHED, PAIR_A, PAIR_B, FAST = "j-vanished", "j-pair-a", "j-pair-b", "j-fast"


def jwst_field() -> Field:
    return Field(Cone(JWST_CENTRE[0], JWST_CENTRE[1], 9.5 / 3600), epoch_jyear=2016.0, label="synthetic JWST field")


def jwst_catalogue(seed: int = 3) -> pa.Table:
    """Twelve Gaia stars inside 9 arcsec of the centre at epoch 2016, with the planted three."""
    rng = np.random.default_rng(seed)
    cosd = math.cos(math.radians(JWST_CENTRE[1]))
    rows: list[dict[str, object]] = []

    def add(key: str, x: float, y: float, pmra: float, pmdec: float, g: float) -> None:
        rows.append(
            {
                "source_key": key,
                "ra_deg": JWST_CENTRE[0] + x / 3600 / cosd,
                "dec_deg": JWST_CENTRE[1] + y / 3600,
                "epoch_jyear": 2016.0,
                "parallax_mas": 1.0,
                "parallax_mas_err": 0.05,
                "pm_ra_cosdec_masyr": pmra,
                "pm_dec_masyr": pmdec,
                "g_mag": g,
                "bp_rp": 1.0,
            }
        )

    placed: list[tuple[float, float]] = [(-5.0, 4.0), (3.0, 3.0), (3.12, 3.0), (-4.0, -5.0)]
    while len(placed) < 12:
        x, y = (float(v) for v in rng.uniform(-8.0, 8.0, 2))
        if all(math.hypot(x - a, y - b) > 1.5 for a, b in placed):
            placed.append((x, y))
    add(VANISHED, *placed[0], 0.0, 0.0, 16.0)
    add(PAIR_A, *placed[1], 0.0, 0.0, 17.0)
    add(PAIR_B, *placed[2], 0.0, 0.0, 17.0)
    add(FAST, *placed[3], 400.0, 0.0, 16.5)
    for i, (x, y) in enumerate(placed[4:]):
        add(f"j-{i:02d}", x, y, float(rng.normal(-2, 3)), float(rng.normal(1, 3)), float(rng.uniform(15, 19)))
    return pa.table({k: [r[k] for r in rows] for k in CANONICAL_COLUMNS})


def image_epoch_jyear() -> float:
    return float(Time(IMAGE_MJD, format="mjd").jyear)


def jwst_i2d(catalogue: pa.Table | None = None, seed: int = 5, missing: tuple[str, ...] = (VANISHED,)) -> bytes:
    """A NIRCam-like level-3 image: every Gaia star but those ``missing`` (``j-vanished``), moved to the
    image epoch, as a Gaussian PSF of 3 pixels FWHM on unit noise; the header carries MJD-AVG and a TAN WCS."""
    rows = (catalogue or jwst_catalogue()).to_pylist()
    rng = np.random.default_rng(seed)
    data = rng.normal(0.0, 1.0, (IMAGE_PIXELS, IMAGE_PIXELS))
    header: dict[str, str | float] = {
        "EXTNAME": "SCI",
        "CTYPE1": "RA---TAN",
        "CTYPE2": "DEC--TAN",
        "CRVAL1": JWST_CENTRE[0],
        "CRVAL2": JWST_CENTRE[1],
        "CRPIX1": (IMAGE_PIXELS + 1) / 2,
        "CRPIX2": (IMAGE_PIXELS + 1) / 2,
        "CDELT1": -PIXEL_ARCSEC / 3600,
        "CDELT2": PIXEL_ARCSEC / 3600,
        "CUNIT1": "deg",
        "CUNIT2": "deg",
    }
    wcs = WCS({**header, "NAXIS": 2, "NAXIS1": IMAGE_PIXELS, "NAXIS2": IMAGE_PIXELS})
    sigma = FWHM_PX / (2 * math.sqrt(2 * math.log(2)))
    yy, xx = np.mgrid[0:IMAGE_PIXELS, 0:IMAGE_PIXELS]
    dt = image_epoch_jyear() - 2016.0
    for r in rows:
        if r["source_key"] in missing:
            continue
        cosd = math.cos(math.radians(float(r["dec_deg"])))
        ra = float(r["ra_deg"]) + float(r["pm_ra_cosdec_masyr"]) * dt / 3.6e6 / cosd
        dec = float(r["dec_deg"]) + float(r["pm_dec_masyr"]) * dt / 3.6e6
        x, y = wcs.world_to_pixel_values(ra, dec)
        amplitude = 400.0 * 10 ** (-0.4 * (float(r["g_mag"]) - 16.0))
        data += amplitude * np.exp(-((xx - float(x)) ** 2 + (yy - float(y)) ** 2) / (2 * sigma**2))
    primary: dict[str, str | float] = {"TELESCOP": "JWST", "INSTRUME": "NIRCAM", "MJD-AVG": IMAGE_MJD}
    return fits_image(primary, header, data)


def _card(key: str, value: str | float) -> str:
    if isinstance(value, bool):
        text = ("T" if value else "F").rjust(20)
    elif isinstance(value, str):
        text = f"'{value.ljust(8)}'"
    elif isinstance(value, int):
        text = str(value).rjust(20)
    else:
        text = repr(float(value)).upper().rjust(20)
    return f"{key.ljust(8)}= {text}".ljust(80)[:80]


def _block(cards: list[str]) -> bytes:
    text = "".join([*cards, "END".ljust(80)])
    return text.ljust(-(-len(text) // 2880) * 2880).encode("ascii")


def fits_image(primary: dict[str, str | float], header: dict[str, str | float], data: np.ndarray) -> bytes:
    """A primary HDU and one 32-bit float IMAGE extension, written to the FITS standard by hand: the
    testing package builds images without astropy.io, which only adapters bind (INV-011)."""
    ny, nx = data.shape
    head = [_card("SIMPLE", True), _card("BITPIX", 8), _card("NAXIS", 0), _card("EXTEND", True)]
    ext = [
        _card("XTENSION", "IMAGE"),
        _card("BITPIX", -32),
        _card("NAXIS", 2),
        _card("NAXIS1", nx),
        _card("NAXIS2", ny),
        _card("PCOUNT", 0),
        _card("GCOUNT", 1),
    ]
    body = data.astype(">f4").tobytes()
    body += bytes(-len(body) % 2880)
    return (
        _block(head + [_card(k, v) for k, v in primary.items()])
        + _block(ext + [_card(k, v) for k, v in header.items()])
        + body
    )


def jwst_archive(
    catalogue: pa.Table | None = None, images: tuple[tuple[str, bytes], ...] | None = None
) -> FakeObservationArchive:
    """MAST-shaped: public level-3 NIRCam images over the field (by default the one i2d), each under its
    file-level product id."""
    images = images if images is not None else ((PRODUCT_ID, jwst_i2d(catalogue)),)
    records = tuple(
        PlacedRecord(
            JWST_CENTRE[0],
            JWST_CENTRE[1],
            ObservationRecord(
                product=ProductRef(MAST, product_id, f"https://mast.invalid/{product_id}"),
                mission="JWST",
                instrument="NIRCAM/IMAGE",
                product_type="image",
                epoch_mjd=IMAGE_MJD,
                size_bytes=len(image),
                licence="public after the exclusive-access period",
                citation="synthetic",
            ),
        )
        for product_id, image in images
    )
    return FakeObservationArchive(MAST, "mast:caom", records=records, products=dict(images))


def jwst_resolver(catalogue: pa.Table | None = None) -> FixtureResolver:
    """SIMBAD knows the fast star as a high-proper-motion star, at its 2000 position."""
    fast = next(r for r in (catalogue or jwst_catalogue()).to_pylist() if r["source_key"] == FAST)
    return FixtureResolver(
        (
            ResolverRecord(
                "LP 999-99",
                "HighPM*",
                ("2001A&A...000..000X",),
                float(fast["ra_deg"]),
                float(fast["dec_deg"]),
            ),
        )
    )


def jwst_profile() -> TestProfile:
    """The slice's predeclared profile: Gaia identifies, MAST serves JWST images, and one test reads
    them (v0.4.0 §7)."""
    return TestProfile(
        profile_id="jwst-vertical-slice",
        version="1.0.0",
        description="Integration v0.4.0 §7: one public JWST field, discover to dossier",
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
                match_radius_arcsec=15.0,
                missions=("JWST",),
                product_types=("image",),
            ),
        ),
        tests=(
            TestSelection(
                "image-counterpart",
                "1.0.0",
                {"source_id": GAIA, "fwhm_px": FWHM_PX, "search_radius_arcsec": 1.0},
                needs_products=("image",),
            ),
        ),
        ranking=RankingPolicy("significance", "1.0.0", threshold=1.0, alpha=0.01),
        null_fields=NullFieldPlan(trials=5),
    )
