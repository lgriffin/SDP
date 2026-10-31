"""T3/T5: MAST (ObsCore, HAPCut) and DASCH behind ObservationArchiveGateway, and the DASCH dialect,
pass the port contracts against primed fakes of the services and again replayed from cassettes."""

from __future__ import annotations

import base64
import gzip
import io
import json
import math
import zipfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs

import astropy.units as u
import pytest

from sdp.adapters.archives.dasch import STANDARD_BAD_AFLAGS, DaschDialect, DaschGateway
from sdp.adapters.archives.mast import PRODUCT_TYPES, MastObservationGateway, file_band, obs_id_of
from sdp.adapters.archives.transport import CassetteTransport, HttpRequest, Transport, TransportError
from sdp.kernel.application.investigate import observation_target
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
    assert "LOWER(access_format) LIKE '%fits%'" in text, "non-FITS rows must not use up the TOP limit"
    assert "access_url LIKE '%_i2d.fits' OR access_url LIKE '%_cal.fits'" in text
    body = parse_qs(gateway.search_request(TARGET, criteria).body.decode())
    assert body["REQUEST"] == ["doQuery"] and body["LANG"] == ["ADQL"]
    assert "calib_level" not in text
    mosaics = gateway.search_query(TARGET, replace(criteria, min_calibration_level=3))
    assert "calib_level >= 3" in mosaics, "level-3 mosaics only, when the profile asks"


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
    assert first.size_bytes == 2000, "MAST reports access_estsize in bytes"
    assert first.product.product_id == "tess-s0006-0001/lc-6.fits" and first.product.cloud_uri is None
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


def test_dasch_follows_a_fast_moving_star_back_to_its_reference_catalogue_position() -> None:
    # 1"/yr east: at 2000 the star sat 16" west of its Gaia 2016 position, well outside a 3" radius.
    fast = ObservationTarget("fast", 83.8, -5.4, pm_ra_cosdec_masyr=1000.0, pm_dec_masyr=0.0, epoch_jyear=2016.0)
    west = 12.0 / 3600.0 / math.cos(math.radians(-5.4))
    fake = PrimedService()
    gateway = DaschGateway(fake)
    fake.raw(
        gateway.querycat_request(83.8, -5.4, 3.0 + 16.0),
        answer(
            [
                "ref_text,ref_number,gsc_bin_index,ra_deg,dec_deg,stdmag",
                f"on-path,5,7,{83.8 - west},-5.4,12.0",  # 12" west, on the star's path back to 2000
                f"off-path,6,7,{83.8 - west},{-5.4 + 6.0 / 3600.0},12.0",  # 6" north of the path
            ]
        ),
        content_type="application/json",
    )
    criteria = ObservationCriteria(radius_arcsec=3.0)
    assert {r.product.product_id for r in gateway.search(fast, criteria).records} == {"apass:7:5"}
    still = ObservationTarget("still", 83.8, -5.4)
    fake.raw(
        gateway.querycat_request(83.8, -5.4, 3.0),
        answer(["ref_text,ref_number,gsc_bin_index,ra_deg,dec_deg,stdmag", f"on-path,5,7,{83.8 - west},-5.4,12.0"]),
        content_type="application/json",
    )
    assert gateway.search(still, criteria).records == ()  # without its motion, 12" is too far


def test_an_observation_search_carries_the_gaia_motion_identify_read() -> None:
    row = {"pm_ra_cosdec_masyr": 1000.0, "pm_dec_masyr": -5.0, "epoch_jyear": 2016.0}
    moving = observation_target("obj", 83.8, -5.4, [row])
    assert moving.moving and (moving.pm_ra_cosdec_masyr, moving.pm_dec_masyr, moving.epoch_jyear) == (
        1000.0,
        -5.0,
        2016.0,
    )
    assert not observation_target("obj", 83.8, -5.4, []).moving
    assert not observation_target("obj", 83.8, -5.4, [{**row, "pm_dec_masyr": None}]).moving
    assert not observation_target("obj", 83.8, -5.4, [{**row, "epoch_jyear": float("nan")}]).moving


def test_dasch_is_skipped_without_a_call_when_the_criteria_exclude_it() -> None:
    fake = PrimedService()
    gateway = DaschGateway(fake)
    index = gateway.search(TARGET, ObservationCriteria(missions=("TESS",)))
    assert index.records == () and fake.calls == 0
    floored = gateway.search(TARGET, ObservationCriteria(min_calibration_level=3))
    assert floored.records == () and fake.calls == 0, "DASCH states no calibration level to meet a floor"


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


