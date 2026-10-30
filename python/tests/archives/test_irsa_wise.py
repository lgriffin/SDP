"""T3 and T5 on real IRSA answers: WISE and NEOWISE light curves are found, fetched, enveloped,
validated and curated through the same product machinery as Gaia's and JWST's, and their exposures
are reconciled with Gaia by both association methods (INT-023).

The cassettes were recorded live from IRSA's TAP service on 2026-10-29 around the Pleiades variable
Gaia DR3 64971933089093760, AllWISE J034608.37+232050.6: a 2-arcsec and a 60-arcsec cone, both light
curves of the source, and its AllWISE row.
"""

from __future__ import annotations

import hashlib
import io
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.io.votable import from_table
from astropy.table import MaskedColumn, Table

from sdp.adapters.archives.irsa_wise import (
    IrsaWiseGateway,
    IrsaWiseInventory,
    _wanted,
    at_allwise_epoch,
    lightcurve_cone,
)
from sdp.adapters.archives.transport import CassetteTransport, HttpRequest, HttpResponse, TransportError
from sdp.adapters.archives.untrusted import UntrustedInput
from sdp.adapters.archives.votable import TapQueryError
from sdp.adapters.association import BayesFactorAssociation, LikelihoodRatioAssociation
from sdp.adapters.products import EnvelopeValidator, WiseLightCurveDialect
from sdp.adapters.products.wise import clean, w1_detections, w1_lightcurve
from sdp.adapters.wise import (
    ACKNOWLEDGEMENT,
    CITATION,
    LICENCE,
    WISE_TABLES,
    lightcurve_query,
    lightcurve_url,
    parse_product_id,
    product_id,
)
from sdp.kernel.domain.association import AssociationPolicy, CatalogueSource, Motion, PositionCovariance
from sdp.kernel.domain.integration import AccessStatus, PayloadExtension, ProductMetadata, ProductType, Rights
from sdp.kernel.domain.investigation import ObservationCriteria, ProductRef
from sdp.kernel.ports.integration import RawProduct
from sdp.kernel.ports.investigate import ObservationTarget
from sdp.kernel.science.products import parse_lightcurve

pytestmark = [pytest.mark.tier("T5"), pytest.mark.req("INT-023")]

CASSETTES = Path(__file__).resolve().parents[1] / "cassettes" / "irsa-wise"
DESIGNATION = "J034608.37+232050.6"
SOURCE = (56.5349135, 23.3473901)
#: Gaia DR3 64971933089093760 as recorded: its position at 2016.0 and its proper motion (mas/yr).
STAR = ObservationTarget("star", 56.534933945361814, 23.3473212122962, 19.0, -45.0, 2016.0)
FIELD = ObservationTarget("field", 56.5349, 23.3473)


def replay() -> CassetteTransport:
    return CassetteTransport(CASSETTES, "replay")


def ref(key: str) -> ProductRef:
    return ProductRef("irsa-wise", product_id(DESIGNATION, *SOURCE, key), lightcurve_url(WISE_TABLES[key], *SOURCE))


def raw(key: str) -> RawProduct:
    transport = replay()
    fetched = IrsaWiseGateway(transport).fetch(ref(key))
    rows = IrsaWiseInventory(transport).rows(fetched.product)
    digest = "sha256:" + hashlib.sha256(fetched.data).hexdigest()
    return RawProduct(fetched, digest, rows.rendered_query, rows.inventory_row, rows.obscore_row, rows.links)


def envelope(key: str) -> tuple[ProductMetadata, RawProduct]:
    product = raw(key)
    return WiseLightCurveDialect().normalize(product), product


class Counting:
    """A transport that must not be called."""

    calls = 0

    def send(self, request: HttpRequest) -> HttpResponse:
        self.calls += 1
        raise AssertionError(f"unexpected request {request.url}")


class Answer:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status, self.body = status, body

    def send(self, request: HttpRequest) -> HttpResponse:
        return HttpResponse(self.status, self.body, {})


class Recording:
    def __init__(self) -> None:
        self.inner, self.requests = replay(), list[HttpRequest]()

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.inner.send(request)


def votable_bytes(table: Table) -> bytes:
    buffer = io.BytesIO()
    from_table(table).to_xml(buffer)
    return buffer.getvalue()


