"""Planning steps. The seam is the use-case tier's: PlanInvestigation, ApprovePlan and ExecutePlan on the
kernel wired to the fakes of ``sdp.testing``, as in ``tests/usecase/test_planning.py``.

A step drives the kernel only through its driving ports and reads back only through ``query_plans``,
the event log and the fakes' own inspection methods.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from sdp.kernel.domain.integration import AcquisitionBudget, AcquisitionPlan, IntegrationRefused
from sdp.kernel.ports.integration import ApprovePlanCommand, ExecutePlanCommand, PlanExecution, PlanInvestigationCommand
from sdp.kernel.ports.ledger import LedgerEntry
from sdp.testing.fakes.archives import FakeArchive
from sdp.testing.fakes.investigate import FakeObservationArchive
from sdp.testing.investigate import (
    ProperMotionExcessTest,
    gaia_archive,
    investigate_ports,
    observation_archive,
    planning_discovery,
    synthetic_catalogue,
    synthetic_field,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

from .runner import given, then, when

LIGHTCURVES = ("lightcurve",)


@dataclass
class PlanningWorld:
    """One field planned against the synthetic Gaia, 2MASS and plate archives."""

    k: InMemoryKernel
    gaia: FakeArchive
    twomass: FakeArchive
    plates: FakeObservationArchive
    plan: AcquisitionPlan
    entry: LedgerEntry | None = None
    execution: PlanExecution | None = None
    error: IntegrationRefused | None = None

    def events(self, name: str) -> list[dict[str, Any]]:
        return [dict(e.event.payload) for e in self.k.db.state.events if e.event.name == name]

    def approve(self, actor: str, ledger_entry_id: str | None = None) -> None:
        assert self.k.kernel.approve_plan is not None
        try:
            self.plan = self.k.kernel.approve_plan.execute(
                ApprovePlanCommand(self.plan.plan_id, actor, ledger_entry_id)
            )
        except IntegrationRefused as error:
            self.error = error


def world(ctx: dict[str, Any]) -> PlanningWorld:
    planning: PlanningWorld = ctx["planning"]
    return planning


# Given


@given("an investigation planned with an approval threshold of {threshold:d} bytes")
def _planned(ctx: dict[str, Any], threshold: int) -> None:
    catalogue = synthetic_catalogue()
    gaia, twomass, plates = gaia_archive(catalogue), twomass_archive(catalogue), observation_archive(catalogue)
    k = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia, "synthetic-2mass": twomass},
        investigate=investigate_ports(
            ProperMotionExcessTest(needs_products=LIGHTCURVES), catalogue=catalogue, observations=plates
        ),
        discovery=planning_discovery(),
    )
    budget = AcquisitionBudget(1_000_000_000, 50, 3600.0, 0, threshold)
    assert k.kernel.plan_investigation is not None
    plan = k.kernel.plan_investigation.execute(
        PlanInvestigationCommand(synthetic_field(), synthetic_profile(needs_products=LIGHTCURVES), budget, seed=11)
    )
    ctx["planning"] = PlanningWorld(k, gaia, twomass, plates, plan)


@given('"{signer}" has signed a ledger approval of another plan')
def _signed_other(ctx: dict[str, Any], signer: str) -> None:
    w = world(ctx)
    w.entry = w.k.ledger.append(
        kind="approval", subject_id="plan", subject_hash="sha256:" + "0" * 64, signer=signer, payload={}
    )


@given('"{signer}" has signed a ledger approval of the plan')
def _signed(ctx: dict[str, Any], signer: str) -> None:
    w = world(ctx)
    w.entry = w.k.ledger.append(
        kind="approval", subject_id="plan", subject_hash=w.plan.plan_id, signer=signer, payload={}
    )


@given('the archive lists a light curve "{product_id}" that the plan does not name')
def _late_product(ctx: dict[str, Any], product_id: str) -> None:
    plates = world(ctx).plates
    first = next(p for p in plates.records if p.record.product.product_id.startswith("lc-"))
    late = replace(first, record=replace(first.record, product=replace(first.record.product, product_id=product_id)))
    plates.records += (late,)
    plates.products[product_id] = plates.products[first.record.product.product_id]


# When


@given('"{actor}" approves the plan')
@when('"{actor}" approves the plan')
def _approve(ctx: dict[str, Any], actor: str) -> None:
    world(ctx).approve(actor)


@when('"{actor}" approves the plan with that ledger entry')
def _approve_with(ctx: dict[str, Any], actor: str) -> None:
    w = world(ctx)
    assert w.entry is not None
    w.approve(actor, w.entry.entry_id)


@when("the plan is executed")
def _execute(ctx: dict[str, Any]) -> None:
    w = world(ctx)
    assert w.k.kernel.execute_plan is not None
    try:
        w.execution = w.k.kernel.execute_plan.execute(ExecutePlanCommand(w.plan.plan_id, "leigh"))
    except IntegrationRefused as error:
        w.error = error


# Then


@then('the plan is "{state}"')
def _state(ctx: dict[str, Any], state: str) -> None:
    w = world(ctx)
    assert w.error is None, w.error
    assert w.plan.approval is not None and w.plan.approval.decision.value == state
    assert w.k.kernel.query_plans is not None
    record = w.k.kernel.query_plans.plan(w.plan.plan_id)
    assert record is not None and record.state == state


@then('the plan is "{state}" under that ledger entry')
def _state_under(ctx: dict[str, Any], state: str) -> None:
    _state(ctx, state)
    w = world(ctx)
    assert w.entry is not None and w.plan.approval is not None
    assert w.plan.approval.ledger_entry_id == w.entry.entry_id


@then('the event "{name}" records the decision "{decision}"')
def _decision(ctx: dict[str, Any], name: str, decision: str) -> None:
    assert [e["decision"] for e in world(ctx).events(name)] == [decision]


@then('the approval is refused because "{reason}"')
@then('the execution is refused because "{reason}"')
def _refused(ctx: dict[str, Any], reason: str) -> None:
    w = world(ctx)
    assert w.error is not None and reason in str(w.error), w.error


@then("nothing has been acquired")
def _nothing(ctx: dict[str, Any]) -> None:
    w = world(ctx)
    assert w.gaia.fetch_calls == 0 and w.twomass.fetch_calls == 0 and w.plates.fetches == []


@then('"{product_id}" was not fetched')
def _not_fetched(ctx: dict[str, Any], product_id: str) -> None:
    assert product_id not in world(ctx).plates.fetches


@then('the event "PlanExecuted" records the refusal of "{target}" at "{source}"')
def _refusal(ctx: dict[str, Any], target: str, source: str) -> None:
    (executed,) = world(ctx).events("PlanExecuted")
    assert [(r["source_id"], r["target"]) for r in executed["refused"]] == [(source, target)]
    assert "INT-015" in executed["refused"][0]["reason"]


@then("every plan item was acquired")
def _all_acquired(ctx: dict[str, Any]) -> None:
    w = world(ctx)
    assert w.execution is not None and w.execution.unavailable == ()
    assert len(w.execution.acquired) == len(w.plan.items)
