"""T4: the image-counterpart test on the synthetic JWST field, through the kernel with photutils and
both association methods (Integration v0.4.0 §7, INT-020)."""

from __future__ import annotations

from typing import Any

import pytest
from astropy.io import fits

from sdp.adapters.association import BayesFactorAssociation, LikelihoodRatioAssociation
from sdp.adapters.imaging.photutils_detector import PhotutilsDetector, header_epoch_mjd
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.errors import PlanInvalid
from sdp.kernel.domain.investigation import Stage
from sdp.kernel.ports.investigate import DetectionList, ImageFrame, InvestigateFieldCommand
from sdp.testing.investigate import gaia_archive, investigate_ports
from sdp.testing.jwst import (
    FAST,
    GAIA,
    IMAGE_MJD,
    MAST,
    PAIR_A,
    PAIR_B,
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
from sdp_investigate.imaging import MISSING_STATISTIC, ImageCounterpart, epoch_jyear

pytestmark = pytest.mark.tier("T4")


def services() -> dict[str, Any]:
    return {"detector": PhotutilsDetector(), "associators": (BayesFactorAssociation(), LikelihoodRatioAssociation())}


@pytest.fixture(scope="module")
def results() -> dict[str, dict[str, Any]]:
    catalogue = jwst_catalogue()
    ports = investigate_ports(ImageCounterpart(), catalogue=catalogue, resolver=jwst_resolver(catalogue))
    ports.observation_gateways = {MAST: jwst_archive(catalogue)}
    ports.services = services()
    k = build_in_memory_kernel(gateways={GAIA: gaia_archive(catalogue)}, investigate=ports)
    assert k.kernel.investigate_field is not None
    run = k.kernel.investigate_field.execute(InvestigateFieldCommand(jwst_field(), jwst_profile(), seed=1))
    stage = run.investigation_set.stages[Stage.TEST].artifact_id
    assert stage is not None
    return {r["subject"]["object_key"]: r for r in decode_artifact(k.store.get(stage), JSON)["results"]}


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