def test_product_ids_name_the_allwise_source_its_position_and_the_table() -> None:
    pid = product_id(DESIGNATION, *SOURCE, "NEOWISE")
    assert pid == f"WISE {DESIGNATION} 56.5349135 +23.3473901/NEOWISE"
    assert parse_product_id(pid) == (DESIGNATION, *SOURCE, WISE_TABLES["NEOWISE"])
    assert parse_product_id(product_id(DESIGNATION, 1.5, -2.25, "ALLWISE"))[1:3] == (1.5, -2.25)
    for bad in (f"WISE {DESIGNATION} 1.0 2.0/2MASS", "WISE J1+2 1.0 2.0/NEOWISE", "x"):
        with pytest.raises(ValueError, match=r"^'.*' is not a WISE light-curve product id$"):
            parse_product_id(bad)


def test_a_light_curve_url_is_the_cone_query_that_reproduces_it() -> None:
    query = lightcurve_query(WISE_TABLES["NEOWISE"], *SOURCE)
    assert query == (
        "SELECT ra, dec, sigra, sigdec, mjd, w1mpro, w1sigmpro, w2mpro, w2sigmpro, qual_frame, qi_fact, saa_sep, "
        "moon_masked, nb, na, cc_flags FROM neowiser_p1bs_psd WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), "
        "CIRCLE('ICRS', 56.5349135, +23.3473901, 0.0006944444444444445)) ORDER BY mjd"
    )
    assert lightcurve_url(WISE_TABLES["ALLWISE"], *SOURCE).startswith(
        "https://irsa.ipac.caltech.edu/TAP/sync?LANG=ADQL&FORMAT=votable&QUERY=SELECT+ra%2C+dec%2C+mjd%2C+w1mpro_ep"
    )


def test_a_cone_offers_both_light_curves_of_every_allwise_source() -> None:
    index = IrsaWiseGateway(replay()).search(STAR, ObservationCriteria(radius_arcsec=2.0))
    assert (index.archive, index.release_label, index.target) == ("irsa-wise", "irsa:allwise-neowise", "star")
    assert (index.licence, index.citation) == (LICENCE, CITATION)
    assert [r.product for r in index.records] == [ref("NEOWISE"), ref("ALLWISE")]
    neowise, allwise = index.records
    assert (neowise.mission, neowise.instrument, neowise.product_type) == ("NEOWISE", "WISE", "lightcurve")
    assert (neowise.epoch_mjd, neowise.epoch_end_mjd, neowise.size_bytes) == (56639.0, 60523.0, None)
    assert (allwise.mission, allwise.epoch_mjd, allwise.epoch_end_mjd) == ("WISE", 55203.0, 55593.0)
    assert {(r.licence, r.citation) for r in index.records} == {(LICENCE, CITATION)}
    assert index.query_text.startswith("SELECT TOP 2000 designation, ra, dec FROM allwise_p3as_psd WHERE ")
    assert index.query_text.endswith(" ORDER BY designation")
    field = IrsaWiseGateway(replay()).search(FIELD, ObservationCriteria(radius_arcsec=60.0))
    assert len(field.records) == 16 and len({r.product.product_id.split("/")[0] for r in field.records}) == 8


def test_a_moving_star_is_looked_for_where_it_was_at_allwise_epoch() -> None:
    ra, dec = at_allwise_epoch(STAR)
    years = 2010.5589 - 2016.0
    assert dec == pytest.approx(STAR.dec_deg - 45.0 * years / 3.6e6, abs=1e-12)
    assert ra == pytest.approx(STAR.ra_deg + 19.0 * years / 3.6e6 / np.cos(np.radians(STAR.dec_deg)), abs=1e-12)
    assert at_allwise_epoch(FIELD) == (FIELD.ra_deg, FIELD.dec_deg)
    wrapped = at_allwise_epoch(ObservationTarget("w", 359.9999999, 0.0, 1000.0, 0.0, 2000.0))
    assert 0.0 <= wrapped[0] < 0.01
    query = IrsaWiseGateway(Counting()).search_query(*at_allwise_epoch(STAR), 2.0)
    assert f"CIRCLE('ICRS', {ra!r}, {dec!r}, {2.0 / 3600.0!r})" in query


