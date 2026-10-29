"""T1: the acquisition plan in full: the approval each decision records, the exact refusal each
budget and approval rule gives, the documents a plan and its parts write, and the order the
minimum product set is chosen in (INT-013 to INT-015).

The behaviour is in test_integration.py; these pin the details mutation testing showed nothing
else checked (docs/testing.md, "Killing a survivor")."""

from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Any

import pytest

from sdp.kernel.domain.errors import IntegrityError, InvariantViolation
from sdp.kernel.domain.integration import (
    AccessStatus,
    AcquisitionBudget,
    AcquisitionPlan,
    ApprovalDecision,
    IntegrationRefused,
    MatchStatus,
    Operation,
    PlanApproval,
    PlanCost,
    PlanGap,
    PlanGate,
    PlanItem,
    ProductType,
    select_items,
)
from sdp.testing.integration import ledger_approval, sample_plan

pytestmark = pytest.mark.tier("T1")

THRESHOLD = 10_000_000


def exactly(error: type[Exception], message: str) -> Any:
    return pytest.raises(error, match="^" + re.escape(message) + "$")


@pytest.mark.req("INT-014")
def test_each_decision_records_the_actor_threshold_and_ledger_entry() -> None:
    small = sample_plan(image_bytes=1_000)
    assert small.decide(actor="kernel").approval == PlanApproval(
        small.plan_id, ApprovalDecision.AUTO_APPROVED, "kernel", THRESHOLD
    )
    witnessed = small.decide(actor="leigh", ledger_entry=ledger_approval(small))
    assert witnessed.approval == PlanApproval(
        small.plan_id, ApprovalDecision.AUTO_APPROVED, "leigh", THRESHOLD, "ledger-42"
    )
    big = sample_plan(image_bytes=50_000_000)
    assert big.decide(actor="leigh", ledger_entry=ledger_approval(big)).approval == PlanApproval(
        big.plan_id, ApprovalDecision.APPROVED, "leigh", THRESHOLD, "ledger-42"
    )
    refused = big.decide(actor="leigh", ledger_entry=ledger_approval(big), refuse_reason="too large")
    assert refused.approval == PlanApproval(
        big.plan_id, ApprovalDecision.REFUSED, "leigh", THRESHOLD, "ledger-42", "too large"
    )
    assert not refused.approval.approves


@pytest.mark.req("INT-014")
def test_decisions_refuse_saying_which_entry_and_how_many_bytes() -> None:
    big = sample_plan(image_bytes=50_000_000)
    with exactly(
        IntegrationRefused,
        "plan needs 50020000 bytes, above the approval threshold of 10000000; "
        "a signed ledger approval is required (INT-014)",
    ):
        big.decide(actor="leigh")
    other = sample_plan(image_bytes=60_000_000)
    with exactly(IntegrationRefused, f"ledger entry ledger-42 does not approve plan {big.plan_id}"):
        big.decide(actor="leigh", ledger_entry=ledger_approval(other))
    with exactly(IntegrationRefused, "ledger entry ledger-42 was signed by another actor"):
        big.decide(actor="leigh", ledger_entry=ledger_approval(big, signer="mallory"))
    at_threshold = sample_plan(image_bytes=THRESHOLD - 20_000)
    assert not at_threshold.requires_signed_approval


@pytest.mark.req("INT-014")
def test_an_approval_refuses_what_it_cannot_record() -> None:
    with exactly(IntegrationRefused, "an above-threshold approval is a signed ledger entry (INT-014)"):
        PlanApproval("p", ApprovalDecision.APPROVED, "leigh", 1)
    with exactly(InvariantViolation, "a refusal records why"):
        PlanApproval("p", ApprovalDecision.REFUSED, "leigh", 1)
    plan = sample_plan()
    auto = plan.decide(actor="kernel").approval
    assert auto is not None
    with exactly(InvariantViolation, "an approval covers the plan it names"):
        replace(plan, approval=replace(auto, plan_id="sha256:" + "0" * 64))
    with exactly(IntegrationRefused, "an approval was given against this plan's own approval threshold"):
        replace(plan, approval=replace(auto, threshold_bytes=1))
    big = sample_plan(image_bytes=50_000_000)
    with exactly(
        IntegrationRefused, "a plan above its approval threshold is approved only by a signed ledger entry (INT-014)"
    ):
        replace(big, approval=replace(auto, plan_id=big.plan_id))
    refusal = PlanApproval(big.plan_id, ApprovalDecision.REFUSED, "leigh", 1, reason="no")
    assert replace(big, approval=refusal).approval == refusal, "a refusal is kept whatever its threshold"


