"""MAST behind ObservationArchiveGateway (Investigate §4): the observation inventory through MAST's
ObsCore TAP service (CAOM), products by their access URL, and cutouts from HAPCut.

One gateway serves every MAST mission: ``criteria.missions`` selects ObsCore ``obs_collection``
values (HST, JWST, TESS, …). Everything travels through a Transport, so CI replays cassettes and
never calls MAST (ADR-015). ObsCore lists no cloud copy, so ``cloud_uri`` stays None (D22).
"""

from __future__ import annotations

import io
import math
import posixpath
import re
import urllib.parse
import zipfile
from dataclasses import dataclass, replace

import astropy.units as u

from sdp.adapters.archives.fits_range import range_cutout, range_cutout_cost
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
from sdp.kernel.ports.investigate import CutoutCost, ObservationTarget, cutout_product_id

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


#: JWST lists every file of an observation as an ObsCore row of the observation's type; only the
#: calibrated science images are what an image test reads (segmentation maps, previews and
#: association tables are not).
JWST_SCIENCE_IMAGES = ("_i2d.fits", "_cal.fits")


def file_name(uri: str) -> str:
    """The product file name from an access URL or a ``mast:`` data URI."""
    parsed = urllib.parse.urlparse(uri)
    query = urllib.parse.parse_qs(parsed.query)
    path = query["uri"][0] if "uri" in query else (parsed.path if parsed.scheme != "mast" else uri)
    return posixpath.basename(urllib.parse.unquote(path))


def product_id(obs_id: str, access_url: str) -> str:
    """One id per file: ObsCore lists several files under one ``obs_id``, and the planner and the
    plan gate key acquisitions by product id."""
    return f"{obs_id}/{file_name(access_url)}"


def obs_id_of(product_id: str) -> str:
    """The observation a product id names; an id from before file-level ids is the obs_id itself."""
    return product_id.partition("/")[0]


#: A filter in a MAST file name: F200W, F444W-F470N, F1130W, F850LP, F657N, F322W2.
_FILTER = re.compile(r"^f\d{3,4}[a-z]{0,2}\d?$")
#: An HST rootname: ``ipppss`` names a visit's mosaic, ``ipppssoo`` one exposure in it.
_HST_VISIT = re.compile(r"^[a-z0-9]{6}$")
_HST_EXPOSURE = re.compile(r"^[a-z0-9]{8}$")


def file_band(name: str) -> tuple[str | None, bool]:
    """The filter a MAST product's file name names, and whether the file is a single exposure.

    JWST level-3 names (``jw02739-o001_t001_nircam_clear-f200w_i2d.fits``) carry the filter pair, of
    which ``clear`` and a subarray (``sub160``) are dropped; a JWST name without the ``-o``
    observation part is one exposure. HST HAP names (``hst_13926_05_wfc3_uvis_f657n_ick905_drc.fits``) carry the filter, then the visit
    (6 characters) or one exposure (8); a sky-cell mosaic (``..._all_drc.fits``) combines visits.
    A detection image (``total``) and a legacy rootname (``j9cs01010_drz.fits``) name no filter.
    """
    stem = name.lower().rsplit(".", 1)[0]
    parts = stem.split("_")
    if stem.startswith("jw"):
        single = re.match(r"^jw\d{5}-[oac]\d+", stem) is None
        for part in reversed(parts):
            filters = [f for f in part.split("-") if _FILTER.match(f)]
            if filters and all(f == "clear" or f.startswith("sub") or _FILTER.match(f) for f in part.split("-")):
                return "-".join(f.upper() for f in filters), single
        return None, single
    if stem.startswith("hst_"):
        for i, part in enumerate(parts):
            filters = [f for f in part.split("-") if _FILTER.match(f)]
            if filters and len(filters) == len(part.split("-")):
                after = parts[i + 1] if i + 1 < len(parts) else ""
                return "-".join(f.upper() for f in filters), bool(_HST_EXPOSURE.match(after))
        return None, False
    return None, False


def _science_file(row: dict[str, object]) -> bool:
    """FITS files only, whatever the mission; for JWST images, only the calibrated science images."""
    if "fits" not in str(row.get("access_format") or "").lower():
        return False
    if str(row.get("obs_collection") or "").strip() == "JWST" and str(row.get("dataproduct_type")).strip() == "image":
        return file_name(str(row["access_url"])).endswith(JWST_SCIENCE_IMAGES)
    return True


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
            # The science-file filter runs in the query too, so previews and auxiliary files do not use
            # up the TOP limit. LIKE's "_" is a wildcard, so the server keeps a superset of what
            # _science_file accepts, and _science_file still decides.
            "LOWER(access_format) LIKE '%fits%'",
            "(obs_collection <> 'JWST' OR dataproduct_type <> 'image' OR "
            + " OR ".join(f"access_url LIKE '%{suffix}'" for suffix in JWST_SCIENCE_IMAGES)
            + ")",
        ]
        if criteria.missions:
            where.append(_in("obs_collection", criteria.missions))
        if criteria.product_types:
            where.append(_in("dataproduct_type", tuple(OBSCORE_TYPES.get(t, t) for t in criteria.product_types)))
        if criteria.min_calibration_level is not None:
            where.append(f"calib_level >= {int(criteria.min_calibration_level)}")
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
            if not url or not _science_file(row):
                continue
            # MAST reports access_estsize in bytes (checked live, 2026-10-29), not the kB ObsCore defines.
            size = _number(row.get("access_estsize"))
            band, single = file_band(file_name(url))
            records.append(
                ObservationRecord(
                    product=ProductRef(self.archive_id, product_id(str(row["obs_id"]).strip(), url), url),
                    mission=str(row["obs_collection"]).strip(),
                    instrument=str(row.get("instrument_name") or "").strip(),
                    product_type=PRODUCT_TYPES.get(str(row["dataproduct_type"]).strip(), str(row["dataproduct_type"])),
                    epoch_mjd=_number(row.get("t_min")),
                    epoch_end_mjd=_number(row.get("t_max")),
                    size_bytes=int(size) if size is not None else None,
                    licence=self.licence,
                    citation=self.citation,
                    band=band,
                    single_exposure=single,
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

    def cutout_product(self, product: ProductRef, ra_deg: float, dec_deg: float, side_arcsec: float) -> FetchedProduct:
        """A cutout of one listed FITS image, read by byte range: MAST publishes no SODA service for
        JWST, and its downloads answer range requests (checked live, 2026-10-29; INT-004)."""
        if product.archive != self.archive_id:
            raise ValueError(f"{product.product_id} is a {product.archive} product, not a MAST one")
        cut = range_cutout(self.transport, product.uri, ra_deg, dec_deg, side_arcsec)
        ref = replace(product, product_id=cutout_product_id(product.product_id, ra_deg, dec_deg, side_arcsec))
        # Read from the product's own URL; which rows were read is in the cutout's header (SDPCUTX0/Y0).
        return FetchedProduct(
            ref, cut.data, "application/fits", self.release_label, self.licence, self.citation, product.uri
        )

    def cutout_cost(self, product: ProductRef, ra_deg: float, dec_deg: float, side_arcsec: float) -> CutoutCost:
        """What ``cutout_product`` would read and keep, from the product's headers alone (INT-026)."""
        if product.archive != self.archive_id:
            raise ValueError(f"{product.product_id} is a {product.archive} product, not a MAST one")
        cost = range_cutout_cost(self.transport, product.uri, ra_deg, dec_deg, side_arcsec)
        return CutoutCost(cost.read_bytes, cost.stored_bytes, cost.coverage)

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