@pytest.mark.parametrize(
    ("criteria", "keys"),
    [
        (ObservationCriteria(), ["NEOWISE", "ALLWISE"]),
        (ObservationCriteria(missions=("neowise",)), ["NEOWISE"]),
        (ObservationCriteria(missions=("WISE",)), ["NEOWISE", "ALLWISE"]),
        (ObservationCriteria(epoch_min_mjd=56000.0), ["NEOWISE"]),
        (ObservationCriteria(epoch_max_mjd=56000.0), ["ALLWISE"]),
        (ObservationCriteria(epoch_min_mjd=55593.0, epoch_max_mjd=56639.0), ["NEOWISE", "ALLWISE"]),
        (ObservationCriteria(min_calibration_level=2, product_types=("lightcurve",)), ["NEOWISE", "ALLWISE"]),
    ],
)
def test_the_criteria_choose_the_tables(criteria: ObservationCriteria, keys: list[str]) -> None:
    assert [t.key for t in _wanted(criteria)] == keys


@pytest.mark.parametrize(
    "criteria",
    [
        ObservationCriteria(missions=("GAIA",)),
        ObservationCriteria(product_types=("image",)),
        ObservationCriteria(min_calibration_level=3),
        ObservationCriteria(epoch_min_mjd=60523.5),
        ObservationCriteria(epoch_max_mjd=55202.5),
    ],
)
def test_nothing_is_asked_when_the_criteria_exclude_every_wise_table(criteria: ObservationCriteria) -> None:
    transport = Counting()
    index = IrsaWiseGateway(transport).search(STAR, criteria)
    assert index.records == () and index.query_text == "" and transport.calls == 0


def test_each_request_is_the_one_irsa_documents() -> None:
    transport = Recording()
    IrsaWiseGateway(transport).search(STAR, ObservationCriteria(radius_arcsec=2.0))
    IrsaWiseGateway(transport).fetch(ref("NEOWISE"))
    IrsaWiseInventory(transport).rows(ref("NEOWISE"))
    assert [(r.method, r.url) for r in transport.requests] == [
        ("POST", "https://irsa.ipac.caltech.edu/TAP/sync"),
        ("GET", lightcurve_url(WISE_TABLES["NEOWISE"], *SOURCE)),
        ("POST", "https://irsa.ipac.caltech.edu/TAP/sync"),
    ]
    search, _, source = transport.requests
    assert search.body is not None and b"MAXREC=2000" in search.body
    assert source.body is not None and b"MAXREC=1&" in source.body


def test_a_light_curve_is_fetched_as_served() -> None:
    fetched = IrsaWiseGateway(replay()).fetch(ref("NEOWISE"))
    assert (fetched.media_type, fetched.release_label, fetched.read_from) == (
        "application/x-votable+xml",
        "irsa:allwise-neowise",
        ref("NEOWISE").uri,
    )
    assert (fetched.licence, fetched.citation, len(fetched.data)) == (LICENCE, CITATION, 129290)


def test_a_foreign_or_malformed_product_is_refused_before_any_request() -> None:
    transport = Counting()
    gateway = IrsaWiseGateway(transport)
    with pytest.raises(ValueError, match=r"^x is a mast product, not an IRSA WISE one$"):
        gateway.fetch(ProductRef("mast", "x", "https://example.invalid/x"))
    with pytest.raises(ValueError, match="is not a WISE light-curve product id"):
        gateway.fetch(ProductRef("irsa-wise", "x", "https://example.invalid/x"))
    with pytest.raises(LookupError, match=r"^IRSA WISE light curves are tables, not images to cut out$"):
        gateway.cutout(None, None)  # type: ignore[arg-type]
    assert transport.calls == 0