def test_an_approval_document_round_trips_and_defaults_its_reason() -> None:
    approval = PlanApproval("p", ApprovalDecision.REFUSED, "leigh", 5, "ledger-1", "too large")
    doc = approval.to_document()
    assert doc == {
        "plan_id": "p",
        "decision": "refused",
        "actor": "leigh",
        "threshold_bytes": 5,
        "ledger_entry_id": "ledger-1",
        "reason": "too large",
    }
    assert PlanApproval.from_document(doc) == approval
    minimal = {"plan_id": "p", "decision": "auto_approved", "actor": "k", "threshold_bytes": "7"}
    assert PlanApproval.from_document(minimal) == PlanApproval("p", ApprovalDecision.AUTO_APPROVED, "k", 7)


@pytest.mark.req("INT-015")
def test_an_unauthorised_acquisition_is_refused_naming_it() -> None:
    plan = sample_plan()
    with exactly(IntegrationRefused, f"plan {plan.plan_id} is not approved; nothing may be acquired under it"):
        plan.authorise("synthetic-mast", Operation.DOWNLOAD, "x")
    with exactly(IntegrationRefused, f"plan {plan.plan_id} is not approved; nothing may be acquired under it"):
        PlanGate(plan)
    approved = plan.decide(actor="kernel")
    with exactly(
        IntegrationRefused, "no approved plan item names cutout 'x' at synthetic-mast; acquisition refused (INT-015)"
    ):
        approved.authorise("synthetic-mast", Operation.CUTOUT, "x")


@pytest.mark.req("INT-015")
def test_the_gate_keeps_the_first_artifact_recorded_and_each_refusal_in_full() -> None:
    gate = PlanGate(sample_plan().decide(actor="kernel"))
    image, rows = gate.plan.items
    gate.record(image, "sha256:" + "1" * 64)
    gate.record(image, "sha256:" + "2" * 64)
    assert gate.acquired == {"item-1": "sha256:" + "1" * 64}
    assert gate.authorise("synthetic-gaia", Operation.CONE, "other") is None
    assert gate.refused == [
        {
            "source_id": "synthetic-gaia",
            "operation": "cone",
            "target": "other",
            "reason": "no approved plan item names cone 'other' at synthetic-gaia; acquisition refused (INT-015)",
        }
    ]
    assert gate.unavailable == ("item-2",)
    gate.record(rows, "sha256:" + "3" * 64)
    assert not gate.unavailable


def test_each_budget_limit_refuses_saying_which() -> None:
    plan = sample_plan()
    with exactly(IntegrationRefused, "a plan cannot exceed its profile's byte budget"):
        replace(plan, budget=replace(plan.budget, max_bytes=plan.cost.total_bytes - 1, approval_threshold_bytes=0))
    assert replace(plan, budget=replace(plan.budget, max_bytes=plan.cost.total_bytes, approval_threshold_bytes=0))
    busy = replace(plan.cost, requests_per_source={"b": 51, "a": 51, "c": 50})
    with exactly(IntegrationRefused, "a plan cannot exceed 50 requests per source: a, b"):
        replace(plan, cost=busy)
    with exactly(IntegrationRefused, "a plan cannot exceed its profile's wall-time budget"):
        replace(plan, cost=replace(plan.cost, estimated_wall_seconds=3600.5))
    assert replace(plan, cost=replace(plan.cost, estimated_wall_seconds=3600.0))
    strict = replace(plan.budget, min_nas_headroom_bytes=1_000)
    with exactly(IntegrationRefused, "a plan cannot leave less NAS headroom than its profile requires (DSI-010)"):
        replace(plan, budget=strict, cost=replace(plan.cost, nas_headroom_after_bytes=999))
    assert replace(plan, budget=strict, cost=replace(plan.cost, nas_headroom_after_bytes=1_000))


