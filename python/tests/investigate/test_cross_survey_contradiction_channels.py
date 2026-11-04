"""T4: cross-survey-contradiction compares Gaia with AllWISE W1/W2 and with Hubble and Webb images
(INV-028). AllWISE rows come out of the packaged dialect; the images are sdp.testing.epochs' synthetic
cameras, run through the kernel with photutils, tweakwcs and forced photometry as in production."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from tests.investigate.test_cross_survey_contradiction import (
    KS_EXCESS,
    MOVED,
    WISE_CONTAMINATED,
    WISE_EXCESS,
    WISE_EXTENDED,
    comparisons,
    context,
    field,
)

from sdp.adapters.imaging.photutils_detector import PhotutilsDetector
from sdp.adapters.imaging.photutils_photometry import PhotutilsPhotometer
from sdp.adapters.imaging.tweakwcs_refiner import TweakwcsRefiner
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.investigation import NullFieldPlan, Stage, TestSelection
from sdp.kernel.ports.investigate import InvestigateFieldCommand
from sdp.testing.epochs import CAMERAS, OTHER_BAND, epoch_archive, epoch_catalogue, epoch_field, epoch_profile
from sdp.testing.investigate import gaia_archive, investigate_ports
from sdp.testing.jwst import GAIA, MAST
from sdp.testing.wiring import build_in_memory_kernel
from sdp_investigate.contradiction import CONTAMINATED, POOR_QUALITY, CrossSurveyContradiction, _clean_band
from sdp_investigate.contradiction import SURVEYS as KNOWN

pytestmark = [pytest.mark.tier("T4"), pytest.mark.req("INV-028")]

WITH_WISE: dict[str, Any] = {"source_id": "gaia", "surveys": {"2mass": "2mass", "ps1": "ps1", "allwise": "allwise"}}


@pytest.fixture(scope="module")
def wise() -> dict[str, Any]:
    test = CrossSurveyContradiction()
    return {str(r.subject.object_key): r for r in test.run(field(allwise=True), WITH_WISE, context())}


def test_allwise_adds_a_position_and_two_bands(wise: dict[str, Any]) -> None:
    star = comparisons(wise["gaia:DR3:30"])
    assert {"allwise:position", "allwise:W1", "allwise:W2"} <= set(star)
    assert star["allwise:W1"]["reported_sigma"] == pytest.approx(0.03)
    assert star["allwise:position"]["reported_sigma"] == pytest.approx(math.hypot(150.0, 0.04 * 5.44), rel=1e-3)
    assert star["allwise:position"]["epoch_gap_years"] == pytest.approx(2010.5558 - 2016.0)


def test_a_warm_dust_excess_in_both_wise_bands_is_flagged(wise: dict[str, Any]) -> None:
    result = wise[f"gaia:DR3:{WISE_EXCESS}"]
    assert result.p_value is not None and result.p_value < 1e-6
    assert set(result.details["flagged"]) == {"allwise:W1", "allwise:W2"}
    assert result.details["pattern"] == "all-bands-one-way"
    assert comparisons(result)["allwise:W1"]["z"] < -10


def test_a_band_allwise_flags_as_contaminated_is_set_aside_with_the_position(wise: dict[str, Any]) -> None:
    star = comparisons(wise[f"gaia:DR3:{WISE_CONTAMINATED}"])
    assert star["allwise:W1"]["set_aside"] == CONTAMINATED and not star["allwise:W1"]["counted"]
    assert star["allwise:position"]["set_aside"] == CONTAMINATED
    assert star["allwise:W2"]["counted"], "W2's own flag is clean"
    assert wise[f"gaia:DR3:{WISE_CONTAMINATED}"].p_value > 0.01


def test_a_row_allwise_fits_as_extended_is_set_aside_whole(wise: dict[str, Any]) -> None:
    star = comparisons(wise[f"gaia:DR3:{WISE_EXTENDED}"])
    allwise = [c for c in star.values() if c["survey"] == "allwise"]
    assert allwise and all(c["set_aside"] == POOR_QUALITY for c in allwise)
    assert wise[f"gaia:DR3:{WISE_EXTENDED}"].p_value > 0.01


def test_the_catalogue_contradictions_are_still_found_beside_allwise(wise: dict[str, Any]) -> None:
    flagged = {k for k, r in wise.items() if r.p_value is not None and r.p_value < 0.01}
    assert {f"gaia:DR3:{MOVED}", f"gaia:DR3:{KS_EXCESS}", f"gaia:DR3:{WISE_EXCESS}"} <= flagged


def test_null_fields_with_allwise_stay_near_the_nominal_rate() -> None:
    null = CrossSurveyContradiction().null(field(allwise=True), WITH_WISE, context(9), trials=20, alpha=0.01)
    assert null.false_positives / null.examined < 0.025


def test_a_contamination_letter_clears_only_a_zero_and_a_short_string_clears_nothing() -> None:
    wise = KNOWN["allwise"]
    assert _clean_band({"quality_flags": '{"cc_flags": "0d00"}'}, wise, 0)
    assert not _clean_band({"quality_flags": '{"cc_flags": "0d00"}'}, wise, 1)
    assert not _clean_band({"quality_flags": '{"cc_flags": "0"}'}, wise, 1)
    assert not _clean_band({"quality_flags": "{}"}, wise, 0)
    assert _clean_band({"quality_flags": "{}"}, KNOWN["2mass"], 0), "2MASS has no contamination string"
    assert _clean_band({"quality_flags": "{}"}, wise, None)


# ---- Hubble and Webb images ------------------------------------------------------------------------

BRIGHTER = "e-12"  # 0.7 mag brighter on the images than its Gaia colour predicts
DISPLACED = "e-20"  # 0.4 arcsec north on the images of where Gaia's motion puts it
IMAGES = (CAMERAS[2], OTHER_BAND)  # WFC3 F656N in 2014 and NIRCam F200W in 2022


def catalogues() -> tuple[pa.Table, pa.Table]:
    """Gaia's catalogue, with colours spread so a colour relation can be fitted, and the world the
    images show: the same stars, one brighter and one moved."""
    gaia = epoch_catalogue(stars=40)
    rng = np.random.default_rng(3)
    gaia = gaia.set_column(
        gaia.schema.get_field_index("bp_rp"), "bp_rp", pa.array(rng.uniform(0.5, 2.5, gaia.num_rows))
    )
    rows = gaia.to_pylist()
    for r in rows:
        if r["source_key"] == BRIGHTER:
            r["g_mag"] -= 0.7
        if r["source_key"] == DISPLACED:
            r["dec_deg"] += 0.4 / 3600
    return gaia, pa.Table.from_pylist(rows, schema=gaia.schema)


def run_images(trials: int = 5) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    gaia, seen = catalogues()
    ports = investigate_ports(CrossSurveyContradiction(), catalogue=gaia)
    ports.observation_gateways = {MAST: epoch_archive(seen, IMAGES)}
    ports.services = {
        "detector": PhotutilsDetector(),
        "refiner": TweakwcsRefiner(),
        "photometer": PhotutilsPhotometer(),
    }
    k = build_in_memory_kernel(gateways={GAIA: gaia_archive(gaia)}, investigate=ports)
    assert k.kernel.investigate_field is not None
    thresholds = {
        "source_id": GAIA,
        "surveys": {},
        "align_search_arcsec": 2.0,
        "align_separation_arcsec": 0.5,
        "align_tolerance_arcsec": 0.3,
        "anchor_sigma_arcsec": 0.01,
        "magnitude_bins": 1,
        "min_per_bin": 10,
    }
    selection = TestSelection("cross-survey-contradiction", "1.2.0", thresholds, needs_products=("image",))
    profile = replace(epoch_profile(), tests=(selection,), null_fields=NullFieldPlan(trials=trials))
    run = k.kernel.investigate_field.execute(InvestigateFieldCommand(epoch_field(), profile, seed=1))
    stage = run.investigation_set.stages[Stage.TEST].artifact_id
    assert stage is not None
    doc = decode_artifact(k.store.get(stage), JSON)
    return {r["subject"]["object_key"]: r for r in doc["results"]}, doc


@pytest.fixture(scope="module")
def run() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    return run_images(trials=100)


@pytest.fixture(scope="module")
def images(run: tuple[dict[str, dict[str, Any]], dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return run[0]


def test_null_fields_on_the_images_hold_the_rate_near_alpha(
    run: tuple[dict[str, dict[str, Any]], dict[str, Any]],
) -> None:
    [null] = run[1]["null_results"]
    assert null["trials"] == 100 and null["examined"] > 3000
    assert null["false_positive_rate"] < 0.025


def by_channel(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["channel"]: c for c in result["details"]["comparisons"]}


def test_each_image_adds_a_brightness_and_a_position_channel(images: dict[str, dict[str, Any]]) -> None:
    names = {c for r in images.values() for c in by_channel(r)}
    for camera in IMAGES:
        assert f"image:{camera.product_id}:{camera.band}" in names
        assert f"image:{camera.product_id}:position" in names
    surveys = {c["survey"] for r in images.values() for c in r["details"]["comparisons"]}
    assert surveys == {"image:WFC3", "image:NIRCAM"}


def test_a_star_brighter_on_the_images_is_flagged_in_both(images: dict[str, dict[str, Any]]) -> None:
    result = images[BRIGHTER]
    assert result["p_value"] < 1e-4
    bands = {f"image:{c.product_id}:{c.band}" for c in IMAGES}
    assert bands <= set(result["details"]["flagged"])
    for name in bands:
        assert by_channel(result)[name]["residual_mag"] == pytest.approx(-0.7, abs=0.1)


def test_a_star_displaced_on_the_images_is_flagged_by_position(images: dict[str, dict[str, Any]]) -> None:
    result = images[DISPLACED]
    assert result["p_value"] < 1e-4
    positions = {f"image:{c.product_id}:position" for c in IMAGES}
    assert positions & set(result["details"]["flagged"])
    for name in positions & set(by_channel(result)):
        c = by_channel(result)[name]
        if c["counted"]:
            assert c["offset_mas"][1] - c["field_offset_mas"][1] == pytest.approx(400.0, abs=60.0)


def test_steady_stars_on_the_images_are_not_flagged(images: dict[str, dict[str, Any]]) -> None:
    flagged = {k for k, r in images.items() if r["p_value"] is not None and r["p_value"] < 0.01}
    assert flagged <= {BRIGHTER, DISPLACED, "e-vanished", "e-brightens", "e-fades"}
    assert {BRIGHTER, DISPLACED} <= flagged


def test_a_pair_the_detector_could_not_split_is_a_blend_where_it_was_not_split(
    images: dict[str, dict[str, Any]],
) -> None:
    # 0.25 arcsec apart: the 2014 WFC3 detections merge them, NIRCam's 2022 ones do not
    hst, jwst = IMAGES
    for key in ("e-pair-a", "e-pair-b"):
        c = by_channel(images[key])
        assert c[f"image:{hst.product_id}:{hst.band}"]["set_aside"] == "blended"
        assert c[f"image:{hst.product_id}:position"]["set_aside"] == "blended"
        assert c[f"image:{jwst.product_id}:{jwst.band}"]["counted"]
        assert c[f"image:{jwst.product_id}:position"]["counted"]
        assert images[key]["p_value"] > 0.01, key


def test_a_neighbour_inside_an_image_s_aperture_is_a_blend() -> None:
    from scipy.spatial import cKDTree

    from sdp_investigate.contradiction import _Field, _unit

    ra = np.array([10.0, 10.0 + 0.15 / 3600.0, 10.0 + 0.5 / 3600.0])
    dec = np.zeros(3)
    g = np.array([16.0, 17.0, 16.0])
    stars = _Field((), ra, dec, g, np.zeros((3, 2)), np.full(3, 2016.0), cKDTree(_unit(ra, dec)), 2.5)
    assert stars.blended(0, 0.2, 2016.0) and stars.blended(1, 0.2, 2016.0)
    assert not stars.blended(2, 0.2, 2016.0)
    assert not stars.blended(0, 0.1, 2016.0)
    faint = _Field((), ra, dec, np.array([12.0, 17.0, 16.0]), np.zeros((3, 2)), np.full(3, 2016.0), stars.tree, 2.5)
    assert not faint.blended(0, 0.2, 2016.0), "a neighbour 5 mag fainter spoils nothing"