def test_an_archive_error_or_a_non_votable_answer_raises() -> None:
    with pytest.raises(TransportError, match=rf"^IRSA WISE {re.escape(DESIGNATION)} .*/NEOWISE: HTTP 503$") as failed:
        IrsaWiseGateway(Answer(503)).fetch(ref("NEOWISE"))
    assert failed.value.status == 503
    with pytest.raises(UntrustedInput):
        IrsaWiseGateway(Answer(200, b"<html>busy</html>")).fetch(ref("NEOWISE"))
    error = b'<?xml version="1.0"?><VOTABLE><RESOURCE type="results"><INFO name="QUERY_STATUS" value="ERROR">bad</INFO></RESOURCE></VOTABLE>'
    with pytest.raises(TapQueryError, match=r"^bad$"):
        IrsaWiseGateway(Answer(200, error)).fetch(ref("NEOWISE"))
    with pytest.raises(TransportError, match=r"^IRSA TAP: HTTP 500$"):
        IrsaWiseGateway(Answer(500)).search(STAR, ObservationCriteria(radius_arcsec=2.0))
    with pytest.raises(TransportError, match=r"^IRSA TAP: HTTP 502$"):
        IrsaWiseInventory(Answer(502)).rows(ref("NEOWISE"))


def test_the_inventory_is_the_allwise_source_row() -> None:
    rows = IrsaWiseInventory(replay()).rows(ref("NEOWISE"))
    assert rows.inventory_row == {
        "productType": "SCIENCE",
        "table": "neowiser_p1bs_psd",
        "calib_level": 2,
        "dataRights": "PUBLIC",
    }
    assert rows.obscore_row == {
        "obs_id": f"WISE {DESIGNATION}",
        "obs_collection": "NEOWISE",
        "s_ra": pytest.approx(SOURCE[0]),
        "s_dec": pytest.approx(SOURCE[1]),
        "ph_qual": "AAAU",
        "ext_flg": 0,
    }
    assert rows.links == ()
    assert rows.rendered_query.startswith("POST https://irsa.ipac.caltech.edu/TAP/sync SELECT designation, ra, dec")
    assert "CIRCLE('ICRS', 56.5349135, +23.3473901, 0.0002777777777777778)" in rows.rendered_query
    assert rows.rendered_query.endswith(f"AND designation = '{DESIGNATION}'")


def test_a_source_allwise_does_not_hold_is_an_inventory_miss() -> None:
    empty = Table(
        names=("designation", "ra", "dec", "w1mpro", "w2mpro", "ph_qual", "ext_flg"),
        dtype=("U20", "f8", "f8", "f4", "f4", "U4", "i2"),
    )
    with pytest.raises(LookupError, match=rf"^AllWISE has no source {re.escape(DESIGNATION)}$"):
        IrsaWiseInventory(Answer(200, votable_bytes(empty))).rows(ref("NEOWISE"))


def test_a_neowise_light_curve_fills_the_same_envelope_as_every_other_product() -> None:
    meta, product = envelope("NEOWISE")
    assert meta.product_id == f"irsa:WISE/product/{DESIGNATION}/neowise"
    assert (meta.product_type, meta.mission, meta.instrument) == (ProductType.TIMESERIES, "NEOWISE", "WISE")
    assert (meta.filter_or_grating, meta.observation_id) == ("W1, W2", f"WISE {DESIGNATION}")
    assert (meta.frame, meta.time_scale, meta.calibration_level) == ("ICRS", "UTC", 2)
    assert (meta.time_start_mjd, meta.time_end_mjd) == pytest.approx((56704.19245361, 60338.46936532))
    assert (meta.em_min_m, meta.em_max_m, meta.spatial_resolution_arcsec) == (2.8e-6, 5.3e-6, 6.1)
    assert (meta.pipeline_version, meta.media_type, meta.units) == (
        "neowiser_p1bs_psd",
        "application/x-votable+xml",
        "mag",
    )
    assert meta.dimensions == (267,)
    assert meta.extensions[:2] == (
        PayloadExtension("TABLE0:W1MPRO", "data", "mag"),
        PayloadExtension("TABLE0:W1SIGMPRO", "error", "mag"),
    )
    assert PayloadExtension("TABLE0:SAA_SEP", "quality", "deg") in meta.extensions
    assert PayloadExtension("TABLE0:CC_FLAGS", "quality", None) in meta.extensions
    assert meta.has_uncertainty and meta.has_quality and not meta.has_wcs
    assert meta.access_status is AccessStatus.PUBLIC
    assert (meta.ra_deg, meta.dec_deg) == pytest.approx(SOURCE)
    assert meta.rights == Rights(LICENCE, CITATION, ACKNOWLEDGEMENT)
    assert meta.checksum == product.raw_artifact_id and meta.size_bytes == 129290
    assert meta.source_id == "irsa-wise" and meta.rendered_query == product.rendered_query
    assert meta.relationships == () and meta.s_region is None and meta.detector is None


