"""T3/T5: MAST (ObsCore, HAPCut) and DASCH behind ObservationArchiveGateway, and the DASCH dialect,
pass the port contracts against primed fakes of the services and again replayed from cassettes."""

from __future__ import annotations

import base64
import gzip
import io
import json
import zipfile
from pathlib import Path
from urllib.parse import parse_qs

import astropy.units as u
import pytest

from sdp.adapters.archives.dasch import STANDARD_BAD_AFLAGS, DaschDialect, DaschGateway
from sdp.adapters.archives.mast import PRODUCT_TYPES, MastObservationGateway
from sdp.adapters.archives.transport import CassetteTransport, HttpRequest, Transport, TransportError
from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.investigation import FetchedProduct, Field, ObservationCriteria, ProductRef
from sdp.kernel.ports.investigate import ObservationTarget
from sdp.kernel.science.products import parse_lightcurve, parse_positions
from sdp.testing.contracts.investigate import (
    DialectCase,
    ObservationArchiveGatewayContract,
    ObservationCase,
    ProductDialectContract,
)

from .vo_fakes import PrimedService

pytestmark = pytest.mark.tier("T3")

# Invented targets, products and plates, for exercising the adapters only.
TARGET = ObservationTarget("obj-1", 83.8, -5.4)
FIELD = Field(Cone(83.8, -5.4, 0.05))
FITS = b"SIMPLE  =                    T / invented fixture" + b" " * 2830
MAST_CRITERIA = ObservationCriteria(missions=("TESS",), product_types=("timeseries",), radius_arcsec=10.0)
DASCH_CRITERIA = ObservationCriteria(radius_arcsec=10.0)


def answer(lines: list[str]) -> bytes:
    return json.dumps(lines).encode()


def lightcurve_lines() -> list[str]:
    header = "date_jd,magcal_magdep,magcal_magdep_rms,ra_deg,dec_deg,gsc_bin_index,sextractor_number,aflags,drad_rms2"
    rows = [
        "2420000.5,12.10,0.10,83.80001,-5.40001,7,11,0,0.6",  # good
        "2410000.5,12.30,0.12,83.80002,-5.40002,7,12,0,99",  # good, no position error: default applies
        "2430000.5,,,,,0,0,0,",  # non-detection
        f"2440000.5,12.20,0.10,83.80003,-5.40003,7,13,{1 << 13},0.5",  # close to the limiting magnitude
        "2445000.5,12.25,99,83.80004,-5.40004,7,14,0,0.7",  # no magnitude error: astrometry only
    ]
    return [header, *rows]


def mast_table() -> dict[str, object]:
    return {
        "obs_id": ["tess-s0006-0001", "tess-s0032-0001"],
        "obs_collection": ["TESS", "TESS"],
        "instrument_name": ["Photometer", "Photometer"],
        "dataproduct_type": ["timeseries", "timeseries"],
        "t_min": [58468.3, 59174.2],
        "t_max": [58490.0, 59199.9],
        "access_url": ["https://mast.example/lc-6.fits", "https://mast.example/lc-32.fits"],
        "access_format": ["application/fits", "application/fits"],
        "access_estsize": [2000.0, 2100.0],
    }


def hapcut_zip() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("hst_cutout_b.fits", FITS + b"b")
        zf.writestr("hst_cutout_a.fits", FITS)
    return out.getvalue()


def primed_mast(fake: PrimedService, gateway: MastObservationGateway) -> None:
    fake.table(gateway.search_request(TARGET, MAST_CRITERIA), mast_table())
    for url in ("https://mast.example/lc-6.fits", "https://mast.example/lc-32.fits"):
        fake.raw(HttpRequest("GET", url), FITS, content_type="application/fits; charset=binary")
    fake.raw(gateway.cutout_request(FIELD, 1 * u.arcmin), hapcut_zip(), content_type="application/zip")


def primed_dasch(fake: PrimedService, gateway: DaschGateway) -> None:
    fake.raw(
        gateway.querycat_request(TARGET.ra_deg, TARGET.dec_deg, 10.0),
        answer(
            [
                "ref_text,ref_number,gsc_bin_index,ra_deg,dec_deg,stdmag",
                "far,3,7,83.8040,-5.4000,13.0",  # 14 arcsec: outside the radius
                "near,2,7,83.8001,-5.4001,12.1",
                "next,1,7,83.8010,-5.4010,12.9",
            ]
        ),
        content_type="application/json",
    )
    fake.raw(gateway.lightcurve_request("apass", 7, 2), answer(lightcurve_lines()), content_type="application/json")
    fake.raw(
        gateway.queryexps_request(TARGET.ra_deg, TARGET.dec_deg),
        answer(["series,platenum,solnum,wcssource", "mc,1234,0,imwcs", "a,99,1,astrometry.net", "b,42,0,imwcs"]),
        content_type="application/json",
    )
    encoded = base64.b64encode(gzip.compress(FITS)).decode()
    fake.raw(
        gateway.cutout_request("b00042", 0, TARGET.ra_deg, TARGET.dec_deg),
        json.dumps(encoded).encode(),
        content_type="application/json",
    )


