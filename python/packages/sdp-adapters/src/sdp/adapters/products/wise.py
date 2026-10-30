"""The WISE and NEOWISE light-curve dialect (Integration v0.4.0 §3, §4; INT-023).

Fills the envelope for a per-exposure light curve IRSA served as a VOTable, from its columns, UCDs
and the WISE Explanatory Supplements, and curates it into the canonical ``lightcurve`` the
variability test reads: W1 (3.4 um) magnitudes of the clean exposures, each with where WISE saw the
source and its 6.1-arcsec beam, which the exposure-counterpart test reconciles with Gaia (INT-024).
``w1_detections`` gives the same exposures as association records, each at its own epoch.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from astropy.table import Table

from sdp.adapters.products.votable import column_roles, numbers, parse_table, read_tables
from sdp.adapters.wise import (
    ACKNOWLEDGEMENT,
    CITATION,
    MEDIA_TYPE,
    W1_RESOLUTION_ARCSEC,
    WISE_BAND_M,
    WiseTable,
    parse_product_id,
)
from sdp.kernel.domain.association import Detection, Photometry, PositionCovariance
from sdp.kernel.domain.integration import AccessStatus, ProductMetadata, ProductType, Rights
from sdp.kernel.domain.investigation import FetchedProduct
from sdp.kernel.ports.integration import RawProduct
from sdp.kernel.ports.investigate import CuratedProduct
from sdp.kernel.science import products

#: The cuts usually applied, after the Supplements, for clean single-exposure photometry: a good
#: frame (``qual_frame`` > 0) of full image quality (``qi_fact`` = 1), well clear of the South
#: Atlantic Anomaly (``saa_sep`` > 5 deg) and of the Moon (W1 digit of ``moon_masked`` 0), unblended
#: and not actively deblended (``nb`` = 1, ``na`` = 0), and not flagged in W1 as a likely artefact
#: (an upper-case ``cc_flags`` character). A lower-case flag marks a real source a neighbour's halo or
#: ghost contaminates; AllWISE repeats the source's flag on every exposure, so its bias is the same at
#: every epoch and cannot fake variability, and dropping it would drop every star near a bright one.
MIN_SAA_SEP_DEG = 5.0
SPURIOUS = frozenset("DPHO")
#: A magnitude error is this times the flux's relative error, its inverse signal-to-noise.
MAG_PER_RELATIVE_FLUX = 2.5 / math.log(10.0)
#: Gaussian FWHM per sigma: a centroid's sigma is the beam's FWHM over this and the signal-to-noise.
FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
J2000_MJD = 51544.5


def _float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _text(table: Table, name: str) -> list[str]:
    return ["" if np.ma.is_masked(v) else (v.decode() if isinstance(v, bytes) else str(v)) for v in table[name]]


def clean(table: Table, wise: WiseTable) -> np.ndarray:
    """The exposures the cuts keep, as a mask."""
    keep = np.ones(len(table), dtype=bool)
    if "qual_frame" in wise.quality:
        keep &= numbers(table, "qual_frame") > 0
    keep &= numbers(table, "qi_fact") == 1.0
    keep &= numbers(table, "saa_sep") > MIN_SAA_SEP_DEG
    keep &= numbers(table, "nb") == 1
    keep &= numbers(table, "na") == 0
    keep &= np.array([m[:1] == "0" for m in _text(table, "moon_masked")], dtype=bool)
    keep &= np.array([c[:1] not in SPURIOUS for c in _text(table, "cc_flags")], dtype=bool)
    return keep


def _w1(table: Table, wise: WiseTable) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The clean exposures with a usable W1 measurement, in time order: indices, MJD, mag, error."""
    mjd, mag, err = (numbers(table, c) for c in ("mjd", wise.w1, wise.w1_error))
    good = clean(table, wise) & np.isfinite(mjd) & np.isfinite(mag) & np.isfinite(err) & (err > 0)
    rows = np.flatnonzero(good)
    rows = rows[np.argsort(mjd[rows], kind="stable")]
    return rows, mjd[rows], mag[rows], err[rows]