def test_an_allwise_light_curve_states_its_own_table_and_years() -> None:
    meta, _ = envelope("ALLWISE")
    assert (meta.mission, meta.pipeline_version, meta.dimensions) == ("WISE", "allwise_p3as_mep", (22,))
    assert meta.time_start_mjd is not None and meta.time_end_mjd is not None
    assert 55203.0 < meta.time_start_mjd < meta.time_end_mjd < 55593.0
    assert PayloadExtension("TABLE0:W1MPRO_EP", "data", "mag") in meta.extensions
    assert "TABLE0:QUAL_FRAME" not in {e.name for e in meta.extensions}


@pytest.mark.parametrize("key", ["NEOWISE", "ALLWISE"])
def test_both_light_curves_pass_the_validator_and_keep_their_uncertainty(key: str) -> None:
    meta, product = envelope(key)
    validator = EnvelopeValidator()
    report = validator.validate(meta, product)
    assert report.passed, [c for c in report.checks if not c.passed]
    assert {c.field: c.detail for c in report.checks}["format"] == "VOTable with 1 table(s)"
    curated = WiseLightCurveDialect().curate("lightcurve", product.fetched)
    assert curated is not None and curated.media_type == "text/csv"
    assert validator.validate_curation(meta, product, curated).passed


def test_an_envelope_that_drops_a_quality_column_fails() -> None:
    meta, product = envelope("NEOWISE")
    lossy = replace(meta, extensions=tuple(e for e in meta.extensions if e.name != "TABLE0:CC_FLAGS"))
    failed = {c.field: c.detail for c in EnvelopeValidator().validate(lossy, product).checks if not c.passed}
    assert failed == {"extensions.TABLE0:CC_FLAGS": "quality column in the file, missing from the envelope"}


def test_the_curated_light_curve_is_the_clean_w1_exposures_in_time_order() -> None:
    product = raw("NEOWISE").fetched
    curve = parse_lightcurve(w1_lightcurve(product.data, WISE_TABLES["NEOWISE"]))
    assert len(curve.mjd) == 219, "48 of 267 exposures fail the cuts"
    assert np.all(np.diff(curve.mjd) >= 0) and curve.mjd[0] == pytest.approx(56704.19245361)
    assert curve.mag[0] == pytest.approx(10.362, abs=1e-6)
    assert 10.2 < curve.mag.min() < curve.mag.max() < 10.5 and np.median(curve.mag_err) < 0.02
    assert WiseLightCurveDialect().curate("image", product) is None
    allwise = parse_lightcurve(w1_lightcurve(raw("ALLWISE").fetched.data, WISE_TABLES["ALLWISE"]))
    assert len(allwise.mjd) == 22, "a lower-case halo flag marks a real source and is kept"


def exposures(**columns: list[Any]) -> Table:
    base: dict[str, Any] = {
        "ra": [10.0] * 3,
        "dec": [20.0] * 3,
        "sigra": [0.05, 0.05, 0.05],
        "sigdec": [0.06, 0.06, 0.06],
        "mjd": [58002.0, 58000.0, 58001.0],
        "w1mpro": [10.0, 11.0, 12.0],
        "w1sigmpro": [0.02, 0.03, 0.04],
        "w2mpro": [9.0, 9.0, 9.0],
        "w2sigmpro": [0.02, 0.02, 0.02],
        "qual_frame": [10, 10, 10],
        "qi_fact": [1.0, 1.0, 1.0],
        "saa_sep": [50.0, 50.0, 50.0],
        "moon_masked": ["00", "00", "00"],
        "nb": [1, 1, 1],
        "na": [0, 0, 0],
        "cc_flags": ["0000", "0000", "0000"],
    }
    base.update(columns)
    return Table(base)


