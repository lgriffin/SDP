"""T4: the image-counterpart test on the synthetic JWST field, through the kernel with photutils and
both association methods (Integration v0.4.0 §7, INT-020)."""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from sdp.adapters.association import BayesFactorAssociation, LikelihoodRatioAssociation
from sdp.adapters.imaging.photutils_detector import PhotutilsDetector, header_epoch_mjd, image_headers
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.association import AssociationPolicy, CatalogueSource, Detection, PositionCovariance
from sdp.kernel.domain.errors import PlanInvalid
from sdp.kernel.domain.investigation import Stage
from sdp.kernel.ports.investigate import DetectionList, ImageFrame, InvestigateFieldCommand
from sdp.testing.fakes.investigate import FakeObservationArchive
from sdp.testing.investigate import gaia_archive, investigate_ports
from sdp.testing.jwst import (
    FAST,
    GAIA,
    IMAGE_MJD,
    MAST,
    PAIR_A,
    PAIR_B,
    PRODUCT_ID,
    VANISHED,
    image_epoch_jyear,
    jwst_archive,
    jwst_catalogue,
    jwst_field,
    jwst_i2d,
    jwst_profile,
    jwst_resolver,
)
from sdp.testing.wiring import build_in_memory_kernel
from sdp_investigate.imaging import MISSING_STATISTIC, ImageCounterpart, best_counterpart, counterpart_rows, epoch_jyear

pytestmark = pytest.mark.tier("T4")


def services() -> dict[str, Any]:
    return {"detector": PhotutilsDetector(), "associators": (BayesFactorAssociation(), LikelihoodRatioAssociation())}


def run_field(archive: FakeObservationArchive | None = None) -> dict[str, dict[str, Any]]:
    catalogue = jwst_catalogue()
    ports = investigate_ports(ImageCounterpart(), catalogue=catalogue, resolver=jwst_resolver(catalogue))
    ports.observation_gateways = {MAST: archive or jwst_archive(catalogue)}
    ports.services = services()
    k = build_in_memory_kernel(gateways={GAIA: gaia_archive(catalogue)}, investigate=ports)
    assert k.kernel.investigate_field is not None
    run = k.kernel.investigate_field.execute(InvestigateFieldCommand(jwst_field(), jwst_profile(), seed=1))
    stage = run.investigation_set.stages[Stage.TEST].artifact_id
    assert stage is not None
    return {r["subject"]["object_key"]: r for r in decode_artifact(k.store.get(stage), JSON)["results"]}


@pytest.fixture(scope="module")
def results() -> dict[str, dict[str, Any]]:
    return run_field()


@pytest.mark.req("INT-020")
def test_a_gaia_star_missing_from_the_image_is_flagged(results: dict[str, dict[str, Any]]) -> None:
    vanished = results[VANISHED]
    assert vanished["p_value"] == 0.0 and vanished["statistic"] == MISSING_STATISTIC
    assert vanished["details"]["counterpart_found"] is False and vanished["details"]["counterparts"] == []
    flagged = {key for key, r in results.items() if r["p_value"] < 0.01}
    assert flagged == {VANISHED}, "every other star sits where its proper motion puts it"


@pytest.mark.req("INT-010")
@pytest.mark.req("INT-020")
def test_a_fast_star_is_found_only_at_its_propagated_position(results: dict[str, dict[str, Any]]) -> None:
    fast = results[FAST]["details"]
    assert fast["counterpart_found"] and fast["counterparts"][0]["posterior"] > 0.99
    assert results[FAST]["p_value"] > 0.05


@pytest.mark.req("INT-009")
@pytest.mark.req("INT-012")
@pytest.mark.req("INT-020")
def test_a_blended_pair_keeps_both_hypotheses_and_both_methods_agree(results: dict[str, dict[str, Any]]) -> None:
    for star, other in ((PAIR_A, PAIR_B), (PAIR_B, PAIR_A)):
        details = results[star]["details"]
        [blend] = details["counterparts"]
        assert details["ambiguous"] and blend["role"] == "blend"
        assert sorted(blend["blend_of"]) == sorted([PAIR_A, PAIR_B]) and blend["competing"] == [f"obj:{other}"]
        assert blend["posterior"] == pytest.approx(0.5, abs=1e-6)
        assert details["other_methods"]["likelihood-ratio"] == pytest.approx(blend["posterior"], abs=1e-6)
        assert results[star]["p_value"] > 0.05, "a blend is measured against its members' mean position"
    assert len(results[PAIR_A]["details"]["graph_ids"]) == 2


