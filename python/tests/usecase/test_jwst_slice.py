"""T4: the JWST vertical slice on the synthetic field (Integration v0.4.0 §7): predeclare, discover,
plan, approve, execute, identify, associate, resolve and dossier, with its definition of done."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from sdp.adapters.association import BayesFactorAssociation, LikelihoodRatioAssociation
from sdp.adapters.imaging.photutils_detector import PhotutilsDetector
from sdp.adapters.imaging.tweakwcs_refiner import TweakwcsRefiner
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.application.planning import cutout_bytes
from sdp.kernel.domain.integration import AcquisitionBudget, AcquisitionPlan, MatchStatus, Operation
from sdp.kernel.domain.investigation import Stage
from sdp.kernel.ports.integration import ApprovePlanCommand, ExecutePlanCommand, PlanInvestigationCommand
from sdp.testing.integration import gaia_descriptor, mast_descriptor
from sdp.testing.investigate import gaia_archive, investigate_ports, planning_discovery
from sdp.testing.jwst import (
    GAIA,
    MAST,
    PAIR_A,
    PRODUCT_ID,
    VANISHED,
    CuttingArchive,
    jwst_archive,
    jwst_catalogue,
    jwst_cutting_archive,
    jwst_field,
    jwst_i2d,
    jwst_profile,
    jwst_resolver,
)
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel
from sdp_investigate.imaging import ImageCounterpart

pytestmark = pytest.mark.tier("T4")
BUDGET = AcquisitionBudget(500_000_000, 10, 600.0, 0, 50_000_000)


def slice_kernel(archive: Any = None) -> InMemoryKernel:
    catalogue = jwst_catalogue()
    ports = investigate_ports(ImageCounterpart(), catalogue=catalogue, resolver=jwst_resolver(catalogue))
    ports.observation_gateways = {MAST: archive if archive is not None else jwst_archive(catalogue)}
    ports.services = {
        "detector": PhotutilsDetector(),
        "associators": (BayesFactorAssociation(), LikelihoodRatioAssociation()),
    }
    return build_in_memory_kernel(
        gateways={GAIA: gaia_archive(catalogue)},
        investigate=ports,
        discovery=planning_discovery(mast_descriptor(), gaia_descriptor()),
    )


def cutout_profile(side_arcsec: float = 25.0) -> Any:
    profile = jwst_profile()
    archives = tuple(replace(a, cutout_arcsec=side_arcsec) for a in profile.archives)
    return replace(profile, profile_id="jwst-vertical-slice-cutouts", archives=archives)


def run_slice(
    k: InMemoryKernel, plan: AcquisitionPlan | None = None, profile: Any = None
) -> tuple[AcquisitionPlan, dict[str, Any]]:
    kernel = k.kernel
    assert kernel.plan_investigation and kernel.approve_plan and kernel.execute_plan
    if plan is None:
        plan = kernel.plan_investigation.execute(
            PlanInvestigationCommand(jwst_field(), profile or jwst_profile(), BUDGET, seed=1)
        )
    else:
        with k.db.unit_of_work() as uow:
            uow.plans.add(plan)
            uow.commit()
    kernel.approve_plan.execute(ApprovePlanCommand(plan.plan_id, "leigh"))
    execution = kernel.execute_plan.execute(ExecutePlanCommand(plan.plan_id, "leigh"))
    with k.db.unit_of_work() as uow:
        record = uow.plans.get(plan.plan_id)
        assert record is not None and record.set_id is not None
        investigation_set = uow.investigations.get_set(record.set_id)
    assert investigation_set is not None
    stage = investigation_set.stages[Stage.TEST].artifact_id
    assert stage is not None
    results = decode_artifact(k.store.get(stage), JSON)["results"]
    return plan, {
        "execution": execution,
        "refused": [dict(e.event.payload)["refused"] for e in k.db.state.events if e.event.name == "PlanExecuted"],
        "set": investigation_set,
        "results": {r["subject"]["object_key"]: r for r in results},
    }


@pytest.fixture(scope="module")
def first() -> tuple[AcquisitionPlan, dict[str, Any]]:
    return run_slice(slice_kernel())


@pytest.mark.req("INT-013")
@pytest.mark.req("INT-020")
def test_discovery_and_the_plan_name_one_jwst_image_and_one_gaia_cone(
    first: tuple[AcquisitionPlan, dict[str, Any]],
) -> None:
    plan, _ = first
    image = next(r for r in plan.requirements if r.needs_bytes)
    satisfied = {
        m.source_id
        for m in plan.matches
        if m.requirement_id == image.requirement_id and m.status is MatchStatus.SATISFIED
    }
    assert satisfied == {MAST}
    assert sorted((i.source_id, i.operation) for i in plan.items) == [
        (GAIA, Operation.CONE),
        (MAST, Operation.DOWNLOAD),
    ]
    assert next(i for i in plan.items if i.source_id == MAST).target == PRODUCT_ID
    assert plan.gaps == () and not plan.requires_signed_approval


@pytest.mark.req("INT-015")
@pytest.mark.req("INT-020")
def test_execution_fetches_only_the_plan_and_the_slice_flags_the_missing_star(
    first: tuple[AcquisitionPlan, dict[str, Any]],
) -> None:
    plan, run = first
    execution = run["execution"]
    assert execution.unavailable == () and len(execution.acquired) == len(plan.items)
    assert run["refused"] == [[]], "Identify and Retrieve asked for nothing the plan does not name"
    assert run["set"].plan_artifact_id == plan.plan_id
    flagged = {key for key, r in run["results"].items() if r["p_value"] < 0.01}
    assert flagged == {VANISHED}


@pytest.mark.req("INT-009")
@pytest.mark.req("INT-020")
def test_the_graph_retains_an_ambiguity_and_nothing_is_accepted_or_verdicted(
    first: tuple[AcquisitionPlan, dict[str, Any]],
) -> None:
    _, run = first
    pair = run["results"][PAIR_A]["details"]
    assert pair["ambiguous"] and len(pair["counterparts"][0]["blend_of"]) == 2
    assert run["set"].to_document()["label"] == "EXPLORATORY"
    assert all("state" not in c or c["state"] == "proposed" for c in pair["counterparts"])


@pytest.mark.req("INT-013")
def test_the_run_reproduces_from_the_plan_artifact_alone(first: tuple[AcquisitionPlan, dict[str, Any]]) -> None:
    plan, run = first
    k = slice_kernel()
    again_plan, again = run_slice(k, AcquisitionPlan.from_document(plan.to_document() | {"approval": None}))
    assert again_plan.plan_id == plan.plan_id
    assert again["results"] == run["results"]


@pytest.mark.req("INT-004")
@pytest.mark.req("INT-015")
def test_a_cutout_profile_plans_and_cuts_one_product_for_the_whole_field() -> None:
    archive: CuttingArchive = jwst_cutting_archive()
    plan, run = run_slice(slice_kernel(archive), profile=cutout_profile(25.0))
    item = next(i for i in plan.items if i.source_id == MAST)
    assert item.operation is Operation.CUTOUT
    assert item.target.startswith(PRODUCT_ID + "@cutout(") and item.target.endswith(",25arcsec)")
    assert item.expected_bytes <= cutout_bytes(25.0)
    assert archive.cuts == [item.target] and archive.fetches == [PRODUCT_ID]
    assert archive.searches == 2, "one field-wide search to plan and one to retrieve, never one per star"
    assert run["refused"] == [[]], "Retrieve cut exactly the product the plan names"
    flagged = {key for key, r in run["results"].items() if r["p_value"] < 0.01}
    assert flagged == {VANISHED}


def test_a_gateway_that_cannot_cut_falls_back_to_whole_products_in_the_plan() -> None:
    plan, _ = run_slice(slice_kernel(), profile=cutout_profile(25.0))
    item = next(i for i in plan.items if i.source_id == MAST)
    assert item.operation is Operation.DOWNLOAD and item.target == PRODUCT_ID


def aligned_profile(align: bool) -> Any:
    profile = jwst_profile()
    tests = tuple(replace(t, thresholds={**t.thresholds, "align_wcs": align}) for t in profile.tests)
    return replace(profile, profile_id=f"jwst-vertical-slice-align-{align}".lower(), tests=tests)


@pytest.mark.req("INT-020")
def test_an_image_whose_astrometry_is_off_is_shifted_onto_gaia_before_it_is_compared() -> None:
    catalogue = jwst_catalogue()
    off = jwst_archive(catalogue, images=((PRODUCT_ID, jwst_i2d(catalogue, wcs_error_arcsec=(1.5, -0.8))),))

    def flagged(align: bool) -> tuple[set[str], dict[str, Any]]:
        k = slice_kernel(replace(off, fetches=[]))
        use_case = k.kernel.investigate_field
        assert use_case is not None
        use_case.ports.services = {**use_case.ports.services, "refiner": TweakwcsRefiner()}
        _, run = run_slice(k, profile=aligned_profile(align))
        return {key for key, r in run["results"].items() if r["p_value"] < 0.01}, run["results"]

    unaligned, _ = flagged(False)
    assert len(unaligned) > 3, "an arcsecond off, the stars have no counterparts where Gaia puts them"
    aligned, results = flagged(True)
    assert aligned == {VANISHED}
    fit = next(r["details"]["alignment"] for r in results.values() if "alignment" in r["details"])
    assert fit["matches"] >= 5 and fit["rms_arcsec"] < 0.1
