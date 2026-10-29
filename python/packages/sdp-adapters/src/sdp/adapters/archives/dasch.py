"""DASCH (Harvard plates, DR7) behind ObservationArchiveGateway, and the dialect that curates its light
curves into the canonical ``lightcurve`` and ``astrometry`` products (Investigate §4, §6).

The gateway calls the DASCH data API endpoints daschlab calls (``querycat``, ``lightcurve``,
``queryexps``, ``cutout``) through a Transport, so CI replays cassettes and never calls DASCH
(ADR-015, D20). Their answers are JSON lists of CSV lines with a header row.
"""

from __future__ import annotations

import base64
import binascii
import csv
import gzip
import json
import math
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import astropy.units as u
import numpy as np

from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.kernel.domain.investigation import (
    FetchedProduct,
    Field,
    ObservationCriteria,
    ObservationIndex,
    ObservationRecord,
    ProductRef,
    region_centre,
)
from sdp.kernel.ports.investigate import CuratedProduct, ObservationTarget
from sdp.kernel.science import products

DASCH_API = "https://api.starglass.cfa.harvard.edu/public"
LICENCE = "DASCH DR7 public data; acknowledge as https://dasch.cfa.harvard.edu/citing/ asks"
CITATION = "Grindlay et al. 2009, ASP Conf. Ser. 410, 101 (DASCH)"
MISSION = "DASCH"
CUTOUT_PREFIX = "cutout/"
PRODUCT_TYPES = (products.LIGHTCURVE, products.ASTROMETRY)

#: AFLAGS bits daschlab's standard cleaning rejects: high background, large isophotal and local RMS,
#: close to the limiting magnitude, unknown radial bin.
STANDARD_BAD_AFLAGS = (1 << 6) | (1 << 11) | (1 << 12) | (1 << 13) | (1 << 15)
#: The DR7 light-curve columns DaschDialect reads; the weekly smoke fails naming any that disappear.
LIGHTCURVE_COLUMNS = frozenset(
    {
        "date_jd",
        "magcal_magdep",
        "magcal_magdep_rms",
        "ra_deg",
        "dec_deg",
        "gsc_bin_index",
        "sextractor_number",
        "aflags",
        "drad_rms2",
    }
)
RMS_FLAG = 99.0
MJD_OFFSET = 2400000.5
J2000_JD = 2451545.0
#: The epochs DASCH's reference catalogues' positions can be at: APASS was observed from about 2009 to
#: 2015, and ATLAS-RefCat2 is at 2015.5. A moving target is looked for along its path over this span.
REFCAT_EPOCHS = (2000.0, 2016.0)


def csv_lines(body: bytes, what: str) -> list[dict[str, str]]:
    """DASCH's JSON list of CSV lines as rows; anything else is an API error."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TransportError(f"DASCH {what}: not JSON") from exc
    if not isinstance(data, list) or not all(isinstance(line, str) for line in data):
        raise TransportError(f"DASCH {what} failed: {str(data)[:200]}")
    return list(csv.DictReader(data))


def _separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 3600.0


def _path(target: ObservationTarget) -> tuple[tuple[float, float], tuple[float, float]]:
    """Where a moving target was at each end of REFCAT_EPOCHS, as offsets in arcsec (east, north)
    from its position; a still target's path is the point itself."""
    if not target.moving:
        return (0.0, 0.0), (0.0, 0.0)
    assert target.pm_ra_cosdec_masyr is not None and target.pm_dec_masyr is not None
    assert target.epoch_jyear is not None
    ends = tuple(
        (
            target.pm_ra_cosdec_masyr * (t - target.epoch_jyear) / 1000.0,
            target.pm_dec_masyr * (t - target.epoch_jyear) / 1000.0,
        )
        for t in REFCAT_EPOCHS
    )
    return ends[0], ends[1]


def _distance_to_path(target: ObservationTarget, ra: float, dec: float) -> float:
    """How far a catalogue star is, in arcsec, from the nearest point of the target's path."""
    if not target.moving:
        return _separation_arcsec(target.ra_deg, target.dec_deg, ra, dec)
    east = ((ra - target.ra_deg + 180.0) % 360.0 - 180.0) * math.cos(math.radians(target.dec_deg)) * 3600.0
    north = (dec - target.dec_deg) * 3600.0
    (x0, y0), (x1, y1) = _path(target)
    dx, dy = x1 - x0, y1 - y0
    length2 = dx * dx + dy * dy
    s = 0.0 if length2 == 0.0 else max(0.0, min(1.0, ((east - x0) * dx + (north - y0) * dy) / length2))
    return math.hypot(east - (x0 + s * dx), north - (y0 + s * dy))


def _float(row: Mapping[str, str], key: str) -> float:
    value = (row.get(key) or "").strip()
    try:
        return float(value) if value else math.nan
    except ValueError:
        return math.nan


def _int(row: Mapping[str, str], key: str) -> int:
    value = _float(row, key)
    return int(value) if math.isfinite(value) else 0


def _json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


