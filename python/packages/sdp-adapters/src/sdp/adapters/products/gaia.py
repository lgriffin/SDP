"""The Gaia DR3 DataLink product dialect (Integration v0.4.0 §3; INT-022, D44).

Fills the same ObsCore-shaped envelope a JWST FITS product gets, from a DataLink VOTable's columns,
UCDs and time system, and curates epoch photometry into the canonical ``lightcurve`` the variability
test reads. What the gateway fetched and the inventory read come from
``sdp.adapters.archives.gaia_datalink``.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from sdp.adapters.gaia_dr3 import (
    ACKNOWLEDGEMENT,
    CITATION,
    DR3_SPAN_MJD,
    GAIA_TIME_ORIGIN_MJD,
    MAG_PER_RELATIVE_FLUX,
    MEDIA_TYPE,
    MISSION,
    RELEASE,
    parse_product_id,
)
from sdp.adapters.products.votable import column_roles, numbers, parse_table, read_tables
from sdp.kernel.domain.integration import AccessStatus, ProductMetadata, ProductType, Rights
from sdp.kernel.domain.investigation import FetchedProduct
from sdp.kernel.ports.integration import RawProduct
from sdp.kernel.ports.investigate import CuratedProduct
from sdp.kernel.science import products

#: The flags that drop a G-band transit: rejected by the photometric or the variability processing.
REJECT_FLAGS = ("variability_flag_g_reject", "rejected_by_photometry")


@dataclass(frozen=True)
class GaiaDataLinkDialect:
    """Gaia DR3 DataLink products: the ``ProductNormalizer`` and the Investigate ``ProductDialect``.

    Epoch photometry curates to the canonical ``lightcurve``: G-band transits that photometry did not
    reject and that the variability processing did not flag, with MJD from the barycentric TCB transit
    time and the magnitude error from the flux error. BP/RP spectra are read as served. Every row Gaia's
    DataLink lists is one of the source's own products (``#this``), so the envelope names no
    relationship; the rows stay in the inventory record.
    """

    dialect_id: str = "gaia-dr3-datalink"
    dialect_version: str = "1.0.0"

    def curate(self, product_type: str, raw: FetchedProduct) -> CuratedProduct | None:
        if product_type != products.LIGHTCURVE:
            return None
        return CuratedProduct(epoch_lightcurve(raw.data), "text/csv")

    def normalize(self, product: RawProduct) -> ProductMetadata:
        raw, obs = product.fetched, product.obscore_row
        source, gaia = parse_product_id(raw.product.product_id)
        table = read_tables(raw.data)[0]
        header = table.header
        rows = parse_table(raw.data)
        times: tuple[float | None, float | None] = DR3_SPAN_MJD
        band: tuple[float | None, float | None] = (gaia.em_min_m, gaia.em_max_m)
        if gaia.kind is ProductType.TIMESERIES:
            times = _range(rows, "g_transit_time", GAIA_TIME_ORIGIN_MJD)
        else:
            band = _range(rows, "wavelength", 0.0, 1e-9)
        (start, end), (em_min, em_max) = times, band
        solution = header.get("solution_id")
        return ProductMetadata(
            product_id=f"gaia:DR3/product/{source}/{gaia.retrieval_type.lower()}",
            product_type=gaia.kind,
            mission=MISSION,
            instrument=gaia.instrument,
            detector=None,
            filter_or_grating=gaia.bands,
            observation_id=f"{RELEASE} {source}",
            programme_id=None,
            ra_deg=_float(obs.get("s_ra")),
            dec_deg=_float(obs.get("s_dec")),
            frame="ICRS",
            s_region=None,
            time_start_mjd=start,
            time_end_mjd=end,
            time_scale=str(header.get("TIMESYS", "UNKNOWN")).upper(),
            em_min_m=em_min,
            em_max_m=em_max,
            spatial_resolution_arcsec=None,
            field_of_view_deg=None,
            calibration_level=gaia.calibration_level,
            pipeline_version=RELEASE if solution is None else f"{RELEASE} solution {solution}",
            media_type=MEDIA_TYPE,
            dimensions=table.shape,
            units=table.column_units.get(gaia.data_column),
            extensions=column_roles(table),
            has_wcs=False,
            access_status=AccessStatus.PUBLIC,
            exclusive_access_ends=None,
            rights=Rights(raw.licence, CITATION, ACKNOWLEDGEMENT),
            source_id=product.source_id,
            rendered_query=product.rendered_query,
            checksum="sha256:" + hashlib.sha256(raw.data).hexdigest(),
            size_bytes=len(raw.data),
        )


def _float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _range(rows: Any, column: str, offset: float, scale: float = 1.0) -> tuple[float | None, float | None]:
    """The finite extent of a column, shifted and scaled (Gaia days to MJD, nanometres to metres)."""
    values = numbers(rows, column)
    values = values[np.isfinite(values)]
    if not len(values):
        return None, None
    return float(values.min()) * scale + offset, float(values.max()) * scale + offset


def epoch_lightcurve(data: bytes) -> bytes:
    """The canonical light curve of Gaia epoch photometry, in the G band."""
    t = parse_table(data)
    time, flux, err, mag = (
        numbers(t, c) for c in ("g_transit_time", "g_transit_flux", "g_transit_flux_error", "g_transit_mag")
    )
    keep = np.isfinite(time) & np.isfinite(mag) & (flux > 0) & (err > 0)
    for flag in REJECT_FLAGS:
        # a flag column that is absent rejects nothing; a masked flag rejects its transit
        keep &= ~np.ma.filled(t[flag], True) if flag in t.colnames else True
    order = np.argsort(time[keep])
    return products.lightcurve_csv(
        (time[keep] + GAIA_TIME_ORIGIN_MJD)[order],
        mag[keep][order],
        (MAG_PER_RELATIVE_FLUX * err[keep] / flux[keep])[order],
    )
