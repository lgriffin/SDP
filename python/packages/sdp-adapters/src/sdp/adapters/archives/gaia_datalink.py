"""Gaia DR3's non-tabular products through DataLink (Integration v0.4.0 §3, §7).

The Gaia archive serves epoch photometry and BP/RP spectra not as TAP tables but as products a
DataLink service lists per source (``datalink/links?ID=Gaia DR3 <source_id>``), each fetched as a
VOTable:

- ``GaiaDataLinkGateway`` finds the sources in a cone that have a product (``has_epoch_photometry``,
  ``has_xp_sampled`` in ``gaiadr3.gaia_source``) and fetches a product by the URL DataLink lists;
- ``GaiaDataLinkInventory`` reads the product's DataLink rows and the source's position, the archive's
  own description of the product beside its bytes.

``sdp.adapters.products.gaia`` envelopes and curates what they fetch. Every request goes through the
archive ``Transport`` (ADR-015), so CI replays recordings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import astropy.units as u

from sdp.adapters.archives import votable
from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.adapters.archives.untrusted import validate_votable
from sdp.adapters.archives.vo import run_sync, sync_request
from sdp.adapters.gaia_dr3 import (
    CITATION,
    DR3_SPAN_MJD,
    GAIA_PRODUCTS,
    GAIA_TAP,
    LICENCE,
    MEDIA_TYPE,
    MISSION,
    RELEASE,
    GaiaProduct,
    data_url,
    links_url,
    parse_product_id,
    product_id,
)
from sdp.kernel.domain.integration import RelationshipSemantics
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


def _wanted(criteria: ObservationCriteria) -> list[GaiaProduct]:
    """The products the criteria allow. Every DR3 product spans the same years, so an epoch window
    outside them allows none."""
    if criteria.missions and MISSION not in {m.upper() for m in criteria.missions}:
        return []
    if criteria.epoch_min_mjd is not None and DR3_SPAN_MJD[1] < criteria.epoch_min_mjd:
        return []
    if criteria.epoch_max_mjd is not None and DR3_SPAN_MJD[0] > criteria.epoch_max_mjd:
        return []
    floor = criteria.min_calibration_level or 0
    return [
        p
        for p in GAIA_PRODUCTS.values()
        if (not criteria.product_types or p.product_type in criteria.product_types) and p.calibration_level >= floor
    ]


@dataclass
class GaiaDataLinkGateway:
    """Gaia DR3 sources' DataLink products behind ObservationArchiveGateway."""

    transport: Transport
    service_url: str = GAIA_TAP
    max_records: int = 2000
    archive_id: str = "gaia-datalink"
    release_label: str = "gaia:DR3"
    licence: str = LICENCE
    citation: str = CITATION

    def search_query(self, target: ObservationTarget, wanted: Sequence[GaiaProduct], radius_arcsec: float) -> str:
        flags = " OR ".join(f"{p.flag} = 'True'" for p in wanted)
        return (
            f"SELECT TOP {self.max_records} source_id, {', '.join(p.flag for p in wanted)} "
            "FROM gaiadr3.gaia_source WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), "
            f"CIRCLE('ICRS', {float(target.ra_deg)!r}, {float(target.dec_deg)!r}, {radius_arcsec / 3600.0!r})) "
            f"AND ({flags}) ORDER BY source_id"
        )

    def search(self, target: ObservationTarget, criteria: ObservationCriteria) -> ObservationIndex:
        """Every source in the cone with a wanted product, one record per product. Positions are
        Gaia's own (epoch 2016.0), which is also the epoch Identify reads, so no motion is applied."""
        wanted = _wanted(criteria)
        records: list[ObservationRecord] = []
        query = ""
        if wanted:
            query = self.search_query(target, wanted, criteria.radius_arcsec)
            table = run_sync(self.transport, sync_request(self.service_url, query, self.max_records), "Gaia TAP")
            for row in table.to_pylist():
                for product in wanted:
                    if row.get(product.flag) is not True:
                        continue
                    source = int(row["source_id"])
                    records.append(
                        ObservationRecord(
                            product=ProductRef(
                                self.archive_id,
                                product_id(source, product.retrieval_type),
                                data_url(source, product.retrieval_type),
                            ),
                            mission=MISSION,
                            instrument=product.instrument,
                            product_type=product.product_type,
                            epoch_mjd=DR3_SPAN_MJD[0],
                            epoch_end_mjd=DR3_SPAN_MJD[1],
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
            raise ValueError(f"{product.product_id} is a {product.archive} product, not a Gaia DataLink one")
        parse_product_id(product.product_id)
        response = self.transport.send(HttpRequest("GET", product.uri))
        if response.status != 200:
            raise TransportError(f"Gaia DataLink {product.product_id}: HTTP {response.status}", response.status)
        validate_votable(response.body)
        return FetchedProduct(
            product, response.body, MEDIA_TYPE, self.release_label, self.licence, self.citation, product.uri
        )

    def cutout(self, field: Field, size: u.Quantity) -> FetchedProduct:
        raise LookupError("Gaia DataLink serves no images to cut out")


@dataclass
class GaiaDataLinkInventory:
    """The DataLink rows for a product's source and the source's position (Stage A for Gaia).

    Gaia has no ObsCore view, so the ObsCore-shaped row holds what ``gaia_source`` says of the
    source; the inventory row is the product's own ``#this`` DataLink row."""

    transport: Transport
    service_url: str = GAIA_TAP

    def rows(self, product: ProductRef) -> ProductRows:
        source, gaia = parse_product_id(product.product_id)
        request = HttpRequest("GET", links_url(source))
        response = self.transport.send(request)
        if response.status != 200:
            raise TransportError(f"Gaia DataLink links for {source}: HTTP {response.status}", response.status)
        validate_votable(response.body)
        links = tuple(_link(r) for r in votable.parse(response.body).to_pylist())
        this = next(
            (
                r
                for r in links
                if r["semantics"] == RelationshipSemantics.THIS.value
                and f"RETRIEVAL_TYPE={gaia.retrieval_type}&" in f"{r['access_url']}&"
            ),
            None,
        )
        if this is None:
            raise LookupError(f"Gaia DataLink lists no {gaia.retrieval_type} for {RELEASE} {source}")
        query = f"SELECT source_id, ra, dec, ref_epoch FROM gaiadr3.gaia_source WHERE source_id = {int(source)}"
        position = run_sync(self.transport, sync_request(self.service_url, query, 1), "Gaia TAP").to_pylist()
        if not position:
            raise LookupError(f"gaia_source has no row for {RELEASE} {source}")
        row = position[0]
        obscore = {
            "obs_id": f"{RELEASE} {source}",
            "obs_collection": MISSION,
            "s_ra": row["ra"],
            "s_dec": row["dec"],
            "ref_epoch": row["ref_epoch"],
        }
        inventory = {
            "productType": "SCIENCE",
            "retrieval_type": gaia.retrieval_type,
            "content_type": this["content_type"],
            "calib_level": gaia.calibration_level,
            "dataRights": "PUBLIC",
        }
        return ProductRows(inventory, obscore, links, f"GET {request.url}\nPOST {self.service_url}/sync {query}")


def _link(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: (None if v is None else str(v)) for k, v in row.items() if k != "content_length"}