@pytest.mark.parametrize(
    ("columns", "kept"),
    [
        ({}, [11.0, 12.0, 10.0]),
        ({"qual_frame": [0, 10, 10]}, [11.0, 12.0]),
        ({"qi_fact": [1.0, 0.5, 1.0]}, [12.0, 10.0]),
        ({"saa_sep": [5.0, 5.1, 50.0]}, [11.0, 12.0]),
        ({"moon_masked": ["10", "01", "00"]}, [11.0, 12.0]),
        ({"nb": [2, 1, 1]}, [11.0, 12.0]),
        ({"na": [0, 1, 0]}, [12.0, 10.0]),
        ({"cc_flags": ["D000", "h000", "0H00"]}, [11.0, 12.0]),
        ({"cc_flags": ["P000", "O000", "H000"]}, []),
        ({"w1sigmpro": [0.02, 0.0, np.nan]}, [10.0]),
        ({"w1mpro": [np.nan, 11.0, 12.0], "mjd": [58002.0, np.nan, 58001.0]}, [12.0]),
    ],
)
def test_curation_keeps_only_clean_exposures(columns: dict[str, list[Any]], kept: list[float]) -> None:
    data = votable_bytes(exposures(**columns))
    assert list(parse_lightcurve(w1_lightcurve(data, WISE_TABLES["NEOWISE"])).mag) == kept


def test_a_masked_flag_drops_its_exposure_and_allwise_has_no_frame_score() -> None:
    table = exposures(moon_masked=MaskedColumn(["00", "00", "00"], mask=[False, True, False]))
    assert list(clean(table, WISE_TABLES["NEOWISE"])) == [True, False, True]
    del table["qual_frame"]
    table["qi_fact"][0] = 0.0
    assert list(clean(table, WISE_TABLES["ALLWISE"])) == [False, False, True]


def test_each_clean_exposure_is_an_association_record_at_its_own_epoch() -> None:
    pid = product_id(DESIGNATION, *SOURCE, "NEOWISE")
    dets = w1_detections(votable_bytes(exposures(sigra=[0.05, 0.0, 0.05])), pid)
    assert [d.record_id for d in dets] == [f"{pid}#1", f"{pid}#2", f"{pid}#0"]
    first = dets[0]
    assert (first.product_id, first.extension, first.instrument, first.resolution_arcsec) == (
        pid,
        "TABLE0",
        "WISE",
        6.1,
    )
    assert (first.ra_deg, first.dec_deg) == (10.0, 20.0)
    assert first.epoch_jyear == pytest.approx(2000.0 + (58000.0 - 51544.5) / 365.25, abs=1e-12)
    assert first.photometry[0].band == "W1"
    assert (first.photometry[0].magnitude, first.photometry[0].sigma) == pytest.approx((11.0, 0.03))
    beam = 6.1 / 2.3548200450309493 * 0.03 / 1.0857362047581294
    assert (first.covariance.sigma_ra_arcsec, first.covariance.sigma_dec_arcsec) == pytest.approx((beam, beam)), (
        "no sigra: the beam's centroid precision"
    )
    assert (dets[1].covariance.sigma_ra_arcsec, dets[1].covariance.sigma_dec_arcsec) == pytest.approx((0.05, 0.06)), (
        "the table's own sigmas"
    )
    epochs = exposures()
    epochs.rename_columns(["w1mpro", "w1sigmpro"], ["w1mpro_ep", "w1sigmpro_ep"])
    allwise = w1_detections(votable_bytes(epochs), product_id(DESIGNATION, *SOURCE, "ALLWISE"))
    assert (allwise[0].covariance.sigma_ra_arcsec, allwise[0].covariance.sigma_dec_arcsec) == pytest.approx(
        (beam, beam)
    ), "AllWISE has no sigra"


