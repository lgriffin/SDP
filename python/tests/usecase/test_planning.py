"""T4: PlanInvestigation, ApprovePlan and ExecutePlan around Retrieve, with the kernel wired to fakes
(INT-013 to INT-015, ADR-020)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest

from sdp.contracts import validate
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.application.planning import PLANNER_ID, derive_requirements, plan_path, plan_view
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.integration import (
    AcquisitionBudget,
    AcquisitionPlan,
    ApprovalDecision,
    IntegrationRefused,
    Operation,
    ProductType,
    select_items,
)
from sdp.kernel.domain.investigation import Field, TestProfile
from sdp.kernel.ports.integration import (
    ApprovePlanCommand,
    ExecutePlanCommand,
    PlanExecution,
    PlanInvestigationCommand,
)
from sdp.testing.fakes.archives import FakeArchive
from sdp.testing.fakes.investigate import FakeObservationArchive
from sdp.testing.investigate import (
    ProperMotionExcessTest,
    gaia_archive,
    investigate_ports,
    observation_archive,
    planning_descriptors,
    planning_discovery,
    synthetic_catalogue,
    synthetic_field,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T4")

LIGHTCURVES = ("lightcurve",)


def budget(threshold: int = 50_000_000, max_bytes: int = 1_000_000_000) -> AcquisitionBudget:
    return AcquisitionBudget(
        max_bytes=max_bytes,
        max_requests_per_source=50,
        max_wall_seconds=3600.0,
        min_nas_headroom_bytes=0,
        approval_threshold_bytes=threshold,
    )


@dataclass
class World:
    k: InMemoryKernel
    gaia: FakeArchive
    twomass: FakeArchive
    plates: FakeObservationArchive
    profile: TestProfile
    field: Field

    def plan(self, **kw: object) -> AcquisitionPlan:
        assert self.k.kernel.plan_investigation is not None
        return self.k.kernel.plan_investigation.execute(
            PlanInvestigationCommand(self.field, self.profile, kw.pop("budget", budget()), seed=11, **kw)  # type: ignore[arg-type]
        )

    def approve(self, plan_id: str, **kw: object) -> AcquisitionPlan:
        assert self.k.kernel.approve_plan is not None
        return self.k.kernel.approve_plan.execute(ApprovePlanCommand(plan_id, kw.pop("actor", "leigh"), **kw))  # type: ignore[arg-type]

    def execute(self, plan_id: str) -> PlanExecution:
        assert self.k.kernel.execute_plan is not None
        return self.k.kernel.execute_plan.execute(ExecutePlanCommand(plan_id, "leigh"))

    def events(self, name: str) -> list[dict[str, Any]]:
        return [dict(e.event.payload) for e in self.k.db.state.events if e.event.name == name]


def make_world(needs_products: tuple[str, ...] = (), *, descriptors: tuple[object, ...] | None = None) -> World:
    catalogue = synthetic_catalogue()
    gaia, twomass = gaia_archive(catalogue), twomass_archive(catalogue)
    plates = observation_archive(catalogue)
    k = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia, "synthetic-2mass": twomass},
        investigate=investigate_ports(
            ProperMotionExcessTest(needs_products=needs_products), catalogue=catalogue, observations=plates
        ),
        discovery=planning_discovery(*(descriptors or ())),  # type: ignore[arg-type]
    )
    return World(k, gaia, twomass, plates, synthetic_profile(needs_products=needs_products), synthetic_field())


# -- PlanInvestigation ------------------------------------------------------------------------------


@pytest.mark.req("INT-013")
def test_a_plan_names_every_item_its_size_purpose_and_access_before_anything_is_acquired() -> None:
    world = make_world(LIGHTCURVES)
    plan = world.plan()
    assert world.gaia.fetch_calls == 0 and world.twomass.fetch_calls == 0 and world.plates.fetches == []
    assert world.plates.searches == 1, "products are listed from the index over the field, once"
    assert plan.planner_id == PLANNER_ID and plan.profile_hash == world.profile.profile_hash
    by_source: dict[str, list[str]] = {}
    for item in plan.items:
        by_source.setdefault(item.source_id, []).append(item.target)
        assert item.expected_bytes > 0 and item.requirement_id and item.test_kind and item.licence
        assert item.access_status.value == "public"
    assert sorted(by_source["fake-plates"]) == sorted(f"lc-{i}" for i in range(5)), "only what a test reads"
    assert len(by_source["synthetic-gaia"]) == 1 and len(by_source["synthetic-2mass"]) == 1
    assert plan.gaps == ()
    assert plan.cost.total_bytes == sum(i.expected_bytes for i in plan.items)
    assert plan.cost.requests_per_source == {"fake-plates": 5, "synthetic-2mass": 1, "synthetic-gaia": 1}
    assert validate("acquisition-plan", plan.to_document()) == []


@pytest.mark.req("INT-013")
def test_the_plan_artifact_is_content_addressed_by_the_plan_id_and_planning_again_changes_nothing() -> None:
    world = make_world(LIGHTCURVES)
    plan = world.plan()
    artifact = world.k.store.resolve(plan_path(plan.plan_id))
    assert artifact == ArtifactId(plan.plan_id)
    assert decode_artifact(world.k.store.get(artifact), JSON) == plan.intent_document()
    assert world.plan().plan_id == plan.plan_id
    assert len(world.events("PlanDrafted")) == 1
    assert AcquisitionPlan.from_document(plan.to_document()) == plan


@pytest.mark.req("INT-013")
def test_requirements_come_from_the_profile_tests_so_a_test_that_needs_no_products_fetches_none() -> None:
    world = make_world()
    plan = world.plan()
    assert [i.source_id for i in plan.items] == ["synthetic-2mass", "synthetic-gaia"]
    needs = {r.requirement_id: r for r in plan.requirements}
    assert set(needs) == {"identify:catalogue:synthetic-gaia", "pm-excess:catalogue:synthetic-gaia"}
    with_products = derive_requirements(
        world.field, synthetic_profile(needs_products=LIGHTCURVES), [ProperMotionExcessTest(needs_products=LIGHTCURVES)]
    )
    product = next(r for r in with_products if r.needs_bytes)
    assert product.requirement_id == "pm-excess:product:lightcurve"
    assert product.product_types == {ProductType.TIMESERIES}


@pytest.mark.req("INT-013")
def test_a_source_without_a_registered_descriptor_is_a_gap_not_an_item() -> None:
    gaia, _, plates = planning_descriptors()
    world = make_world(LIGHTCURVES, descriptors=(gaia,))
    plan = world.plan()
    assert {i.source_id for i in plan.items} == {"synthetic-gaia"}
    gap = next(g for g in plan.gaps if g.requirement_id == "pm-excess:product:lightcurve")
    assert gap.reason == "no source that lists it satisfies it: fake-plates (no registered descriptor)"
    assert plates.adapter is not None
    # Not yet admitted: planned for exploration, with the reason on the match (verdicts stay refused, INT-016).
    unadmitted = replace(plates, adapter=replace(plates.adapter, conformance={}))
    plan = make_world(LIGHTCURVES, descriptors=(gaia, unadmitted)).plan()
    assert "fake-plates" in {i.source_id for i in plan.items}
    assert not any(g.requirement_id == "pm-excess:product:lightcurve" for g in plan.gaps)
    match = next(
        m for m in plan.matches if m.source_id == "fake-plates" and m.requirement_id == "pm-excess:product:lightcurve"
    )
    assert match.status.value == "partial"
    assert any(r.field == "adapter.conformance" and r.outcome.value == "partial" for r in match.reasons)


def test_a_plan_over_the_byte_budget_cannot_be_drafted() -> None:
    world = make_world(LIGHTCURVES)
    with pytest.raises(IntegrationRefused, match="byte budget"):
        world.plan(budget=budget(threshold=1000, max_bytes=1000))
    assert world.events("PlanDrafted") == []


def test_the_harness_rederives_the_items_from_requirements_matches_and_candidates() -> None:
    from sdp.kernel.domain.integration import PlanItem

    plan = make_world(LIGHTCURVES).plan()
    candidates = [PlanItem.from_document(c) for c in plan.constraints["candidates"]]
    items, gaps = select_items(plan.requirements, plan.matches, candidates)
    assert items == plan.items and gaps == plan.gaps


# -- ApprovePlan ------------------------------------------------------------------------------------


@pytest.mark.req("INT-014")
def test_below_the_threshold_a_plan_is_approved_automatically_and_the_decision_recorded() -> None:
    world = make_world()
    plan = world.plan()
    assert not plan.requires_signed_approval
    approved = world.approve(plan.plan_id)
    assert approved.approval is not None and approved.approval.decision is ApprovalDecision.AUTO_APPROVED
    assert approved.plan_id == plan.plan_id, "approving a plan does not change which plan it is"
    assert world.events("PlanApproved")[0]["decision"] == "auto_approved"
    record = world.k.kernel.query_plans.plan(plan.plan_id)  # type: ignore[union-attr]
    assert record is not None and record.state == "auto_approved"
    with pytest.raises(IntegrationRefused, match="already auto_approved"):
        world.approve(plan.plan_id)


@pytest.mark.req("INT-014")
def test_above_the_threshold_only_a_signed_ledger_approval_of_this_plan_by_this_actor_approves_it() -> None:
    world = make_world(LIGHTCURVES)
    plan = world.plan(budget=budget(threshold=1000))
    assert plan.requires_signed_approval
    with pytest.raises(IntegrationRefused, match="signed ledger approval is required"):
        world.approve(plan.plan_id)
    other = world.k.ledger.append(
        kind="approval", subject_id="plan", subject_hash="sha256:" + "0" * 64, signer="leigh", payload={}
    )
    with pytest.raises(IntegrationRefused, match="does not approve plan"):
        world.approve(plan.plan_id, ledger_entry_id=other.entry_id)
    entry = world.k.ledger.append(
        kind="approval", subject_id="plan", subject_hash=plan.plan_id, signer="leigh", payload={}
    )
    with pytest.raises(IntegrationRefused, match="another actor"):
        world.approve(plan.plan_id, actor="someone-else")
    approved = world.approve(plan.plan_id)
    assert approved.approval is not None and approved.approval.decision is ApprovalDecision.APPROVED
    assert approved.approval.ledger_entry_id == entry.entry_id


@pytest.mark.req("INT-014")
def test_a_refused_plan_records_why_and_cannot_be_executed() -> None:
    world = make_world()
    plan = world.plan()
    refused = world.approve(plan.plan_id, refuse_reason="not this field")
    assert refused.approval is not None and refused.approval.reason == "not this field"
    assert world.events("PlanRefused")[0]["reason"] == "not this field"
    with pytest.raises(IntegrationRefused, match="not approved"):
        world.execute(plan.plan_id)


# -- ExecutePlan ------------------------------------------------------------------------------------


@pytest.mark.req("INT-015")
def test_an_unapproved_plan_acquires_nothing() -> None:
    world = make_world()
    plan = world.plan()
    with pytest.raises(IntegrationRefused, match="not approved"):
        world.execute(plan.plan_id)
    assert world.gaia.fetch_calls == 0


@pytest.mark.req("INT-015")
def test_execution_acquires_every_item_and_the_run_names_its_plan() -> None:
    world = make_world(LIGHTCURVES)
    plan = world.approve(world.plan().plan_id)
    execution = world.execute(plan.plan_id)
    assert execution.unavailable == ()
    assert len(execution.acquired) == len(plan.items) == 7
    assert sorted(world.plates.fetches) == sorted(f"lc-{i}" for i in range(5))
    record = world.k.kernel.query_plans.plan(plan.plan_id)  # type: ignore[union-attr]
    assert record is not None and record.state == "executed" and record.set_id is not None
    sets = world.k.kernel.query_investigations.sets()  # type: ignore[union-attr]
    assert [s.plan_artifact_id for s in sets] == [plan.plan_id]
    manifest = world.k.kernel.query_investigations.manifest(record.set_id)  # type: ignore[union-attr]
    assert manifest is not None and manifest["plan_artifact_id"] == plan.plan_id
    executed = world.events("PlanExecuted")[0]
    assert executed["set_id"] == record.set_id and executed["refused"] == []
    view = plan_view(record)
    assert view["state"] == "executed" and view["execution"] == execution.to_document()
    with pytest.raises(IntegrationRefused, match="already executed"):
        world.execute(plan.plan_id)


@pytest.mark.req("INT-015")
def test_an_acquisition_the_plan_does_not_name_is_refused_and_recorded() -> None:
    world = make_world(LIGHTCURVES)
    plan = world.approve(world.plan().plan_id)
    # A light curve that appears after planning: Retrieve finds it, the plan never named it.
    late = next(p for p in world.plates.records if p.record.product.product_id == "lc-0")
    late = replace(late, record=replace(late.record, product=replace(late.record.product, product_id="lc-late")))
    world.plates.records += (late,)
    world.plates.products["lc-late"] = world.plates.products["lc-0"]
    execution = world.execute(plan.plan_id)
    assert "lc-late" not in world.plates.fetches
    refused = world.events("PlanExecuted")[0]["refused"]
    assert [(r["source_id"], r["operation"], r["target"]) for r in refused] == [
        ("fake-plates", Operation.DOWNLOAD.value, "lc-late")
    ]
    assert "INT-015" in refused[0]["reason"]
    assert execution.unavailable == ()


@pytest.mark.req("INT-015")
def test_an_item_the_run_cannot_find_is_listed_as_unavailable() -> None:
    world = make_world(LIGHTCURVES)
    plan = world.approve(world.plan().plan_id)
    world.plates.records = tuple(p for p in world.plates.records if p.record.product.product_id != "lc-3")
    execution = world.execute(plan.plan_id)
    missing = next(i for i in plan.items if i.target == "lc-3")
    assert execution.unavailable == (missing.item_id,)
    assert len(execution.acquired) == len(plan.items) - 1


@pytest.mark.req("INT-015")
def test_a_catalogue_the_plan_left_out_is_not_queried_and_the_run_still_completes() -> None:
    gaia, _, plates = planning_descriptors()
    world = make_world(descriptors=(gaia, plates))
    plan = world.approve(world.plan().plan_id)
    assert all(i.source_id != "synthetic-2mass" for i in plan.items)
    world.execute(plan.plan_id)
    assert world.twomass.fetch_calls == 0
    refused = world.events("PlanExecuted")[0]["refused"]
    assert [r["source_id"] for r in refused] == ["synthetic-2mass"]


@pytest.mark.req("INT-015")
def test_executing_the_same_plan_in_two_clean_worlds_yields_the_same_artifact_ids() -> None:
    first = make_world(LIGHTCURVES)
    plan = first.approve(first.plan().plan_id)
    second = make_world(LIGHTCURVES)
    second.k.db.state.plans[plan.plan_id] = first.k.db.state.plans[plan.plan_id]
    assert first.execute(plan.plan_id).acquired == second.execute(plan.plan_id).acquired
