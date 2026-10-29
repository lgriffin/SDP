"""T3 and T5 on real Gaia answers: DataLink products are found, fetched, enveloped, validated and
curated through the same product machinery as a JWST FITS file (INT-022).

The cassettes were recorded live from the Gaia archive on 2026-10-29: a 2-arcmin cone in the
Pleiades, the products of the variable star Gaia DR3 64971933089093760, and the links of a star
with no epoch photometry.
"""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.io.votable import from_table
from astropy.table import MaskedColumn, Table

from sdp.adapters.archives.gaia_datalink import GaiaDataLinkGateway, GaiaDataLinkInventory, _link, _wanted
from sdp.adapters.archives.transport import CassetteTransport, HttpRequest, HttpResponse, TransportError
from sdp.adapters.archives.untrusted import UntrustedInput
from sdp.adapters.gaia_dr3 import (
    ACKNOWLEDGEMENT,
    CITATION,
    GAIA_TIME_ORIGIN_MJD,
    LICENCE,
    MAG_PER_RELATIVE_FLUX,
    data_url,
    links_url,
    parse_product_id,
    product_id,
)
from sdp.adapters.products import EnvelopeValidator, GaiaDataLinkDialect
from sdp.adapters.products.fits import HduInfo
from sdp.adapters.products.gaia import epoch_lightcurve
from sdp.adapters.products.votable import check_votable, column_roles, numbers, read_tables
from sdp.kernel.domain.integration import AccessStatus, PayloadExtension, ProductMetadata, ProductType, Rights
from sdp.kernel.domain.investigation import FetchedProduct, ObservationCriteria, ProductRef
from sdp.kernel.ports.integration import RawProduct
from sdp.kernel.ports.investigate import CuratedProduct, ObservationTarget
from sdp.kernel.science.products import parse_lightcurve

pytestmark = [pytest.mark.tier("T5"), pytest.mark.req("INT-022")]

CASSETTES = Path(__file__).resolve().parents[1] / "cassettes" / "gaia-datalink"
VARIABLE = 64971933089093760
STAR = ObservationTarget("star", 56.534933945361814, 23.3473212122962)
FIELD = ObservationTarget("field", 56.5349, 23.3473)


def replay() -> CassetteTransport:
    return CassetteTransport(CASSETTES, "replay")


class Counting:
    """A transport that must not be called."""

    calls = 0

    def send(self, request: HttpRequest) -> HttpResponse:
        self.calls += 1
        raise AssertionError(f"unexpected request {request.url}")


def ref(retrieval_type: str, source: int = VARIABLE) -> ProductRef:
    return ProductRef("gaia-datalink", product_id(source, retrieval_type), data_url(source, retrieval_type))


def raw(retrieval_type: str) -> tuple[RawProduct, FetchedProduct]:
    transport = replay()
    fetched = GaiaDataLinkGateway(transport).fetch(ref(retrieval_type))
    rows = GaiaDataLinkInventory(transport).rows(fetched.product)
    digest = "sha256:" + hashlib.sha256(fetched.data).hexdigest()
    return (
        RawProduct(fetched, digest, rows.rendered_query, rows.inventory_row, rows.obscore_row, rows.links),
        fetched,
    )


def envelope(retrieval_type: str) -> tuple[ProductMetadata, RawProduct]:
    product, _ = raw(retrieval_type)
    return GaiaDataLinkDialect().normalize(product), product


def test_product_ids_name_the_source_and_the_retrieval_type() -> None:
    assert product_id(VARIABLE, "EPOCH_PHOTOMETRY") == f"Gaia DR3 {VARIABLE}/EPOCH_PHOTOMETRY"
    source, product = parse_product_id(f"Gaia DR3 {VARIABLE}/XP_SAMPLED")
    assert (source, product.product_type, product.calibration_level) == (str(VARIABLE), "spectrum", 3)
    assert data_url(VARIABLE, "XP_SAMPLED") == (
        f"https://gea.esac.esa.int/data-server/data?ID=Gaia+DR3+{VARIABLE}"
        "&RETRIEVAL_TYPE=XP_SAMPLED&LINKING_PARAMETER=SOURCE_ID"
    )
    assert links_url(VARIABLE) == f"https://gea.esac.esa.int/data-server/datalink/links?ID=Gaia+DR3+{VARIABLE}"
    for bad in (f"Gaia DR3 {VARIABLE}/MCMC_MSC", f"Gaia DR2 {VARIABLE}/XP_SAMPLED", "Gaia DR3 x/XP_SAMPLED"):
        with pytest.raises(ValueError, match=r"^'.*' is not a Gaia DataLink product id$"):
            parse_product_id(bad)