def gaia_pair() -> list[CatalogueSource]:
    """The variable and its G = 17.6 neighbour 4.7 arcsec away, as Gaia DR3 gives them (read 2026-10-29)."""
    bright = CatalogueSource(
        "gaia:64971933089093760",
        "gaia",
        "gaia:DR3",
        "64971933089093760",
        56.534933945361814,
        23.3473212122962,
        PositionCovariance(0.014513168e-3, 0.010024038e-3),
        2016.0,
        Motion(19.54291585018702, -45.246857647763434, 7.388568334356815, 0.01882543, 0.013102093, 0.015051717),
    )
    faint = CatalogueSource(
        "gaia:64971933089093632",
        "gaia",
        "gaia:DR3",
        "64971933089093632",
        56.5362400738399,
        23.346808216259614,
        PositionCovariance(0.11527101e-3, 0.080930226e-3),
        2016.0,
        Motion(2.423147683602297, -5.947389885710633, 0.25777760324214316, 0.14964984, 0.10796746, 0.12207962),
    )
    return [bright, faint]


@pytest.mark.tier("T3")
@pytest.mark.parametrize("key", ["NEOWISE", "ALLWISE"])
def test_both_methods_reconcile_wise_exposures_with_the_gaia_pair_its_beam_blends(key: str) -> None:
    dets = w1_detections(raw(key).fetched.data, ref(key).product_id)
    policy = AssociationPolicy("wise-gaia", "1.0.0", 2016.0, search_radius_arcsec=6.1)
    roles = []
    for service in (BayesFactorAssociation(), LikelihoodRatioAssociation()):
        graph = service.associate([*gaia_pair(), *dets], policy)
        tops: Counter[str] = Counter()
        for d in dets:
            ranked = graph.for_record(d.record_id)
            top = ranked[0]
            tops[top.evidence["role"]] += 1
            if top.evidence["role"] == "blend":
                assert {a.hypothesis_id for a in ranked if a.evidence["role"] == "blend"} == {
                    "obj:gaia:64971933089093760",
                    "obj:gaia:64971933089093632",
                }, "both stars in the beam are kept, neither alone (INT-009)"
                assert top.evidence["comparison_epoch_jyear"] == pytest.approx(d.epoch_jyear)
        roles.append(tops)
    assert roles[0] == roles[1], "the two methods agree on every exposure"
    assert roles[0]["blend"] >= 0.9 * len(dets) and set(roles[0]) <= {"blend", "no-counterpart"}


def test_a_fast_star_is_followed_along_its_path_and_a_slow_one_cut_where_allwise_saw_it() -> None:
    neowise = WISE_TABLES["NEOWISE"]
    assert lightcurve_cone(STAR, neowise, *SOURCE) == (*SOURCE, 2.5), "the Pleiades star moves 0.5″ in 10 years"
    assert lightcurve_cone(FIELD, neowise, *SOURCE) == (*SOURCE, 2.5)
    fast = ObservationTarget("fast", 10.0, 60.0, 1000.0, -2000.0, 2016.0)
    ra, dec, radius = lightcurve_cone(fast, neowise, 10.0, 60.0)
    start, end = (2000.0 + (m - 51544.5) / 365.25 for m in neowise.span_mjd)
    years = (start + end) / 2 - 2010.5589
    assert dec == pytest.approx(60.0 - 2000.0 * years / 3.6e6, abs=1e-12)
    assert ra == pytest.approx(10.0 + 1000.0 * years / 3.6e6 / 0.5, abs=1e-9)
    assert radius == pytest.approx(2.5 + np.hypot(1.0, 2.0) * (end - start) / 2)
    edge = ObservationTarget("edge", 10.0, 0.0, 1000.0 / (end - start), 0.0, 2016.0)
    assert lightcurve_cone(edge, neowise, 10.0, 0.0)[2] == 2.5, "exactly the tolerance stays put"
    wrapped = lightcurve_cone(ObservationTarget("w", 359.9999, 0.0, -5000.0, 0.0, 2016.0), neowise, 359.9999, 0.0)
    assert 359.9 < wrapped[0] < 360.0 or wrapped[0] < 0.1


def test_a_fast_star_light_curve_url_is_the_wider_cone() -> None:
    fast = ObservationTarget("fast", SOURCE[0], SOURCE[1], 3000.0, 0.0, 2016.0)
    ra, dec, radius = lightcurve_cone(fast, WISE_TABLES["NEOWISE"], *SOURCE)
    assert radius > 10.0
    query = lightcurve_query(WISE_TABLES["NEOWISE"], ra, dec, radius)
    assert f"{radius / 3600.0!r}))" in query and f"{ra:.7f}" in query