def _positions(
    table: Table, wise: WiseTable, rows: np.ndarray, err: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Where each exposure saw the source and its sigmas in arcsec: the table's own (NEOWISE's
    ``sigra``, ``sigdec``), or for an exposure without them the centroid precision of a Gaussian beam,
    FWHM / (2.355 x signal-to-noise)."""
    ra, dec = numbers(table, "ra")[rows], numbers(table, "dec")[rows]
    beam = W1_RESOLUTION_ARCSEC / FWHM_PER_SIGMA * err / MAG_PER_RELATIVE_FLUX
    sig_ra, sig_dec = (numbers(table, c)[rows] for c in wise.position_errors)
    own = (sig_ra > 0) & (sig_dec > 0)
    return ra, dec, np.where(own, sig_ra, beam), np.where(own, sig_dec, beam)


def w1_lightcurve(data: bytes, wise: WiseTable) -> bytes:
    """The canonical W1 light curve of the clean exposures, with where each one saw the source when
    the table measures that (NEOWISE; AllWISE's repeats the source's coadded position)."""
    table = parse_table(data)
    rows, mjd, mag, err = _w1(table, wise)
    if not wise.position_errors:
        return products.lightcurve_csv(mjd, mag, err)
    ra, dec, sig_ra, sig_dec = _positions(table, wise, rows, err)
    return products.lightcurve_csv(mjd, mag, err, (ra, dec, sig_ra, sig_dec, np.full(len(rows), W1_RESOLUTION_ARCSEC)))


def w1_detections(data: bytes, product_id: str) -> tuple[Detection, ...]:
    """Each clean W1 exposure as an association record at its own epoch, with WISE's beam as its
    resolution and the sigmas ``_positions`` gives."""
    _, _, _, wise = parse_product_id(product_id)
    if not wise.position_errors:
        raise ValueError(f"{wise.table} repeats the source's coadded position; its exposures have none of their own")
    table = parse_table(data)
    rows, mjd, mag, err = _w1(table, wise)
    ra, dec, sig_ra, sig_dec = _positions(table, wise, rows, err)
    return tuple(
        Detection(
            f"{product_id}#{row}",
            product_id,
            "TABLE0",
            float(ra[i]),
            float(dec[i]),
            PositionCovariance(float(sig_ra[i]), float(sig_dec[i])),
            2000.0 + (float(mjd[i]) - J2000_MJD) / 365.25,
            W1_RESOLUTION_ARCSEC,
            "WISE",
            (Photometry("W1", float(mag[i]), float(err[i])),),
        )
        for i, row in enumerate(rows)
    )


@dataclass(frozen=True)
class WiseLightCurveDialect:
    """IRSA's WISE and NEOWISE per-exposure tables: the ``ProductNormalizer`` and the Investigate
    ``ProductDialect``. Times are MJD in UTC, as the Supplements define ``mjd``; the VOTable states
    no time system of its own."""

    dialect_id: str = "irsa-wise-lightcurve"
    dialect_version: str = "1.1.0"

    def curate(self, product_type: str, raw: FetchedProduct) -> CuratedProduct | None:
        if product_type != products.LIGHTCURVE:
            return None
        _, _, _, wise = parse_product_id(raw.product.product_id)
        return CuratedProduct(w1_lightcurve(raw.data, wise), "text/csv")

    def normalize(self, product: RawProduct) -> ProductMetadata:
        raw, obs = product.fetched, product.obscore_row
        designation, _, _, wise = parse_product_id(raw.product.product_id)
        info = read_tables(raw.data)[0]
        mjd = numbers(parse_table(raw.data), "mjd")
        mjd = mjd[np.isfinite(mjd)]
        extensions = column_roles(info, quality=tuple(c.upper() for c in wise.quality))
        return ProductMetadata(
            product_id=f"irsa:WISE/product/{designation}/{wise.key.lower()}",
            product_type=ProductType.TIMESERIES,
            mission=wise.mission,
            instrument="WISE",
            detector=None,
            filter_or_grating="W1, W2",
            observation_id=f"WISE {designation}",
            programme_id=None,
            ra_deg=_float(obs.get("s_ra")),
            dec_deg=_float(obs.get("s_dec")),
            frame="ICRS",
            s_region=None,
            time_start_mjd=float(mjd.min()) if len(mjd) else None,
            time_end_mjd=float(mjd.max()) if len(mjd) else None,
            time_scale="UTC",
            em_min_m=WISE_BAND_M[0],
            em_max_m=WISE_BAND_M[1],
            spatial_resolution_arcsec=W1_RESOLUTION_ARCSEC,
            field_of_view_deg=None,
            calibration_level=2,
            pipeline_version=wise.table,
            media_type=MEDIA_TYPE,
            dimensions=info.shape,
            units=info.column_units.get(wise.w1.upper()),
            extensions=extensions,
            has_wcs=False,
            access_status=AccessStatus.PUBLIC,
            exclusive_access_ends=None,
            rights=Rights(raw.licence, CITATION, ACKNOWLEDGEMENT),
            source_id=product.source_id,
            rendered_query=product.rendered_query,
            checksum="sha256:" + hashlib.sha256(raw.data).hexdigest(),
            size_bytes=len(raw.data),
        )