def test_a_cone_lists_one_record_per_product_of_every_source_that_has_one() -> None:
    index = GaiaDataLinkGateway(replay()).search(FIELD, ObservationCriteria(radius_arcsec=120.0))
    assert (index.archive, index.release_label, index.target) == ("gaia-datalink", "gaia:DR3", "field")
    assert len(index.records) == 6
    assert {r.product_type for r in index.records} == {"lightcurve", "spectrum"}
    variable = [r for r in index.records if r.product.product_id.startswith(f"Gaia DR3 {VARIABLE}/")]
    assert [r.product.product_id for r in variable] == [
        f"Gaia DR3 {VARIABLE}/EPOCH_PHOTOMETRY",
        f"Gaia DR3 {VARIABLE}/XP_SAMPLED",
    ]
    curve = variable[0]
    assert curve.product.uri == data_url(VARIABLE, "EPOCH_PHOTOMETRY") and curve.product.cloud_uri is None
    assert (curve.mission, curve.instrument, curve.size_bytes) == ("GAIA", "Astro, BP, RP", None)
    assert (curve.epoch_mjd, curve.epoch_end_mjd) == (56863.0, 57901.0)
    assert "CIRCLE('ICRS', 56.5349, 23.3473, 0.03333333333333333)" in index.query_text
    assert "(has_epoch_photometry = 'True' OR has_xp_sampled = 'True')" in index.query_text


def test_a_one_arcsecond_search_finds_only_the_star_itself() -> None:
    index = GaiaDataLinkGateway(replay()).search(STAR, ObservationCriteria(radius_arcsec=1.0))
    assert [r.product.product_id for r in index.records] == [
        f"Gaia DR3 {VARIABLE}/EPOCH_PHOTOMETRY",
        f"Gaia DR3 {VARIABLE}/XP_SAMPLED",
    ]


@pytest.mark.parametrize(
    ("criteria", "wanted"),
    [
        (ObservationCriteria(product_types=("lightcurve",)), "has_epoch_photometry"),
        (ObservationCriteria(product_types=("spectrum",)), "has_xp_sampled"),
        (ObservationCriteria(min_calibration_level=3), "has_xp_sampled"),
        (ObservationCriteria(missions=("gaia",)), "has_epoch_photometry, has_xp_sampled"),
    ],
)
def test_the_query_asks_only_for_the_wanted_products(criteria: ObservationCriteria, wanted: str) -> None:
    gateway = GaiaDataLinkGateway(Counting())
    query = gateway.search_query(STAR, _wanted(criteria), criteria.radius_arcsec)
    assert query.startswith(f"SELECT TOP 2000 source_id, {wanted} FROM gaiadr3.gaia_source WHERE ")
    assert query.endswith(" ORDER BY source_id")


@pytest.mark.parametrize(
    "criteria",
    [
        ObservationCriteria(missions=("TESS",)),
        ObservationCriteria(product_types=("image",)),
        ObservationCriteria(min_calibration_level=4),
        ObservationCriteria(epoch_min_mjd=57901.5),
        ObservationCriteria(epoch_max_mjd=56862.5),
    ],
)
def test_nothing_is_asked_when_the_criteria_exclude_every_gaia_product(criteria: ObservationCriteria) -> None:
    transport = Counting()
    index = GaiaDataLinkGateway(transport).search(STAR, criteria)
    assert index.records == () and index.query_text == "" and transport.calls == 0