def test_a_plan_refuses_duplicate_items_a_wrong_total_and_a_low_calibration_level() -> None:
    plan = sample_plan()
    image, rows = plan.items
    with exactly(InvariantViolation, "plan item ids are unique"):
        replace(plan, items=(image, replace(rows, item_id=image.item_id)))
    with exactly(InvariantViolation, "plan cost totals its items"):
        replace(plan, cost=replace(plan.cost, total_bytes=plan.cost.total_bytes + 1))
    with exactly(InvariantViolation, "plan item item-x serves no requirement the plan derived"):
        replace(plan, items=(image, replace(rows, item_id="item-x", requirement_id="req-none")))
    with exactly(IntegrationRefused, "plan item item-1 is calibration level None; req-jwst-image needs 2 or above"):
        replace(plan, items=(replace(image, calibration_level=None), rows))
    assert replace(plan, items=(replace(image, calibration_level=2), rows)).items[0].calibration_level == 2


def test_a_budget_refuses_what_it_cannot_be_and_round_trips() -> None:
    budget = sample_plan().budget
    assert AcquisitionBudget.from_document(budget.to_document()) == budget
    assert budget.to_document() == {
        "max_bytes": 1_000_000_000,
        "max_requests_per_source": 50,
        "max_wall_seconds": 3600.0,
        "min_nas_headroom_bytes": 0,
        "approval_threshold_bytes": THRESHOLD,
    }
    for change in (
        {"max_bytes": -1, "approval_threshold_bytes": 0},
        {"max_requests_per_source": -1},
        {"min_nas_headroom_bytes": -1},
    ):
        with exactly(InvariantViolation, "budget limits cannot be negative"):
            replace(budget, **change)
    for threshold in (-1, budget.max_bytes + 1):
        with exactly(InvariantViolation, "the approval threshold lies within the byte budget"):
            replace(budget, approval_threshold_bytes=threshold)
    assert replace(budget, approval_threshold_bytes=budget.max_bytes).approval_threshold_bytes == budget.max_bytes
    for wall in (0.0, math.inf, math.nan):
        with exactly(InvariantViolation, "wall-time budget must be positive and finite"):
            replace(budget, max_wall_seconds=wall)


def test_a_plan_item_refuses_negative_bytes_and_restricted_data_and_round_trips() -> None:
    item = sample_plan().items[1]
    assert replace(item, expected_bytes=0).expected_bytes == 0
    with exactly(InvariantViolation, "expected bytes cannot be negative"):
        replace(item, expected_bytes=-1)
    with exactly(InvariantViolation, "a plan item is public data; restricted products are reported as gaps"):
        replace(item, access_status=AccessStatus.LOGIN)
    doc = item.to_document()
    assert doc == {
        "item_id": "item-2",
        "source_id": "synthetic-gaia",
        "operation": "cone",
        "target": "SELECT ... CONTAINS(...)",
        "expected_bytes": 20_000,
        "expected_rows": 100,
        "calibration_level": None,
        "access_status": "public",
        "licence": "CC-BY-SA-3.0 IGO",
        "requirement_id": "req-gaia-astrometry",
        "test_kind": "astrometric-coherence",
    }
    assert PlanItem.from_document(doc) == item
    minimal = {k: v for k, v in doc.items() if k not in ("expected_rows", "calibration_level")}
    assert PlanItem.from_document({**minimal, "expected_bytes": "20000"}) == replace(item, expected_rows=None)


def test_costs_and_gaps_round_trip() -> None:
    cost = PlanCost(10, {"b": 2, "a": 1}, 1.5, 7)
    assert cost.to_document() == {
        "total_bytes": 10,
        "requests_per_source": {"a": 1, "b": 2},
        "estimated_wall_seconds": 1.5,
        "nas_headroom_after_bytes": 7,
    }
    assert list(cost.to_document()["requests_per_source"]) == ["a", "b"]
    assert PlanCost.from_document(cost.to_document()) == cost
    assert (
        PlanCost.from_document({**cost.to_document(), "nas_headroom_after_bytes": None}).nas_headroom_after_bytes
        is None
    )
    source = {"a": 1}
    frozen = PlanCost(1, source, 1.0, None)
    source["a"] = 9
    assert frozen.requests_per_source == {"a": 1}
    gap = PlanGap("req-x", "nothing serves it")
    assert gap.to_document() == {"requirement_id": "req-x", "reason": "nothing serves it"}
    assert PlanGap.from_document(gap.to_document()) == gap


