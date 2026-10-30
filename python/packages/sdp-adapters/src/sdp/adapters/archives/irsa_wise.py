"""WISE and NEOWISE light curves from IRSA's TAP service (Integration v0.4.0 §3, §7).

- ``IrsaWiseGateway`` finds the AllWISE sources in a cone and offers, for each, its NEOWISE and AllWISE
  per-exposure light curves; a light curve is fetched as the TAP query its product URI names;
- ``IrsaWiseInventory`` reads the AllWISE source the light curve was cut around, the archive's own
  description of the product beside its bytes.

``sdp.adapters.products.wise`` envelopes and curates what they fetch. Every request goes through the
archive ``Transport`` (ADR-015), so CI replays recordings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import astropy.units as u

from sdp.adapters.archives import votable
from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.adapters.archives.untrusted import validate_votable
from sdp.adapters.archives.vo import literal, run_sync, sync_request
from sdp.adapters.wise import (
    CITATION,
    IRSA_TAP,
    LICENCE,
    LIGHTCURVE_RADIUS_ARCSEC,
    MAX_PRODUCT_BYTES,
    MEDIA_TYPE,
    MISSION,
    SOURCE_TABLE,
    WISE_TABLES,
    WiseTable,
    lightcurve_url,
    parse_product_id,
    product_id,
)
from sdp.kernel.domain.investigation import (
    FetchedProduct,
    Field,
    ObservationCriteria,
    ObservationIndex,
    ObservationRecord,
    ProductRef,
)
from sdp.kernel.ports.integration import ProductRows
from sdp.kernel.ports.investigate import ObservationTarget
from sdp.kernel.science import products

#: AllWISE positions are at about 2010.5; a moving star is looked for where its motion put it then.
ALLWISE_EPOCH_JYEAR = 2010.5589
#: A star that moves less than this over a table's span is cut out at its AllWISE position.
MOTION_TOLERANCE_ARCSEC = 1.0
J2000_MJD = 51544.5


def _wanted(criteria: ObservationCriteria) -> list[WiseTable]:
    if criteria.product_types and products.LIGHTCURVE not in criteria.product_types:
        return []
    if criteria.min_calibration_level is not None and criteria.min_calibration_level > 2:
        return []  # per-exposure photometry is ObsCore level 2
    out = []
    for table in WISE_TABLES.values():
        if criteria.missions and not {table.mission, MISSION} & {m.upper() for m in criteria.missions}:
            continue
        if criteria.epoch_min_mjd is not None and table.span_mjd[1] < criteria.epoch_min_mjd:
            continue
        if criteria.epoch_max_mjd is not None and table.span_mjd[0] > criteria.epoch_max_mjd:
            continue
        out.append(table)
    return out


def at_allwise_epoch(target: ObservationTarget) -> tuple[float, float]:
    """The target's position at AllWISE's epoch, along its proper motion when it has one."""
    if not target.moving:
        return target.ra_deg, target.dec_deg
    assert target.pm_ra_cosdec_masyr is not None and target.pm_dec_masyr is not None
    assert target.epoch_jyear is not None
    years = ALLWISE_EPOCH_JYEAR - target.epoch_jyear
    dec = target.dec_deg + target.pm_dec_masyr * years / 3.6e6
    ra = target.ra_deg + target.pm_ra_cosdec_masyr * years / 3.6e6 / math.cos(math.radians(target.dec_deg))
    return ra % 360.0, dec


def lightcurve_cone(
    target: ObservationTarget, wise: WiseTable, ra_deg: float, dec_deg: float
) -> tuple[float, float, float]:
    """Where a source's light curve is cut out of a table: at its AllWISE position, or, for a star that
    moves more than ``MOTION_TOLERANCE_ARCSEC`` across the table's span, at the middle of its path with
    the radius grown by half the path, so its exposures late in NEOWISE are kept."""
    if not target.moving:
        return ra_deg, dec_deg, LIGHTCURVE_RADIUS_ARCSEC
    assert target.pm_ra_cosdec_masyr is not None and target.pm_dec_masyr is not None
    start, end = (2000.0 + (mjd - J2000_MJD) / 365.25 for mjd in wise.span_mjd)
    path = math.hypot(target.pm_ra_cosdec_masyr, target.pm_dec_masyr) / 1000.0 * (end - start)
    if path <= MOTION_TOLERANCE_ARCSEC:
        return ra_deg, dec_deg, LIGHTCURVE_RADIUS_ARCSEC
    years = (start + end) / 2.0 - ALLWISE_EPOCH_JYEAR
    dec = dec_deg + target.pm_dec_masyr * years / 3.6e6
    ra = ra_deg + target.pm_ra_cosdec_masyr * years / 3.6e6 / math.cos(math.radians(dec_deg))
    return ra % 360.0, dec, LIGHTCURVE_RADIUS_ARCSEC + path / 2.0


@dataclass
class IrsaWiseGateway:
    """WISE and NEOWISE per-exposure light curves behind ObservationArchiveGateway."""

    transport: Transport
    service_url: str = IRSA_TAP
    max_records: int = 2000
    archive_id: str = "irsa-wise"
    release_label: str = "irsa:allwise-neowise"
    licence: str = LICENCE
    citation: str = CITATION

    def search_query(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> str:
        return (
            f"SELECT TOP {self.max_records} designation, ra, dec FROM {SOURCE_TABLE} "
            "WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), "
            f"CIRCLE('ICRS', {ra_deg!r}, {dec_deg!r}, {radius_arcsec / 3600.0!r})) ORDER BY designation"
        )

    def search(self, target: ObservationTarget, criteria: ObservationCriteria) -> ObservationIndex:
        """The AllWISE sources in the cone (at AllWISE's epoch for a moving target), each offered as a
        NEOWISE and an AllWISE light curve."""
        wanted = _wanted(criteria)
        records: list[ObservationRecord] = []
        query = ""
        if wanted:
            ra, dec = at_allwise_epoch(target)
            query = self.search_query(ra, dec, criteria.radius_arcsec)
            table = run_sync(self.transport, sync_request(self.service_url, query, self.max_records), "IRSA TAP")
            for row in table.to_pylist():
                designation = str(row["designation"]).strip()
                for wise in wanted:
                    src_ra, src_dec = float(row["ra"]), float(row["dec"])
                    records.append(
                        ObservationRecord(
                            product=ProductRef(
                                self.archive_id,
                                product_id(designation, src_ra, src_dec, wise.key),
                                lightcurve_url(wise, *lightcurve_cone(target, wise, src_ra, src_dec)),
                            ),
                            mission=wise.mission,
                            instrument="WISE",
                            product_type=products.LIGHTCURVE,
                            epoch_mjd=wise.span_mjd[0],
                            epoch_end_mjd=wise.span_mjd[1],
                            size_bytes=None,
                            licence=self.licence,
                            citation=self.citation,
                        )
                    )
        return ObservationIndex(
            archive=self.archive_id,
            release_label=self.release_label,
            query_text=query,
            target=target.key,
            records=tuple(records),
            licence=self.licence,
            citation=self.citation,
        )

    def fetch(self, product: ProductRef) -> FetchedProduct:
        if product.archive != self.archive_id:
            raise ValueError(f"{product.product_id} is a {product.archive} product, not an IRSA WISE one")
        parse_product_id(product.product_id)
        response = self.transport.send(HttpRequest("GET", product.uri))
        if response.status != 200:
            raise TransportError(f"IRSA {product.product_id}: HTTP {response.status}", response.status)
        validate_votable(response.body, MAX_PRODUCT_BYTES)
        votable.parse(response.body)  # a TAP error document is an error, never a product
        return FetchedProduct(
            product, response.body, MEDIA_TYPE, self.release_label, self.licence, self.citation, product.uri
        )

    def cutout(self, field: Field, size: u.Quantity) -> FetchedProduct:
        raise LookupError("IRSA WISE light curves are tables, not images to cut out")


@dataclass
class IrsaWiseInventory:
    """The AllWISE source a light curve was cut around: its designation, position and photometry
    quality, read as the ObsCore-shaped row. IRSA lists no DataLink rows for these tables."""

    transport: Transport
    service_url: str = IRSA_TAP

    def rows(self, product: ProductRef) -> ProductRows:
        designation, ra, dec, wise = parse_product_id(product.product_id)
        # designation is not indexed; the product's position is, so the source is looked up in a cone
        query = (
            f"SELECT designation, ra, dec, w1mpro, w2mpro, ph_qual, ext_flg FROM {SOURCE_TABLE} "
            f"WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.7f}, {dec:+.7f}, {1.0 / 3600.0!r})) "
            f"AND designation = {literal(designation)}"
        )
        found = run_sync(self.transport, sync_request(self.service_url, query, 1), "IRSA TAP").to_pylist()
        if not found:
            raise LookupError(f"AllWISE has no source {designation}")
        row = found[0]
        obscore = {
            "obs_id": f"WISE {designation}",
            "obs_collection": wise.mission,
            "s_ra": row["ra"],
            "s_dec": row["dec"],
            "ph_qual": row["ph_qual"],
            "ext_flg": row["ext_flg"],
        }
        inventory = {
            "productType": "SCIENCE",
            "table": wise.table,
            "calib_level": 2,
            "dataRights": "PUBLIC",
        }
        return ProductRows(inventory, obscore, (), f"POST {self.service_url}/sync {query}")
