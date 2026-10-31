"""T4: the epoch-change test on a synthetic field imaged by four cameras over 27 years, through the
kernel with photutils, tweakwcs and forced photometry (INT-025). The world is sdp.testing.epochs: each
image has its own pixel scale, PSF, zero point and WCS error, and three stars are planted to change."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from sdp.adapters.association import BayesFactorAssociation, LikelihoodRatioAssociation
from sdp.adapters.imaging.photutils_detector import PhotutilsDetector
from sdp.adapters.imaging.photutils_photometry import PhotutilsPhotometer
from sdp.adapters.imaging.tweakwcs_refiner import TweakwcsRefiner
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.investigation import NullFieldPlan, Stage
from sdp.kernel.ports.compute import TaskFailed
from sdp.kernel.ports.investigate import InvestigateFieldCommand
from sdp.testing.epochs import (
    BRIGHTENS,
    CAMERAS,
    FADES,
    FAST,
    OTHER_BAND,
    PAIR_A,
    PAIR_B,
    PLANTED_CHANGES,
    VANISHED,
    Camera,
    epoch_archive,
    epoch_catalogue,
    epoch_field,
    epoch_profile,
)
from sdp.testing.investigate import gaia_archive, investigate_ports
from sdp.testing.jwst import GAIA, MAST
from sdp.testing.wiring import build_in_memory_kernel
from sdp_investigate.changes import (
    APPEARED,
    CHANGED,
    STEADY,
    EpochChange,
    Series,
    aperture,
    blend_groups,
    enclosed,
    enclosed_offset,
    field_scale,
    verdict,
    zero_point,
)

pytestmark = pytest.mark.tier("T4")


def services() -> dict[str, Any]:
    return {
        "detector": PhotutilsDetector(),
        "refiner": TweakwcsRefiner(),
        "photometer": PhotutilsPhotometer(),
        "associators": (BayesFactorAssociation(), LikelihoodRatioAssociation()),
    }


def run_field(
    cameras: tuple[Camera, ...] = (*CAMERAS, OTHER_BAND), trials: int = 5, **thresholds: object
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    catalogue = epoch_catalogue()
    ports = investigate_ports(EpochChange(), catalogue=catalogue)
    ports.observation_gateways = {MAST: epoch_archive(catalogue, cameras)}
    ports.services = services()
    k = build_in_memory_kernel(gateways={GAIA: gaia_archive(catalogue)}, investigate=ports)
    assert k.kernel.investigate_field is not None
    profile = replace(epoch_profile(**thresholds), null_fields=NullFieldPlan(trials=trials))
    run = k.kernel.investigate_field.execute(InvestigateFieldCommand(epoch_field(), profile, seed=1))
    stage = run.investigation_set.stages[Stage.TEST].artifact_id
    assert stage is not None
    doc = decode_artifact(k.store.get(stage), JSON)
    return {r["subject"]["object_key"]: r for r in doc["results"]}, doc


@pytest.fixture(scope="module")
def field() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    return run_field(trials=200)


@pytest.mark.req("INT-025")
def test_the_planted_changes_are_flagged_and_nothing_else(
    field: tuple[dict[str, dict[str, Any]], dict[str, Any]],
) -> None:
    results, _ = field
    flagged = {key for key, r in results.items() if r["p_value"] < 0.01}
    assert flagged == PLANTED_CHANGES
    assert {key for key, r in results.items() if r["details"]["verdict"] != STEADY} == PLANTED_CHANGES
    brightens = results[BRIGHTENS]["details"]
    assert brightens["verdict"] == CHANGED
    assert brightens["largest_change"]["delta_mag"] == pytest.approx(-2.5 * math.log10(2.0), abs=0.02)
    fades = results[FADES]["details"]
    assert fades["verdict"] == CHANGED and fades["largest_change"]["delta_mag"] == pytest.approx(0.5, abs=0.02)
    assert fades["largest_change"]["to"] == CAMERAS[-1].product_id
    assert results[FADES]["effect_size"] == pytest.approx(0.5, abs=0.02)


@pytest.mark.req("INT-025")
def test_a_vanished_star_is_called_vanished_with_an_upper_limit(
    field: tuple[dict[str, dict[str, Any]], dict[str, Any]],
) -> None:
    results, _ = field
    gone = results[VANISHED]["details"]
    assert gone["verdict"] == "vanished" and gone["largest_change"]["delta_mag"] is None
    last = gone["epochs"][-1]
    assert last["image"] == CAMERAS[-1].product_id and abs(last["snr"]) < 2
    assert last["upper_limit_5sigma"] > 0
    assert all(e["snr"] > 100 for e in gone["epochs"][:-1])
    assert results[VANISHED]["effect_size"] is None


@pytest.mark.req("INT-025")
@pytest.mark.req("INT-010")
def test_a_fast_star_is_measured_where_it_is_on_each_date(
    field: tuple[dict[str, dict[str, Any]], dict[str, Any]],
) -> None:
    results, _ = field
    fast = results[FAST]
    assert fast["details"]["verdict"] == STEADY and fast["p_value"] > 0.01
    assert len(fast["details"]["epochs"]) == len(CAMERAS)
    assert fast["details"]["baseline_years"] == pytest.approx(27.4, abs=0.1)


@pytest.mark.req("INT-025")
def test_a_pair_one_camera_splits_and_another_blends_is_measured_as_one_blend(
    field: tuple[dict[str, dict[str, Any]], dict[str, Any]],
) -> None:
    results, _ = field
    assert PAIR_B not in results, "the blend is reported once, under its brighter member"
    pair = results[PAIR_A]
    assert pair["details"]["blend_of"] == [PAIR_A, PAIR_B]
    assert pair["details"]["verdict"] == STEADY and pair["p_value"] > 0.01
    assert pair["details"]["comparison"]["blends"] == [[PAIR_A, PAIR_B]]


@pytest.mark.req("INT-025")
def test_every_image_is_aligned_sized_and_calibrated_before_any_comparison(
    field: tuple[dict[str, dict[str, Any]], dict[str, Any]],
) -> None:
    results, _ = field
    comparison = results[FADES]["details"]["comparison"]
    assert comparison["band"] == "F656N" and comparison["filters"] == ["F656N"]
    images = {i["image"]: i for i in comparison["images"]}
    assert set(images) == {c.product_id for c in CAMERAS}
    for c in CAMERAS:
        got = images[c.product_id]
        assert got["alignment"]["matches"] >= 20 and got["alignment"]["rms_arcsec"] < 0.02
        assert got["fwhm_arcsec"] == pytest.approx(c.fwhm_px * c.pixel_arcsec, rel=0.05)
    coarsest = max(c.fwhm_px * c.pixel_arcsec for c in CAMERAS)
    assert comparison["aperture_arcsec"] == pytest.approx(coarsest, rel=0.05)
    # the reference is the deepest image; every other image's zero point is its count ratio to it, in
    # magnitudes, plus the constant share of light the fixed aperture loses on a blurrier image
    reference = next(c for c in CAMERAS if c.product_id == comparison["reference"])
    radius = comparison["aperture_arcsec"]
    for c in CAMERAS:
        got = images[c.product_id]
        share = enclosed(radius, c.fwhm_px * c.pixel_arcsec) / enclosed(
            radius, reference.fwhm_px * reference.pixel_arcsec
        )
        expected = -2.5 * math.log10(c.zp_factor / reference.zp_factor * share)
        # to 0.02 mag: the coarse camera samples its stars on few pixels
        assert got["zero_point_mag"] == pytest.approx(expected, abs=0.02)
        if c is not reference:
            assert got["calibrators"] >= 15 and 0 < got["zero_point_err_mag"] < 0.01


@pytest.mark.req("INT-025")
def test_an_image_in_a_band_no_other_image_shares_is_left_out(
    field: tuple[dict[str, dict[str, Any]], dict[str, Any]],
) -> None:
    results, _ = field
    images = {i["image"] for i in results[FADES]["details"]["comparison"]["images"]}
    assert OTHER_BAND.product_id not in images
    assert results[FADES]["details"]["images_left_out"] == {OTHER_BAND.product_id: "the only image in band F200W"}
    assert all(r["details"]["bands"] == 1 for r in results.values())


@pytest.mark.req("INT-025")
def test_the_null_fields_hold_the_false_positive_rate_near_alpha(
    field: tuple[dict[str, dict[str, Any]], dict[str, Any]],
) -> None:
    _, doc = field
    [null] = doc["null_results"]
    assert null["trials"] == 200 and null["examined"] == 200 * 25
    assert 0.005 < null["false_positive_rate"] < 0.02


@pytest.mark.req("INT-025")
def test_an_image_that_cannot_be_aligned_to_gaia_is_left_out() -> None:
    lost = replace(
        CAMERAS[1], product_id="hst-2006-acs-f656n_drc.fits", date="2006-06-10T00:00:00", wcs_error_arcsec=(4.0, 0.0)
    )
    results, _ = run_field(cameras=(*CAMERAS, lost))
    fades = results[FADES]["details"]
    assert fades["images_left_out"] == {lost.product_id: "could not be aligned to Gaia"}
    assert lost.product_id not in {i["image"] for i in fades["comparison"]["images"]}
    assert {key for key, r in results.items() if r["p_value"] < 0.01} == PLANTED_CHANGES


@pytest.mark.req("INT-025")
def test_joined_bands_need_a_declared_systematic_and_then_are_compared() -> None:
    bands = {"F656N": "halpha", "F200W": "halpha"}
    with pytest.raises(TaskFailed, match="band_systematic_mag"):
        run_field(bands=bands)
    results, _ = run_field(bands=bands, band_systematic_mag=0.05)
    comparison = results[FADES]["details"]["comparison"]
    assert comparison["band"] == "halpha" and comparison["filters"] == ["F200W", "F656N"]
    assert len(comparison["images"]) == len(CAMERAS) + 1
    # a 5% band systematic hides nothing as large as the planted changes
    assert {key for key, r in results.items() if r["p_value"] < 0.01} >= PLANTED_CHANGES


@pytest.mark.req("INT-025")
def test_one_image_alone_compares_nothing() -> None:
    results, _ = run_field(cameras=CAMERAS[:1])
    assert results == {}


# ---------------------------------------------------------------------------------------------
# The parts, one at a time


def test_the_zero_point_clips_a_variable_calibrator() -> None:
    rng = np.random.default_rng(1)
    delta = np.concatenate([rng.normal(0.3, 0.01, 30), [1.5]])
    zp, err, n = zero_point(delta)
    assert zp == pytest.approx(0.3, abs=0.01) and 27 <= n <= 30
    assert err == pytest.approx(math.sqrt(math.pi / 2) * 0.01 / math.sqrt(30), rel=0.5)


def test_sources_closer_than_the_limit_on_any_image_are_one_blend() -> None:
    near = 0.5 / 3600
    first = {"a": (10.0, 0.0), "b": (10.0 + 3 * near, 0.0), "c": (10.0, 0.01)}
    second = {"a": (10.0, 0.0), "b": (10.0 + near, 0.0), "c": (10.0, 0.01)}
    assert blend_groups([first], 1.0) == [["a"], ["b"], ["c"]]
    assert blend_groups([first, second], 1.0) == [["a", "b"], ["c"]]


def test_the_aperture_and_annulus_scale_with_the_coarsest_fwhm() -> None:
    r, (inner, outer) = aperture(0.3, 1.0)
    assert (r, inner, outer) == pytest.approx((0.3, 0.75, 1.35))
    assert enclosed(1.0, 1.0) == pytest.approx(1 - 2**-4)


@pytest.mark.parametrize(
    ("snr", "p", "expected"),
    [
        ([50, 50, 50], 0.5, STEADY),
        ([0.5, 1.0, 40.0], 1e-9, APPEARED),
        ([40.0, 30.0, 0.1], 1e-9, "vanished"),
        ([40.0, 0.1, 40.0], 1e-9, CHANGED),
        ([40.0, 80.0, 40.0], 1e-9, CHANGED),
    ],
)
def test_the_verdict(snr: list[float], p: float, expected: str) -> None:
    assert verdict(p, 0.01, np.array(snr), 5.0) == expected


def test_the_field_scale_is_one_for_right_errors_whatever_the_number_of_images() -> None:
    rng = np.random.default_rng(3)
    for n in (2, 3, 6):
        series = [
            Series(
                f"s{i}",
                "F",
                tuple(f"i{j}" for j in range(n)),
                np.arange(n, dtype=float),
                100 + rng.normal(0, 1, n),
                np.ones(n),
                np.zeros(n),
                0.0,
            )
            for i in range(400)
        ]
        scale = field_scale(series, [s.err for s in series], "field")
        assert scale == pytest.approx(1.0, abs=0.08)
        inflated = [replace(s, flux=100 + (s.flux - 100) * 2) for s in series]
        assert field_scale(inflated, [s.err for s in inflated], "field") == pytest.approx(2.0, rel=0.1)
    assert field_scale(series[:2], [s.err for s in series[:2]], "field") == 1.0
    assert field_scale(series, [s.err for s in series], 1.5) == 1.5
    with pytest.raises(ValueError, match="error_scale"):
        field_scale(series, [s.err for s in series], "nonsense")


def test_a_star_off_the_apertures_centre_puts_less_light_in_it() -> None:
    assert enclosed_offset(0.3, 0.0, 0.2) == pytest.approx(enclosed(0.3, 0.2))
    assert enclosed_offset(0.3, 0.1, 0.2) < enclosed(0.3, 0.2)
    # a blurrier image loses less of an offset star from a wide aperture than a sharp one gains
    assert enclosed_offset(1.0, 0.5, 0.05) == pytest.approx(1.0, abs=1e-6)


def test_a_source_compared_in_two_bands_pays_for_both() -> None:
    def series(band: str, flux: list[float]) -> Series:
        n = len(flux)
        return Series("s", band, tuple(f"{band}{i}" for i in range(n)), np.arange(n, dtype=float),
                      np.array(flux), np.ones(n), np.zeros(n), 0.0)  # fmt: skip

    from sdp_investigate.base import Features

    one = Features(("s",), {"series": [series("A", [100.0, 103.9])], "frames": [], "left_out": {}})
    two = Features(("s", "s"), {"series": [series("A", [100.0, 103.9]), series("B", [100.0, 100.0])],
                                "frames": [], "left_out": {}})  # fmt: skip
    thresholds = {"error_scale": 1.0, "alpha": 0.01}
    [a] = EpochChange().evaluate(one, thresholds, np.random.default_rng(0))
    [b] = EpochChange().evaluate(two, thresholds, np.random.default_rng(0))
    assert a.p_value is not None and b.p_value == pytest.approx(2 * a.p_value) and b.details["bands"] == 2
    assert a.details["verdict"] == CHANGED and b.details["verdict"] == STEADY
    # each band is read at the adjusted level, so the bands never disagree with the verdict at the top
    assert [d["verdict"] for d in b.details["per_band"]] == [STEADY, STEADY]
    assert b.details["per_band"][0]["p_adjusted"] == pytest.approx(b.p_value)