def mast_case(transport: Transport) -> ObservationCase:
    return ObservationCase(MastObservationGateway(transport), TARGET, MAST_CRITERIA, FIELD)


def dasch_case(transport: Transport) -> ObservationCase:
    return ObservationCase(DaschGateway(transport), TARGET, DASCH_CRITERIA, FIELD)


class TestMastObservationGateway(ObservationArchiveGatewayContract):
    @pytest.fixture
    def observation_case(self) -> ObservationCase:
        fake = PrimedService()
        primed_mast(fake, MastObservationGateway(fake))
        return mast_case(fake)


class TestDaschGateway(ObservationArchiveGatewayContract):
    @pytest.fixture
    def observation_case(self) -> ObservationCase:
        fake = PrimedService()
        primed_dasch(fake, DaschGateway(fake))
        return dasch_case(fake)


def replayed(tmp_path: Path, prime: object, build: object) -> Transport:
    fake = PrimedService()
    recorder = CassetteTransport(tmp_path, "record", inner=fake)
    prime(fake, build(recorder))  # type: ignore[operator]
    case = (mast_case if build is MastObservationGateway else dasch_case)(recorder)
    index = case.gateway.search(case.target, case.criteria)
    for rec in index.records:
        case.gateway.fetch(rec.product)
    case.gateway.cutout(case.field, 1 * u.arcmin)
    return CassetteTransport(tmp_path, "replay")


@pytest.mark.tier("T5")
@pytest.mark.req("INV-004")
class TestMastFromCassettes(ObservationArchiveGatewayContract):
    @pytest.fixture
    def observation_case(self, tmp_path: Path) -> ObservationCase:
        return mast_case(replayed(tmp_path, primed_mast, MastObservationGateway))


@pytest.mark.tier("T5")
@pytest.mark.req("INV-004")
class TestDaschFromCassettes(ObservationArchiveGatewayContract):
    @pytest.fixture
    def observation_case(self, tmp_path: Path) -> ObservationCase:
        return dasch_case(replayed(tmp_path, primed_dasch, DaschGateway))


def raw_lightcurve() -> FetchedProduct:
    ref = ProductRef("dasch", "apass:7:2", "https://api.example/dasch/dr7/lightcurve?refcat=apass")
    return FetchedProduct(ref, answer(lightcurve_lines()), "application/json", "dasch:DR7", "l", "c", ref.uri)


class TestDaschLightcurveDialect(ProductDialectContract):
    @pytest.fixture
    def dialect_case(self) -> DialectCase:
        return DialectCase(DaschDialect(), raw_lightcurve(), "lightcurve", min_epochs=2)


class TestDaschAstrometryDialect(ProductDialectContract):
    @pytest.fixture
    def dialect_case(self) -> DialectCase:
        return DialectCase(DaschDialect(), raw_lightcurve(), "astrometry", min_epochs=3)


# -- MAST specifics ---------------------------------------------------------------------------------


def test_mast_search_is_bounded_ordered_obscore_adql_with_mission_and_type_filters() -> None:
    gateway = MastObservationGateway(PrimedService())
    criteria = ObservationCriteria(
        missions=("TESS", "HST"), product_types=("timeseries", "image"), radius_arcsec=5.0, epoch_min_mjd=50000.0
    )
    text = gateway.search_query(TARGET, criteria)
    assert text.startswith("SELECT TOP 500 obs_id, obs_collection")
    assert "FROM ivoa.obscore" in text and "ORDER BY t_min, obs_id, access_url" in text
    assert "obs_collection IN ('TESS', 'HST')" in text
    assert "dataproduct_type IN ('timeseries', 'image')" in text
    assert "t_min >= 50000.0" in text
    body = parse_qs(gateway.search_request(TARGET, criteria).body.decode())
    assert body["REQUEST"] == ["doQuery"] and body["LANG"] == ["ADQL"]


def test_mast_records_carry_epochs_sizes_and_no_cloud_uri() -> None:
    fake = PrimedService()
    gateway = MastObservationGateway(fake)
    primed_mast(fake, gateway)
    index = gateway.search(TARGET, MAST_CRITERIA)
    first = index.records[0]
    assert (first.mission, first.product_type, first.epoch_mjd, first.epoch_end_mjd) == (
        "TESS",
        "timeseries",
        58468.3,
        58490.0,
    )
    assert first.size_bytes == 2000 * 1024 and first.product.cloud_uri is None
    assert index.reach_back_mjd == 58468.3
    fetched = gateway.fetch(first.product)
    assert fetched.media_type == "application/fits" and fetched.data == FITS