@pytest.mark.req("INT-013")
def test_a_plan_document_carries_its_gaps_constraints_and_approval() -> None:
    plan = replace(
        sample_plan(),
        gaps=(PlanGap("req-later", "no source lists it"),),
        constraints={"tags": {"b", "a"}, "ceiling": math.inf, "kind": ProductType.IMAGE, "nested": {"n": [1, 2]}},
    ).decide(actor="kernel")
    doc = plan.to_document()
    assert doc["schema_version"] == "1.0.0" and doc["plan_id"] == plan.plan_id
    assert doc["gaps"] == [{"requirement_id": "req-later", "reason": "no source lists it"}]
    assert doc["constraints"] == {
        "tags": ["a", "b"],
        "ceiling": None,
        "kind": "image",
        "nested": {"n": [1, 2]},
        "budget": plan.budget.to_document(),
    }
    assert doc["approval"] == plan.approval.to_document()  # type: ignore[union-attr]
    assert doc["intent"]["field"] == dict(sample_plan().field)
    assert AcquisitionPlan.from_document({**doc, "plan_id": None}).plan_id == plan.plan_id
    gapless = {k: v for k, v in sample_plan().to_document().items() if k != "gaps"}
    assert AcquisitionPlan.from_document(gapless) == sample_plan()
    edited = {**doc, "plan_id": "sha256:" + "0" * 64}
    with exactly(IntegrityError, f"plan document names sha256:{'0' * 64} but hashes to {plan.plan_id}"):
        AcquisitionPlan.from_document(edited)


@pytest.mark.req("INT-015")
def test_a_plan_keeps_a_deep_copy_of_its_field_and_constraints() -> None:
    field = {"region": {"ra_deg": 1.0}}
    constraints = {"limits": {"rows": 10}}
    plan = replace(sample_plan(), field=field, constraints=constraints)
    plan_id = plan.plan_id
    field["region"]["ra_deg"] = 2.0
    constraints["limits"]["rows"] = 20
    assert plan.plan_id == plan_id
    assert plan.field["region"] == {"ra_deg": 1.0} and plan.constraints["limits"] == {"rows": 10}


@pytest.mark.req("INT-013")
def test_the_minimum_set_plans_a_shared_acquisition_under_the_first_requirement() -> None:
    plan = sample_plan()
    image, rows = plan.items
    # Gaia also serves the image requirement here, so its cone query could be planned under either.
    gaia_for_image = replace(plan.matches[1], requirement_id="req-jwst-image", status=MatchStatus.SATISFIED)
    matches = (*plan.matches, gaia_for_image)
    for_image = replace(rows, requirement_id="req-jwst-image")
    stray = replace(image, requirement_id="req-unknown", target="stray")
    items, gaps = select_items(plan.requirements, matches, [rows, stray, image, for_image])
    assert [(i.item_id, i.source_id, i.requirement_id) for i in items] == [
        ("item-001", "synthetic-gaia", "req-jwst-image"),
        ("item-002", "synthetic-mast", "req-jwst-image"),
    ]
    assert gaps == ()
    unregistered = replace(image, source_id="synthetic-nowhere")
    dasch = replace(image, source_id="synthetic-dasch")
    gaia = replace(image, source_id="synthetic-gaia")
    _, gaps = select_items(plan.requirements[:1], plan.matches, [unregistered, gaia, dasch])
    assert gaps == (
        PlanGap(
            "req-jwst-image",
            "no source that lists it satisfies it: synthetic-dasch (unsatisfied), "
            "synthetic-gaia (unsatisfied), synthetic-nowhere (no registered descriptor)",
        ),
    )