def test_criteria_touching_the_dr3_span_still_ask() -> None:
    assert len(_wanted(ObservationCriteria(epoch_min_mjd=57901.0, epoch_max_mjd=56863.0))) == 2
    assert [p.retrieval_type for p in _wanted(ObservationCriteria(min_calibration_level=2))] == [
        "EPOCH_PHOTOMETRY",
        "XP_SAMPLED",
    ]


def test_a_product_is_fetched_as_served_and_read_from_its_datalink_url() -> None:
    _, fetched = raw("EPOCH_PHOTOMETRY")
    assert fetched.read_from == data_url(VARIABLE, "EPOCH_PHOTOMETRY")
    assert fetched.media_type == "application/x-votable+xml"
    assert (fetched.release_label, len(fetched.data)) == ("gaia:DR3", 12017)
    assert fetched.licence.startswith("ESA/Gaia/DPAC, CC BY-SA 3.0 IGO")


def test_a_foreign_or_malformed_product_is_refused_before_any_request() -> None:
    transport = Counting()
    gateway = GaiaDataLinkGateway(transport)
    with pytest.raises(ValueError, match=r"^x is a mast product, not a Gaia DataLink one$"):
        gateway.fetch(ProductRef("mast", "x", "https://example.invalid/x"))
    with pytest.raises(ValueError, match="is not a Gaia DataLink product id"):
        gateway.fetch(ProductRef("gaia-datalink", "x", "https://example.invalid/x"))
    with pytest.raises(LookupError, match=r"^Gaia DataLink serves no images to cut out$"):
        gateway.cutout(None, None)  # type: ignore[arg-type]
    assert transport.calls == 0


class Answer:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status, self.body = status, body

    def send(self, request: HttpRequest) -> HttpResponse:
        return HttpResponse(self.status, self.body, {})


def test_an_archive_error_or_a_non_votable_answer_raises() -> None:
    with pytest.raises(TransportError, match=r"^Gaia DataLink Gaia DR3 \d+/XP_SAMPLED: HTTP 503$"):
        GaiaDataLinkGateway(Answer(503)).fetch(ref("XP_SAMPLED"))
    with pytest.raises(UntrustedInput):
        GaiaDataLinkGateway(Answer(200, b"<html>busy</html>")).fetch(ref("XP_SAMPLED"))
    with pytest.raises(TransportError, match=rf"^Gaia DataLink links for {VARIABLE}: HTTP 500$"):
        GaiaDataLinkInventory(Answer(500)).rows(ref("XP_SAMPLED"))


def test_the_inventory_is_the_products_own_datalink_row_and_the_sources_position() -> None:
    rows = GaiaDataLinkInventory(replay()).rows(ref("EPOCH_PHOTOMETRY"))
    assert rows.inventory_row == {
        "productType": "SCIENCE",
        "retrieval_type": "EPOCH_PHOTOMETRY",
        "content_type": "application/x-votable+xml",
        "calib_level": 2,
        "dataRights": "PUBLIC",
    }
    assert rows.obscore_row["obs_id"] == f"Gaia DR3 {VARIABLE}" and rows.obscore_row["obs_collection"] == "GAIA"
    assert rows.obscore_row["s_ra"] == pytest.approx(56.534933945361814)
    assert rows.obscore_row["s_dec"] == pytest.approx(23.3473212122962)
    assert rows.obscore_row["ref_epoch"] == 2016.0
    assert len(rows.links) == 5 and {link["semantics"] for link in rows.links} == {"#this"}
    assert rows.rendered_query.startswith(
        f"GET {links_url(VARIABLE)}\nPOST https://gea.esac.esa.int/tap-server/tap/sync "
    )


def test_a_product_datalink_does_not_list_is_an_inventory_miss() -> None:
    with pytest.raises(LookupError, match=r"^Gaia DataLink lists no EPOCH_PHOTOMETRY for Gaia DR3 66526127137440128$"):
        GaiaDataLinkInventory(replay()).rows(ref("EPOCH_PHOTOMETRY", 66526127137440128))