def test_jwst_lists_one_product_per_science_file_and_drops_auxiliary_files() -> None:
    fake = PrimedService()
    gateway = MastObservationGateway(fake)
    obs = "jw01074-o104_t002_nircam_clear-f200w-sub160"
    base = "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:JWST/product/"
    files = [
        (f"{obs}_i2d.fits", "application/fits", 9354240.0),
        (f"{obs}_segm.fits", "application/fits", 1442880.0),
        (f"{obs}_cat.ecsv", "text/plain", 25867.0),
        (f"{obs}_i2d.jpg", "image/jpeg", 47348.0),
    ]
    table = {
        "obs_id": [obs] * 4,
        "obs_collection": ["JWST"] * 4,
        "instrument_name": ["NIRCAM/IMAGE"] * 4,
        "dataproduct_type": ["image"] * 4,
        "t_min": [59671.4] * 4,
        "t_max": [59671.5] * 4,
        "access_url": [base + f for f, _, _ in files],
        "access_format": [m for _, m, _ in files],
        "access_estsize": [b for _, _, b in files],
    }
    fake.table(gateway.search_request(TARGET, MAST_CRITERIA), table)
    [record] = gateway.search(TARGET, MAST_CRITERIA).records
    assert record.product.product_id == f"{obs}/{obs}_i2d.fits"
    assert record.size_bytes == 9354240
    assert obs_id_of(record.product.product_id) == obs and obs_id_of(obs) == obs


@pytest.mark.req("INT-026")
@pytest.mark.parametrize(
    ("name", "band", "single"),
    [
        ("jw02739-o001_t001_nircam_clear-f200w_i2d.fits", "F200W", False),
        ("jw02739-o001_t001_nircam_f444w-f470n_i2d.fits", "F444W-F470N", False),
        ("jw02739-o002_t001_miri_f1130w_i2d.fits", "F1130W", False),
        ("jw01074-o104_t002_nircam_clear-f200w-sub160_i2d.fits", "F200W", False),
        ("jw02739001001_02101_00004_nrcb3_i2d.fits", None, True),
        ("jw01345-o001_t021_nircam_clear-f150w2_i2d.fits", "F150W2", False),
        ("jw01345-o001_t021_nircam_f322w2-f323n_i2d.fits", "F322W2-F323N", False),
        ("hst_13926_05_wfc3_uvis_f657n_ick905_drc.fits", "F657N", False),
        ("hst_13926_05_wfc3_uvis_f657n_ick905k5_drc.fits", "F657N", True),
        ("hst_skycell-p1033x08y12_acs_wfc_f775w_all_drc.fits", "F775W", False),
        ("hst_5773_05_wfpc2_pc_f656n_u2lx05_drz.fits", "F656N", False),
        ("hst_13926_07_acs_wfc_total_jck907_drc.fits", None, False),
        ("j9cs01010_drz.fits", None, False),
        ("lc-6.fits", None, False),
    ],
)
def test_the_filter_and_whether_a_file_is_one_exposure_are_read_from_its_name(
    name: str, band: str | None, single: bool
) -> None:
    """MAST's ObsCore has no filter column; its file names carry one (checked on M16, 2026-10-31)."""
    assert file_band(name) == (band, single)


@pytest.mark.req("INT-026")
def test_mast_image_records_carry_their_filter_and_single_exposures_say_so() -> None:
    fake = PrimedService()
    gateway = MastObservationGateway(fake)
    criteria = ObservationCriteria(missions=("JWST", "HST"), product_types=("image",), radius_arcsec=60.0)
    names = ["jw02739-o001_t001_nircam_clear-f200w_i2d.fits", "hst_13926_05_wfc3_uvis_f657n_ick905k5_drc.fits"]
    fake.table(
        gateway.search_request(TARGET, criteria),
        {
            "obs_id": ["jw02739-o001_t001_nircam_clear-f200w", "hst_13926_05_wfc3_uvis_f657n_ick905k5"],
            "obs_collection": ["JWST", "HST"],
            "instrument_name": ["NIRCAM/IMAGE", "WFC3/UVIS"],
            "dataproduct_type": ["image", "image"],
            "t_min": [59805.4, 56902.2],
            "t_max": [59805.6, 56902.3],
            "access_url": [f"https://mast.example/{n}" for n in names],
            "access_format": ["application/fits", "application/fits"],
            "access_estsize": [5421758400.0, 505062720.0],
        },
    )
    jwst, hst = gateway.search(TARGET, criteria).records
    assert (jwst.band, jwst.single_exposure) == ("F200W", False)
    assert (hst.band, hst.single_exposure) == ("F657N", True)
    assert "band" in jwst.to_document() and type(jwst).from_document(jwst.to_document()) == jwst
