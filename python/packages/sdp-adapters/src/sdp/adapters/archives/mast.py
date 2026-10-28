"""MAST behind ObservationArchiveGateway (Investigate §4): the observation inventory through MAST's
ObsCore TAP service (CAOM), products by their access URL, and cutouts from HAPCut.

One gateway serves every MAST mission: ``criteria.missions`` selects ObsCore ``obs_collection``
values (HST, JWST, TESS, …). Everything travels through a Transport, so CI replays cassettes and
never calls MAST (ADR-015). ObsCore lists no cloud copy, so ``cloud_uri`` stays None (D22).
"""

from __future__ import annotations

import io
import math
import urllib.parse
import zipfile
from dataclasses import dataclass

import astropy.units as u

from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.adapters.archives.vo import literal, run_sync, sync_request
from sdp.kernel.domain.investigation import (
    FetchedProduct,
    Field,
    ObservationCriteria,
    ObservationIndex,
    ObservationRecord,
    ProductRef,
    region_centre,
)
from sdp.kernel.ports.investigate import ObservationTarget

MAST_OBSCORE = "https://mast.stsci.edu/vo-tap/api/v0.1/caom"
HAPCUT = "https://mast.stsci.edu/hapcut/api/v0.1/astrocut"
LICENCE = "MAST data are public after any exclusive-access period; see https://archive.stsci.edu/publishing/data-use"
CITATION = "Data from the Mikulski Archive for Space Telescopes (MAST), STScI; cite each mission's own reference"

#: ObsCore dataproduct_type to the product types Investigate tests declare. A MAST time series is
#: mission FITS, not the canonical ``lightcurve`` CSV, so it keeps its own type until a MAST
#: product dialect curates it; a variability test never reads FITS bytes as CSV.
PRODUCT_TYPES = {"timeseries": "timeseries", "image": "image", "spectrum": "spectrum", "cube": "cube"}
OBSCORE_TYPES = {v: k for k, v in PRODUCT_TYPES.items()}
HAPCUT_PREFIX = "hapcut/"

COLUMNS = (
    "obs_id",
    "obs_collection",
    "instrument_name",
    "dataproduct_type",
    "t_min",
    "t_max",
    "access_url",
    "access_format",
    "access_estsize",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(literal(v) for v in values)})"


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    f = float(value)  # type: ignore[arg-type]
    return f if math.isfinite(f) else None