def test_epoch_photometry_fills_the_same_envelope_a_jwst_product_does() -> None:
    meta, product = envelope("EPOCH_PHOTOMETRY")
    assert meta.product_id == f"gaia:DR3/product/{VARIABLE}/epoch_photometry"
    assert (meta.product_type, meta.mission, meta.instrument) == (ProductType.TIMESERIES, "GAIA", "Astro, BP, RP")
    assert (meta.filter_or_grating, meta.observation_id) == ("G, BP, RP", f"Gaia DR3 {VARIABLE}")
    assert (meta.frame, meta.time_scale, meta.calibration_level) == ("ICRS", "TCB", 2)
    assert meta.time_start_mjd == pytest.approx(1709.1022325861527 + GAIA_TIME_ORIGIN_MJD)
    assert meta.time_start_mjd is not None and meta.time_end_mjd is not None
    assert meta.time_end_mjd > meta.time_start_mjd + 900
    assert (meta.em_min_m, meta.em_max_m) == (330e-9, 1050e-9)
    assert meta.pipeline_version == "Gaia DR3 solution 375316653866487564"
    assert (meta.media_type, meta.dimensions, meta.units) == ("application/x-votable+xml", (28,), "electron / s")
    assert PayloadExtension("TABLE0:G_TRANSIT_FLUX_ERROR", "error", "electron / s") in meta.extensions
    assert PayloadExtension("TABLE0:G_TRANSIT_MAG", "data", "mag") in meta.extensions
    assert PayloadExtension("TABLE0:REJECTED_BY_PHOTOMETRY", "quality", None) in meta.extensions
    assert meta.has_uncertainty and meta.has_quality and not meta.has_wcs
    assert meta.access_status is AccessStatus.PUBLIC and meta.ra_deg == pytest.approx(56.534933945361814)
    assert meta.rights.citation.startswith("Gaia Collaboration, Vallenari et al. 2023")
    assert meta.checksum == product.raw_artifact_id and meta.size_bytes == 12017
    assert meta.source_id == "gaia-datalink" and meta.rendered_query == product.rendered_query
    assert meta.relationships == (), "every other DataLink row is the source's own #this product"


def test_a_spectrum_states_its_wavelength_range_and_level() -> None:
    meta, _ = envelope("XP_SAMPLED")
    assert (meta.product_type, meta.calibration_level, meta.filter_or_grating) == (ProductType.SPECTRUM, 3, "BP/RP")
    assert meta.em_min_m == pytest.approx(336e-9) and meta.em_max_m == pytest.approx(1020e-9)
    assert (meta.time_start_mjd, meta.time_end_mjd) == (56863.0, 57901.0)
    assert meta.extensions == (
        PayloadExtension("TABLE0:FLUX", "data", "W / (nm m2)"),
        PayloadExtension("TABLE0:FLUX_ERROR", "error", "W / (nm m2)"),
    )
    assert meta.units == "W / (nm m2)"


@pytest.mark.parametrize("retrieval_type", ["EPOCH_PHOTOMETRY", "XP_SAMPLED"])
def test_both_products_pass_the_validator_every_jwst_product_passes(retrieval_type: str) -> None:
    meta, product = envelope(retrieval_type)
    report = EnvelopeValidator().validate(meta, product)
    assert report.passed, [c for c in report.checks if not c.passed]
    fields = {c.field: c.detail for c in report.checks}
    assert fields["format"] == "VOTable with 1 table(s)"
    assert fields["time_scale"] == "TCB" and fields["has_wcs"] == "a table carries no WCS"


def test_an_envelope_that_omits_an_error_column_fails() -> None:
    meta, product = envelope("EPOCH_PHOTOMETRY")
    lossy = replace(meta, extensions=tuple(e for e in meta.extensions if e.name != "TABLE0:BP_FLUX_ERROR"))
    failed = {c.field: c.detail for c in EnvelopeValidator().validate(lossy, product).checks if not c.passed}
    assert failed == {"extensions.TABLE0:BP_FLUX_ERROR": "error column in the file, missing from the envelope"}