@dataclass
class DaschGateway:
    transport: Transport
    base_url: str = DASCH_API
    refcat: str = "apass"
    archive_id: str = "dasch"
    release_label: str = "dasch:DR7"
    licence: str = LICENCE
    citation: str = CITATION

    def _post(self, endpoint: str, payload: Mapping[str, object]) -> HttpRequest:
        return HttpRequest(
            "POST", f"{self.base_url}/dasch/dr7/{endpoint}", _json(payload), {"Content-Type": "application/json"}
        )

    def querycat_request(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> HttpRequest:
        return self._post(
            "querycat",
            {"refcat": self.refcat, "ra_deg": float(ra_deg), "dec_deg": float(dec_deg), "radius_arcsec": radius_arcsec},
        )

    def lightcurve_request(self, refcat: str, gsc_bin_index: int, ref_number: int) -> HttpRequest:
        return self._post(
            "lightcurve", {"refcat": refcat, "gsc_bin_index": int(gsc_bin_index), "ref_number": int(ref_number)}
        )

    def queryexps_request(self, ra_deg: float, dec_deg: float) -> HttpRequest:
        return self._post("queryexps", {"ra_deg": float(ra_deg), "dec_deg": float(dec_deg)})

    def cutout_request(self, plate_id: str, solution_number: int, ra_deg: float, dec_deg: float) -> HttpRequest:
        return self._post(
            "cutout",
            {
                "plate_id": plate_id,
                "solution_number": int(solution_number),
                "center_ra_deg": float(ra_deg),
                "center_dec_deg": float(dec_deg),
            },
        )

    def _uri(self, refcat: str, gsc_bin_index: int, ref_number: int) -> str:
        query = urllib.parse.urlencode({"refcat": refcat, "gsc_bin_index": gsc_bin_index, "ref_number": ref_number})
        return f"{self.base_url}/dasch/dr7/lightcurve?{query}"

    def search(self, target: ObservationTarget, criteria: ObservationCriteria) -> ObservationIndex:
        """The nearest reference-catalogue star within the radius, offered as a light curve and as a
        position history (both read from its DR7 light curve). A moving target is matched against its
        path over the reference catalogue's possible epochs, so a fast star is still found."""
        reach = max(math.hypot(*end) for end in _path(target))
        request = self.querycat_request(target.ra_deg, target.dec_deg, criteria.radius_arcsec + reach)
        wanted = [t for t in PRODUCT_TYPES if not criteria.product_types or t in criteria.product_types]
        records: list[ObservationRecord] = []
        if wanted and (not criteria.missions or MISSION in criteria.missions):
            response = self.transport.send(request)
            if response.status != 200:
                raise TransportError(f"DASCH querycat: HTTP {response.status}", response.status)
            stars = []
            for row in csv_lines(response.body, "querycat"):
                ra, dec = _float(row, "ra_deg"), _float(row, "dec_deg")
                if not (math.isfinite(ra) and math.isfinite(dec)):
                    continue
                sep = _distance_to_path(target, ra, dec)
                if sep <= criteria.radius_arcsec:
                    stars.append((sep, _int(row, "gsc_bin_index"), _int(row, "ref_number")))
            if stars:
                _, gsc_bin, ref = min(stars)
                product = ProductRef(
                    self.archive_id, f"{self.refcat}:{gsc_bin}:{ref}", self._uri(self.refcat, gsc_bin, ref)
                )
                records = [
                    ObservationRecord(
                        product=product,
                        mission=MISSION,
                        instrument="harvard-plates",
                        product_type=t,
                        epoch_mjd=None,
                        size_bytes=None,
                        licence=self.licence,
                        citation=self.citation,
                    )
                    for t in wanted
                ]
        return ObservationIndex(
            archive=self.archive_id,
            release_label=self.release_label,
            query_text=f"POST {request.url} {request.body.decode()}",
            target=target.key,
            records=tuple(records),
            licence=self.licence,
            citation=self.citation,
        )

    def fetch(self, product: ProductRef) -> FetchedProduct:
        """The star's DR7 light curve, as DASCH sent it (a JSON list of CSV lines), or a plate
        cutout this gateway made earlier (its URI keeps the request)."""
        if product.archive != self.archive_id:
            raise ValueError(f"{product.product_id} is a {product.archive} product, not a DASCH one")
        if product.product_id.startswith(CUTOUT_PREFIX):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(product.uri).query)
            plate_id, solnum = query["plate_id"][0], int(query["solution_number"][0])
            ra, dec = float(query["ra"][0]), float(query["dec"][0])
            return self._cutout(plate_id, solnum, ra, dec)
        refcat, gsc_bin, ref = product.product_id.split(":")
        response = self.transport.send(self.lightcurve_request(refcat, int(gsc_bin), int(ref)))
        if response.status != 200:
            raise TransportError(f"DASCH lightcurve {product.product_id}: HTTP {response.status}", response.status)
        csv_lines(response.body, f"lightcurve {product.product_id}")
        return FetchedProduct(
            product, response.body, "application/json", self.release_label, self.licence, self.citation, product.uri
        )

    def cutout(self, field: Field, size: u.Quantity) -> FetchedProduct:
        """A cutout of the field from the first plate (by series, number and solution) whose WCS was
        solved by DASCH's own pipeline. DR7 cutouts have a fixed size, so ``size`` is not sent."""
        ra, dec = region_centre(field.region)
        response = self.transport.send(self.queryexps_request(ra, dec))
        if response.status != 200:
            raise TransportError(f"DASCH queryexps: HTTP {response.status}", response.status)
        exposures = sorted(
            (row["series"], _int(row, "platenum"), _int(row, "solnum"))
            for row in csv_lines(response.body, "queryexps")
            if (row.get("wcssource") or "").strip().lower() == "imwcs" and row.get("series")
        )
        if not exposures:
            raise TransportError(f"DASCH has no plate with a solved WCS at ({ra:.5f}, {dec:.5f})")
        series, platenum, solnum = exposures[0]
        return self._cutout(f"{series}{platenum:05d}", solnum, ra, dec)

    def _cutout(self, plate_id: str, solnum: int, ra: float, dec: float) -> FetchedProduct:
        ra, dec = round(ra, 6), round(dec, 6)  # the precision the product URI keeps
        request = self.cutout_request(plate_id, solnum, ra, dec)
        answer = self.transport.send(request)
        if answer.status != 200:
            raise TransportError(f"DASCH cutout {plate_id}: HTTP {answer.status}", answer.status)
        try:
            encoded = json.loads(answer.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TransportError(f"DASCH cutout {plate_id}: not JSON") from exc
        if not isinstance(encoded, str):
            raise TransportError(f"DASCH cutout {plate_id} failed: {str(encoded)[:200]}")
        try:
            data = gzip.decompress(base64.b64decode(encoded))
        except (binascii.Error, OSError) as exc:
            raise TransportError(f"DASCH cutout {plate_id}: not a gzipped FITS file") from exc
        uri = f"{request.url}?plate_id={plate_id}&solution_number={solnum}&ra={ra:.6f}&dec={dec:.6f}"
        product = ProductRef(self.archive_id, f"{CUTOUT_PREFIX}{plate_id}/{solnum}", uri)
        return FetchedProduct(product, data, "image/fits", self.release_label, self.licence, self.citation, uri)


@dataclass(frozen=True)
class DaschDialect:
    """DR7 light curves to canonical products. A row is used only when it is a detection
    (``gsc_bin_index`` or ``sextractor_number`` non-zero) with no standard bad AFLAGS.

    - ``lightcurve``: MJD from ``date_jd``, ``magcal_magdep`` and its RMS (99 is DASCH's no-value flag)
    - ``astrometry``: Julian epoch from ``date_jd``, ``ra_deg``/``dec_deg``, and ``drad_rms2`` (arcsec)
      as the position error, or ``default_pos_err_mas`` where DASCH gives none.
    """

    default_pos_err_mas: float = 1000.0
    reject_aflags: int = STANDARD_BAD_AFLAGS
    dialect_id: str = "dasch-dr7-lightcurve"
    dialect_version: str = "1.0.0"

    def _detections(self, raw: bytes) -> list[Mapping[str, str]]:
        rows = []
        for row in csv_lines(raw, "lightcurve"):
            if _int(row, "gsc_bin_index") == 0 and _int(row, "sextractor_number") == 0:
                continue
            if _int(row, "aflags") & self.reject_aflags:
                continue
            if not math.isfinite(_float(row, "date_jd")):
                continue
            rows.append(row)
        return sorted(rows, key=lambda r: _float(r, "date_jd"))

    def curate(self, product_type: str, raw: FetchedProduct) -> CuratedProduct | None:
        if product_type == products.LIGHTCURVE:
            return CuratedProduct(self.lightcurve(raw.data), "text/csv")
        if product_type == products.ASTROMETRY:
            return CuratedProduct(self.astrometry(raw.data), "text/csv")
        return None

    def lightcurve(self, raw: bytes) -> bytes:
        kept: list[tuple[float, float, float]] = []
        for row in self._detections(raw):
            mag, err = _float(row, "magcal_magdep"), _float(row, "magcal_magdep_rms")
            if math.isfinite(mag) and math.isfinite(err) and 0 < err < RMS_FLAG:
                kept.append((_float(row, "date_jd") - MJD_OFFSET, mag, err))
        return products.lightcurve_csv(*_columns(kept, 3))

    def astrometry(self, raw: bytes) -> bytes:
        kept: list[tuple[float, float, float, float]] = []
        for row in self._detections(raw):
            ra, dec, drad = _float(row, "ra_deg"), _float(row, "dec_deg"), _float(row, "drad_rms2")
            if not (math.isfinite(ra) and math.isfinite(dec)):
                continue
            err = drad * 1000.0 if math.isfinite(drad) and 0 < drad < RMS_FLAG else self.default_pos_err_mas
            epoch = 2000.0 + (_float(row, "date_jd") - J2000_JD) / 365.25
            kept.append((epoch, ra % 360.0, dec, err))
        return products.positions_csv(*_columns(kept, 4))


def _columns(rows: Sequence[tuple[float, ...]], n: int) -> list[np.ndarray]:
    return [np.array([r[i] for r in rows], dtype=float) for i in range(n)]