@dataclass
class MastObservationGateway:
    transport: Transport
    service_url: str = MAST_OBSCORE
    cutout_url: str = HAPCUT
    max_records: int = 500
    archive_id: str = "mast"
    release_label: str = "mast:caom"
    licence: str = LICENCE
    citation: str = CITATION

    def search_query(self, target: ObservationTarget, criteria: ObservationCriteria) -> str:
        where = [
            "1 = CONTAINS(POINT('ICRS', s_ra, s_dec), "
            f"CIRCLE('ICRS', {float(target.ra_deg)!r}, {float(target.dec_deg)!r}, {criteria.radius_arcsec / 3600.0!r}))",
            "access_url IS NOT NULL",
        ]
        if criteria.missions:
            where.append(_in("obs_collection", criteria.missions))
        if criteria.product_types:
            where.append(_in("dataproduct_type", tuple(OBSCORE_TYPES.get(t, t) for t in criteria.product_types)))
        if criteria.epoch_min_mjd is not None:
            where.append(f"t_min >= {float(criteria.epoch_min_mjd)!r}")
        if criteria.epoch_max_mjd is not None:
            where.append(f"t_max <= {float(criteria.epoch_max_mjd)!r}")
        return (
            f"SELECT TOP {self.max_records} {', '.join(COLUMNS)} FROM ivoa.obscore "
            f"WHERE {' AND '.join(where)} ORDER BY t_min, obs_id, access_url"
        )

    def search_request(self, target: ObservationTarget, criteria: ObservationCriteria) -> HttpRequest:
        return sync_request(self.service_url, self.search_query(target, criteria), self.max_records)

    def search(self, target: ObservationTarget, criteria: ObservationCriteria) -> ObservationIndex:
        table = run_sync(self.transport, self.search_request(target, criteria), "MAST ObsCore")
        records = []
        for row in table.to_pylist():
            url = str(row.get("access_url") or "").strip()
            if not url:
                continue
            size = _number(row.get("access_estsize"))
            records.append(
                ObservationRecord(
                    product=ProductRef(self.archive_id, str(row["obs_id"]).strip(), url),
                    mission=str(row["obs_collection"]).strip(),
                    instrument=str(row.get("instrument_name") or "").strip(),
                    product_type=PRODUCT_TYPES.get(str(row["dataproduct_type"]).strip(), str(row["dataproduct_type"])),
                    epoch_mjd=_number(row.get("t_min")),
                    epoch_end_mjd=_number(row.get("t_max")),
                    size_bytes=int(size * 1024) if size is not None else None,
                    licence=self.licence,
                    citation=self.citation,
                )
            )
        return ObservationIndex(
            archive=self.archive_id,
            release_label=self.release_label,
            query_text=self.search_query(target, criteria),
            target=target.key,
            records=tuple(records),
            licence=self.licence,
            citation=self.citation,
        )

    def fetch(self, product: ProductRef) -> FetchedProduct:
        if product.archive != self.archive_id:
            raise ValueError(f"{product.product_id} is a {product.archive} product, not a MAST one")
        response = self.transport.send(HttpRequest("GET", product.uri))
        if response.status != 200:
            raise TransportError(f"MAST {product.product_id}: HTTP {response.status}", response.status)
        if product.product_id.startswith(HAPCUT_PREFIX):
            member = product.product_id.removeprefix(HAPCUT_PREFIX)
            _, data = _fits_member(response.body, member)
            return FetchedProduct(
                product, data, "image/fits", self.release_label, self.licence, self.citation, product.uri
            )
        media = response.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
        return FetchedProduct(
            product, response.body, media, self.release_label, self.licence, self.citation, product.uri
        )

    def cutout_request(self, field: Field, size: u.Quantity) -> HttpRequest:
        ra, dec = region_centre(field.region)
        side = float(size.to_value(u.deg))
        if not 0 < side <= 1.0:
            raise ValueError("a cutout stays within a degree")
        query = urllib.parse.urlencode({"ra": f"{ra:.8f}", "dec": f"{dec:.8f}", "x": side, "y": side, "units": "d"})
        return HttpRequest("GET", f"{self.cutout_url}?{query}")

    def cutout(self, field: Field, size: u.Quantity) -> FetchedProduct:
        """The first FITS file of HAPCut's zip; the zip is a container, the FITS file is the product."""
        request = self.cutout_request(field, size)
        response = self.transport.send(request)
        if response.status != 200:
            raise TransportError(f"HAPCut: HTTP {response.status}", response.status)
        name, data = _fits_member(response.body)
        product = ProductRef(self.archive_id, f"{HAPCUT_PREFIX}{name}", request.url)
        return FetchedProduct(product, data, "image/fits", self.release_label, self.licence, self.citation, request.url)


def _fits_member(body: bytes, member: str | None = None) -> tuple[str, bytes]:
    """A FITS file from HAPCut's zip: the named member, or the first by name."""
    if not zipfile.is_zipfile(io.BytesIO(body)):
        raise TransportError(f"HAPCut returned no cutout: {body[:200]!r}")
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        names = sorted(n for n in zf.namelist() if n.lower().endswith((".fits", ".fits.gz", ".fit")))
        if member is not None:
            names = [n for n in names if n == member]
        if not names:
            raise TransportError(f"HAPCut zip holds no FITS file{f' named {member}' if member else ''}")
        return names[0], zf.read(names[0])