def test_an_envelope_naming_an_absent_column_or_a_wcs_fails() -> None:
    meta, product = envelope("XP_SAMPLED")
    wrong = replace(meta, extensions=(*meta.extensions, PayloadExtension("TABLE0:DQ", "quality")), has_wcs=True)
    failed = {c.field: c.detail for c in EnvelopeValidator().validate(wrong, product).checks if not c.passed}
    assert failed == {
        "extensions.TABLE0:DQ": "named in the envelope but absent from the file",
        "has_wcs": "envelope claims a WCS",
    }


def test_bytes_that_are_not_the_votable_the_envelope_claims_fail_format() -> None:
    meta, product = envelope("XP_SAMPLED")
    broken = replace(product, fetched=replace(product.fetched, data=b"<?xml version='1.0'?><VOTABLE><RESOURCE/>"))
    report = EnvelopeValidator().validate(meta, broken)
    assert {c.field for c in report.checks if not c.passed} == {"checksum", "size_bytes", "format"}


def test_epoch_photometry_curates_to_the_canonical_g_band_light_curve() -> None:
    _, fetched = raw("EPOCH_PHOTOMETRY")
    curated = GaiaDataLinkDialect().curate("lightcurve", fetched)
    assert curated is not None and curated.media_type == "text/csv"
    curve = parse_lightcurve(curated.data)
    assert len(curve.mjd) == 27, "one of 28 transits is rejected"
    assert np.all(np.diff(curve.mjd) > 0)
    assert curve.mjd[0] == pytest.approx(1709.1022325861527 + 55197.0, abs=1e-9)
    assert curve.mag[0] == pytest.approx(13.19862916, abs=1e-8)
    assert curve.mag_err.min() > 0 and curve.mag_err.max() < 0.01
    assert pytest.approx(1.0857362047581294) == MAG_PER_RELATIVE_FLUX


def test_only_epoch_photometry_is_curated() -> None:
    _, fetched = raw("EPOCH_PHOTOMETRY")
    assert len(parse_lightcurve(epoch_lightcurve(fetched.data)).mjd) == 27
    assert GaiaDataLinkDialect().curate("spectrum", fetched) is None
    assert GaiaDataLinkDialect().curate("image", fetched) is None


def test_the_curated_light_curve_keeps_the_uncertainty_or_is_refused() -> None:
    meta, product = envelope("EPOCH_PHOTOMETRY")
    curated = GaiaDataLinkDialect().curate("lightcurve", product.fetched)
    assert curated is not None
    validator = EnvelopeValidator()
    assert validator.validate_curation(meta, product, curated).passed
    lossy = CuratedProduct(b"mjd,mag\n57000.0,13.2\n", "text/csv")
    failed = {c.field: c.detail for c in validator.validate_curation(meta, product, lossy).checks if not c.passed}
    assert failed == {"uncertainty": "the raw product's uncertainty is lost"}


def test_a_spectrum_is_never_curated_to_a_table() -> None:
    meta, product = envelope("XP_SAMPLED")
    report = EnvelopeValidator().validate_curation(meta, product, CuratedProduct(b"wl,flux,flux_err\n", "text/csv"))
    assert not report.passed and {c.field for c in report.checks if not c.passed} == {"format"}