def test_hapcut_answers_unzip_to_the_first_fits_file_by_name() -> None:
    fake = PrimedService()
    gateway = MastObservationGateway(fake)
    primed_mast(fake, gateway)
    cut = gateway.cutout(FIELD, 1 * u.arcmin)
    assert cut.data == FITS and cut.product.product_id == "hapcut/hst_cutout_a.fits"
    assert gateway.fetch(cut.product).data == FITS  # the reference names the member, not the zip
    assert "units=d" in cut.read_from and "x=0.01666" in cut.read_from
    with pytest.raises(ValueError, match="within a degree"):
        gateway.cutout_request(FIELD, 2 * u.deg)
    fake.raw(gateway.cutout_request(FIELD, 2 * u.arcmin), b'{"msg": "no data"}', content_type="application/json")
    with pytest.raises(TransportError, match="no cutout"):
        gateway.cutout(FIELD, 2 * u.arcmin)


def test_mast_refuses_another_archives_product() -> None:
    with pytest.raises(ValueError, match="not a MAST one"):
        MastObservationGateway(PrimedService()).fetch(ProductRef("dasch", "x", "https://x"))


# -- DASCH specifics --------------------------------------------------------------------------------


def test_dasch_offers_the_nearest_star_inside_the_radius_as_light_curve_and_positions() -> None:
    fake = PrimedService()
    gateway = DaschGateway(fake)
    primed_dasch(fake, gateway)
    index = gateway.search(TARGET, DASCH_CRITERIA)
    assert [r.product_type for r in index.records] == ["lightcurve", "astrometry"]
    assert {r.product.product_id for r in index.records} == {"apass:7:2"}
    assert index.release_label == "dasch:DR7" and "querycat" in index.query_text
    only = gateway.search(TARGET, ObservationCriteria(product_types=("astrometry",), radius_arcsec=10.0))
    assert [r.product_type for r in only.records] == ["astrometry"]


def test_dasch_is_skipped_without_a_call_when_the_criteria_exclude_it() -> None:
    fake = PrimedService()
    gateway = DaschGateway(fake)
    index = gateway.search(TARGET, ObservationCriteria(missions=("TESS",)))
    assert index.records == () and fake.calls == 0


def test_dasch_api_errors_raise() -> None:
    fake = PrimedService()
    gateway = DaschGateway(fake)
    fake.raw(
        gateway.querycat_request(TARGET.ra_deg, TARGET.dec_deg, 10.0),
        b'{"message": "Internal server error"}',
        content_type="application/json",
    )
    with pytest.raises(TransportError, match="querycat failed"):
        gateway.search(TARGET, DASCH_CRITERIA)


def test_the_dasch_cutout_uses_the_first_plate_with_a_dasch_solved_wcs() -> None:
    fake = PrimedService()
    gateway = DaschGateway(fake)
    primed_dasch(fake, gateway)
    cut = gateway.cutout(FIELD, 1 * u.arcmin)
    assert cut.data == FITS and cut.media_type == "image/fits"
    assert cut.product.product_id == "cutout/b00042/0"
    assert gateway.fetch(cut.product).data == FITS


def test_a_dasch_cutout_that_is_not_json_is_a_transport_error() -> None:
    fake = PrimedService()
    gateway = DaschGateway(fake)
    primed_dasch(fake, gateway)
    fake.raw(
        gateway.cutout_request("b00042", 0, TARGET.ra_deg, TARGET.dec_deg),
        b"<html>busy</html>",
        content_type="text/html",
    )
    with pytest.raises(TransportError, match="not JSON"):
        gateway.cutout(FIELD, 1 * u.arcmin)


def test_mast_time_series_are_not_offered_as_canonical_light_curves() -> None:
    # Mission FITS is not the lightcurve CSV a variability test parses; a MAST dialect would curate it.
    assert PRODUCT_TYPES["timeseries"] != "lightcurve"


def test_the_dialect_drops_non_detections_and_bad_flags_and_falls_back_to_the_declared_error() -> None:
    lc = parse_lightcurve(DaschDialect().lightcurve(answer(lightcurve_lines())))
    assert lc.mjd.tolist() == [2410000.5 - 2400000.5, 2420000.5 - 2400000.5]
    assert lc.mag.tolist() == [12.30, 12.10] and lc.mag_err.tolist() == [0.12, 0.10]
    pos = parse_positions(DaschDialect(default_pos_err_mas=1500.0).astrometry(answer(lightcurve_lines())))
    assert pos.pos_err_mas.tolist() == [1500.0, 600.0, 700.0]
    assert pos.epoch_jyear[0] == pytest.approx(1900.0 + (2410000.5 - 2415020.0) / 365.25, abs=0.01)
    kept = parse_positions(DaschDialect(reject_aflags=0).astrometry(answer(lightcurve_lines())))
    assert len(kept.epoch_jyear) == 4
    assert STANDARD_BAD_AFLAGS & (1 << 13)


def test_the_dialect_declares_what_it_does_not_curate() -> None:
    assert DaschDialect().curate("image", raw_lightcurve()) is None