def test_the_image_epoch_comes_from_the_header() -> None:
    found = PhotutilsDetector().detect(ImageFrame("a", jwst_i2d(), "application/fits"), {"fwhm": 3.0})
    assert found.epoch_mjd == IMAGE_MJD and found.instrument == "NIRCAM"
    assert epoch_jyear(found, "a") == pytest.approx(image_epoch_jyear())
    dated = fits.Header({"DATE-OBS": "2023-02-25", "TIME-OBS": "00:00:00"})
    assert header_epoch_mjd(fits.Header(), dated) == pytest.approx(IMAGE_MJD)
    assert header_epoch_mjd(fits.Header()) is None
    with pytest.raises(PlanInvalid, match="no observation time"):
        epoch_jyear(DetectionList((), "", 0.0, 1.0, "photutils"), "a")


@pytest.mark.req("INT-020")
def test_a_star_found_on_one_image_but_missing_from_another_is_flagged() -> None:
    second = PRODUCT_ID.replace("o001", "o002")
    archive = jwst_archive(images=((PRODUCT_ID, jwst_i2d()), (second, jwst_i2d(missing=(VANISHED, FAST), seed=6))))
    results = run_field(archive)
    assert {key for key, r in results.items() if r["p_value"] < 0.01} == {VANISHED, FAST}
    fast = results[FAST]["details"]
    assert fast["images"] == 2 and fast["image"] == second and not fast["counterpart_found"]
    # Each image keeps its own hypotheses (INV-020): the first image's counterpart is not lost to the worst.
    on = {p["image"]: p for p in fast["per_image"]}
    assert on[second]["counterparts"] == fast["counterparts"]
    assert on[PRODUCT_ID]["counterparts"] and on[PRODUCT_ID]["counterparts"] != fast["counterparts"]


def test_a_detection_more_probably_unrelated_is_not_a_counterpart() -> None:
    sigma = PositionCovariance(0.05, 0.05)
    star = CatalogueSource("s", "gaia", "", "s", 150.0, 2.0, sigma, 2016.0, None)
    near = Detection("d", "img", "SCI", 150.0 + 0.4 / 3600, 2.0, sigma, 2016.0, 0.1, "X")
    policy = AssociationPolicy(
        "t", "1.0.0", common_epoch_jyear=2016.0, search_radius_arcsec=1.0, anchor_catalogue="gaia"
    )
    [row] = counterpart_rows(BayesFactorAssociation().associate([star, near], policy), "s")
    assert row["no_counterpart"] > row["option_posterior"] > 0
    assert best_counterpart([row]) is None


def test_the_epoch_comes_from_the_extension_the_pixels_come_from() -> None:
    data = np.zeros((8, 8), dtype=np.float32)
    hdus = fits.HDUList(
        [
            fits.PrimaryHDU(),
            fits.ImageHDU(data, fits.Header({"MJD-AVG": 59000.0}), name="SCI"),
            fits.ImageHDU(data, fits.Header({"MJD-AVG": 59500.0}), name="SCI2"),
        ]
    )
    buffer = io.BytesIO()
    hdus.writeto(buffer)
    frame = ImageFrame("a", buffer.getvalue(), "application/fits")
    assert header_epoch_mjd(*image_headers(frame)) == 59000.0
    assert header_epoch_mjd(*image_headers(frame, 2)) == 59500.0


@pytest.mark.req("INV-020")
def test_each_star_carries_where_it_and_its_counterpart_fall_on_each_image(results: dict[str, dict[str, Any]]) -> None:
    import math

    from astropy.wcs import WCS

    from sdp.testing.jwst import IMAGE_PIXELS, JWST_CENTRE, PIXEL_ARCSEC

    wcs = WCS(
        {
            "NAXIS": 2,
            "CTYPE1": "RA---TAN",
            "CTYPE2": "DEC--TAN",
            "CRVAL1": JWST_CENTRE[0],
            "CRVAL2": JWST_CENTRE[1],
            "CRPIX1": (IMAGE_PIXELS + 1) / 2,
            "CRPIX2": (IMAGE_PIXELS + 1) / 2,
            "CDELT1": -PIXEL_ARCSEC / 3600,
            "CDELT2": PIXEL_ARCSEC / 3600,
        }
    )
    dt = image_epoch_jyear() - 2016.0
    for row in jwst_catalogue().to_pylist():
        r = results[row["source_key"]]
        [on] = r["details"]["per_image"]
        assert on["image"] == r["details"]["image"] and on["ambiguous"] == r["details"]["ambiguous"]
        cosd = math.cos(math.radians(row["dec_deg"]))
        ra = row["ra_deg"] + row["pm_ra_cosdec_masyr"] * dt / 3.6e6 / cosd
        dec = row["dec_deg"] + row["pm_dec_masyr"] * dt / 3.6e6
        x, y = wcs.world_to_pixel_values(ra, dec)
        assert on["predicted_px"] == pytest.approx([float(x), float(y)], abs=0.05), row["source_key"]
        if row["source_key"] == VANISHED:
            assert on["counterpart_px"] is None and on["d2"] is None
        else:
            assert on["counterpart_px"] == pytest.approx(on["predicted_px"], abs=1.0)
            assert on["d2"] == pytest.approx(r["statistic"])