def test_a_votable_product_is_read_into_the_shape_a_fits_file_is() -> None:
    _, fetched = raw("XP_SAMPLED")
    (table,) = read_tables(fetched.data)
    assert (table.index, table.name, table.kind, table.shape) == (0, "TABLE0", "table", (343,))
    assert table.columns == ("WAVELENGTH", "FLUX", "FLUX_ERROR")
    assert table.column_units == {"WAVELENGTH": "nm", "FLUX": "W / (nm m2)", "FLUX_ERROR": "W / (nm m2)"}
    assert table.column_ucds == {"WAVELENGTH": "em.wl", "FLUX": "spect", "FLUX_ERROR": "stat.error;spect"}
    assert table.header["TIMESYS"] == "TCB" and table.header["TIMEORIGIN"] == 2455197.5
    assert table.header["TIMEREFPOS"] == "BARYCENTER" and table.header["source_id"] == VARIABLE
    assert table.header["release"] == "Gaia DR3"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"<html></html>", r"^not an XML VOTable document$"),
        (
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><VOTABLE/>',
            r"^a VOTable declares no DTD; refusing one that does$",
        ),
        (
            b'<?xml version="1.0"?><VOTABLE version="1.4"><RESOURCE/></VOTABLE>',
            "^not a readable VOTable: it holds no table$",
        ),
        (b'<?xml version="1.0"?><VOTABLE><RESOURCE><TABLE>', r"^not a readable VOTable: "),
    ],
)
def test_bytes_that_are_not_a_plain_votable_are_refused(data: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        read_tables(data)


def test_a_votable_product_over_the_size_limit_is_refused_unread() -> None:
    from sdp.adapters.products.votable import MAX_PRODUCT_BYTES

    check_votable(b"<?xml" + b" " * (MAX_PRODUCT_BYTES - 5))
    with pytest.raises(ValueError, match=rf"^a VOTable product of {MAX_PRODUCT_BYTES + 1} bytes exceeds the "):
        check_votable(b"<?xml" + b" " * (MAX_PRODUCT_BYTES - 4))
    check_votable(b"\xef\xbb\xbf \t\r\n<VOTABLE/>")


# What follows pins the details the tests above leave loose: the requests sent, the messages and
# statuses of failures, and the edge cases of the VOTable reader and the curation.


def votable_bytes(table: Table, *, name: str | None = None, table_id: str | None = None) -> bytes:
    document = from_table(table)
    vo_table = document.get_first_table()
    vo_table.name, vo_table.ID = name, table_id
    buffer = io.BytesIO()
    document.to_xml(buffer)
    return buffer.getvalue()


class Recording:
    """Replays the cassettes and remembers each request."""

    def __init__(self) -> None:
        self.inner, self.requests = replay(), list[HttpRequest]()

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.inner.send(request)


class Routing:
    """Replays DataLink requests and answers every TAP request with one fixed response."""

    def __init__(self, tap: HttpResponse) -> None:
        self.inner, self.tap = replay(), tap

    def send(self, request: HttpRequest) -> HttpResponse:
        return self.tap if "/tap-server/" in request.url else self.inner.send(request)


def test_each_request_is_the_one_the_archive_documents() -> None:
    transport = Recording()
    GaiaDataLinkGateway(transport).search(STAR, ObservationCriteria(radius_arcsec=1.0))
    GaiaDataLinkGateway(transport).fetch(ref("EPOCH_PHOTOMETRY"))
    GaiaDataLinkInventory(transport).rows(ref("EPOCH_PHOTOMETRY"))
    assert [(r.method, r.url) for r in transport.requests] == [
        ("POST", "https://gea.esac.esa.int/tap-server/tap/sync"),
        ("GET", data_url(VARIABLE, "EPOCH_PHOTOMETRY")),
        ("GET", links_url(VARIABLE)),
        ("POST", "https://gea.esac.esa.int/tap-server/tap/sync"),
    ]
    search, *_, position = transport.requests
    assert search.body is not None and b"MAXREC=2000" in search.body
    assert position.body is not None and b"MAXREC=1&" in position.body
    assert b"WHERE+source_id+%3D+64971933089093760" in position.body


def test_failures_carry_the_http_status() -> None:
    with pytest.raises(TransportError) as fetch:
        GaiaDataLinkGateway(Answer(503)).fetch(ref("XP_SAMPLED"))
    with pytest.raises(TransportError) as links:
        GaiaDataLinkInventory(Answer(500)).rows(ref("XP_SAMPLED"))
    assert (fetch.value.status, links.value.status) == (503, 500)
    with pytest.raises(UntrustedInput):
        GaiaDataLinkInventory(Answer(200, b"<html>busy</html>")).rows(ref("XP_SAMPLED"))


def test_a_failed_tap_query_names_gaia_tap() -> None:
    with pytest.raises(TransportError, match=r"^Gaia TAP: HTTP 500$"):
        GaiaDataLinkGateway(Answer(500)).search(STAR, ObservationCriteria(radius_arcsec=1.0))
    with pytest.raises(TransportError, match=r"^Gaia TAP: HTTP 502$"):
        GaiaDataLinkInventory(Routing(HttpResponse(502, b"", {}))).rows(ref("EPOCH_PHOTOMETRY"))


def test_a_source_gaia_source_does_not_hold_is_an_inventory_miss() -> None:
    empty = Table(names=("source_id", "ra", "dec", "ref_epoch"), dtype=("i8", "f8", "f8", "f8"))
    transport = Routing(HttpResponse(200, votable_bytes(empty), {}))
    with pytest.raises(LookupError, match=rf"^gaia_source has no row for Gaia DR3 {VARIABLE}$"):
        GaiaDataLinkInventory(transport).rows(ref("EPOCH_PHOTOMETRY"))


def test_a_datalink_row_is_kept_as_text_without_its_length() -> None:
    assert _link({"access_url": "u", "error_message": None, "content_length": 12017, "n": 3}) == {
        "access_url": "u",
        "error_message": None,
        "n": "3",
    }
    rows = GaiaDataLinkInventory(replay()).rows(ref("EPOCH_PHOTOMETRY"))
    assert all("content_length" not in link for link in rows.links)


def test_records_and_products_carry_gaias_licence_and_citation() -> None:
    index = GaiaDataLinkGateway(replay()).search(STAR, ObservationCriteria(radius_arcsec=1.0))
    assert (index.licence, index.citation) == (LICENCE, CITATION)
    assert {(r.licence, r.citation, r.product.archive) for r in index.records} == {(LICENCE, CITATION, "gaia-datalink")}
    _, fetched = raw("XP_SAMPLED")
    assert (fetched.licence, fetched.citation) == (LICENCE, CITATION)
    meta, _ = envelope("XP_SAMPLED")
    assert meta.dec_deg == pytest.approx(23.3473212122962)
    assert meta.rights == Rights(LICENCE, CITATION, ACKNOWLEDGEMENT)
    assert meta.spatial_resolution_arcsec is None and meta.s_region is None


def photometry(**columns: list[Any]) -> bytes:
    base: dict[str, list[Any]] = {
        "g_transit_time": [1702.0, 1700.0, 1701.0],
        "g_transit_flux": [1000.0, 1000.0, 1000.0],
        "g_transit_flux_error": [10.0, 20.0, 30.0],
        "g_transit_mag": [13.0, 14.0, 15.0],
    }
    base.update(columns)
    return votable_bytes(Table(base))


def test_an_envelope_says_what_the_product_does_not() -> None:
    product, _ = raw("EPOCH_PHOTOMETRY")
    bare = photometry(g_transit_time=[np.nan, np.nan, np.nan])
    product = replace(
        product,
        fetched=replace(product.fetched, data=bare),
        obscore_row={**product.obscore_row, "s_ra": float("nan"), "s_dec": "unknown"},
    )
    meta = GaiaDataLinkDialect().normalize(product)
    assert (meta.pipeline_version, meta.time_scale) == ("Gaia DR3", "UNKNOWN")
    assert (meta.time_start_mjd, meta.time_end_mjd) == (None, None)
    assert (meta.ra_deg, meta.dec_deg, meta.units) == (None, None, None)
    assert meta.dimensions == (3,)


@pytest.mark.parametrize(
    ("columns", "kept"),
    [
        ({}, [14.0, 13.0, 15.0]),  # sorted by time, not served order
        ({"g_transit_flux": [1000.0, -1.0, 0.0]}, [13.0]),
        ({"g_transit_flux_error": [10.0, 0.0, -1.0]}, [13.0]),
        ({"g_transit_mag": [13.0, np.nan, 15.0]}, [13.0, 15.0]),
        ({"g_transit_time": [1702.0, np.nan, 1701.0]}, [15.0, 13.0]),
        ({"rejected_by_photometry": [False, True, False]}, [15.0, 13.0]),
        ({"variability_flag_g_reject": [True, False, False]}, [14.0, 15.0]),
    ],
)
def test_epoch_photometry_curation_keeps_only_good_transits(columns: dict[str, list[Any]], kept: list[float]) -> None:
    curve = parse_lightcurve(epoch_lightcurve(photometry(**columns)))
    assert list(curve.mag) == sorted(kept, key=lambda m: {13.0: 1702.0, 14.0: 1700.0, 15.0: 1701.0}[m])


def test_a_masked_flag_rejects_its_transit() -> None:
    table = Table(
        {
            "g_transit_time": [1700.0, 1701.0],
            "g_transit_flux": [1000.0, 1000.0],
            "g_transit_flux_error": [10.0, 10.0],
            "g_transit_mag": [13.0, 14.0],
            "rejected_by_photometry": MaskedColumn([False, False], mask=[False, True]),
        }
    )
    curve = parse_lightcurve(epoch_lightcurve(votable_bytes(table)))
    assert list(curve.mag) == [13.0]


def test_the_curated_error_is_the_flux_error_in_magnitudes() -> None:
    curve = parse_lightcurve(epoch_lightcurve(photometry()))
    assert list(curve.mjd) == [1700.0 + 55197.0, 1701.0 + 55197.0, 1702.0 + 55197.0]
    assert curve.mag_err == pytest.approx(
        [0.02 * MAG_PER_RELATIVE_FLUX, 0.03 * MAG_PER_RELATIVE_FLUX, 0.01 * MAG_PER_RELATIVE_FLUX]
    )


def test_a_column_role_comes_from_its_ucd_or_the_dialects_quality_list() -> None:
    table = HduInfo(
        0,
        "T",
        "table",
        {},
        (1,),
        ("TIME", "MAG", "ERR", "QUAL", "FLAGS", "NOTE"),
        {"MAG": "mag", "ERR": "mag"},
        {"TIME": "time.epoch", "MAG": "phot.mag;em.opt", "ERR": "stat.error;phot.mag", "QUAL": "meta.code.qual"},
    )
    assert column_roles(table) == (
        PayloadExtension("T:MAG", "data", "mag"),
        PayloadExtension("T:ERR", "error", "mag"),
        PayloadExtension("T:QUAL", "quality", None),
    )
    assert column_roles(table, quality=("FLAGS", "TIME"))[0] == PayloadExtension("T:TIME", "quality", None)
    assert PayloadExtension("T:FLAGS", "quality", None) in column_roles(table, quality=("FLAGS",))


def test_a_masked_number_reads_as_nan() -> None:
    table = Table({"x": MaskedColumn([1, 2, 3], mask=[False, True, False])})
    values = numbers(table, "x")
    assert values.dtype == np.float64 and not np.ma.isMaskedArray(values)
    assert values[0] == 1.0 and np.isnan(values[1]) and values[2] == 3.0


def test_xml_must_come_first() -> None:
    with pytest.raises(ValueError, match=r"^not an XML VOTable document$"):
        check_votable(b"X<?xml version='1.0'?><VOTABLE/>")
    check_votable(b"<VOTABLE/>")


def test_a_table_is_named_by_its_name_then_its_id_then_its_place() -> None:
    simple = Table({"x": [1.0]})
    assert read_tables(votable_bytes(simple, name="lc"))[0].name == "LC"
    assert read_tables(votable_bytes(simple, table_id="curve"))[0].name == "CURVE"
    assert read_tables(votable_bytes(simple))[0].name == "TABLE0"


def test_links_and_params_read_as_plain_python_without_warnings() -> None:
    _, fetched = raw("EPOCH_PHOTOMETRY")
    links = replay().send(HttpRequest("GET", links_url(VARIABLE))).body
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        (table,) = read_tables(fetched.data)
        (listing,) = read_tables(links)
    assert type(table.header["source_id"]) is int and type(table.header["TIMEORIGIN"]) is float
    assert not {"TIMESYS", "TIMEREFPOS", "TIMEORIGIN"} & set(listing.header)
    assert "ACCESS_URL" in listing.columns and listing.shape == (5,)
