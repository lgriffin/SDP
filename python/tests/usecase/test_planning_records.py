"""T4: PlanInvestigation, ApprovePlan and ExecutePlan in full: every candidate a plan considers, its
estimates and cost, the events each step records with their exact payloads, the exact refusal each
guard gives, and the shape the evidence UI reads (INT-013 to INT-015, ADR-020).

The behaviour is in test_planning.py; these pin the details mutation testing showed nothing else
checked (docs/testing.md, "Killing a survivor")."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from tests.usecase.test_planning import LIGHTCURVES, World, budget, make_world

from sdp.kernel.application.media import JSON
from sdp.kernel.application.planning import (
    CUTOUT_HEADER_BYTES,
    PRODUCT_KINDS,
    THROUGHPUT_BYTES_PER_S,
    cutout_bytes,
    derive_requirements,
    plan_path,
    plan_view,
    product_kind,
)
from sdp.kernel.domain.errors import IntegrityError, InvariantViolation
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.integration import (
    AcquisitionPlan,
    EvidenceRequirement,
    IntegrationRefused,
    Operation,
    ProductType,
)
from sdp.kernel.domain.investigation import ObservationCriteria
from sdp.kernel.ports.integration import PlanRecord
from sdp.kernel.ports.investigate import ObservationTarget
from sdp.testing.investigate import ProperMotionExcessTest, planning_descriptors, synthetic_profile

pytestmark = pytest.mark.tier("T4")

GAIA_COLUMNS = 10
TWOMASS_COLUMNS = 5


def events(world: World, name: str) -> list[tuple[str, Any, dict[str, Any]]]:
    return [
        (e.event.aggregate_id, e.event.occurred_at, dict(e.event.payload))
        for e in world.k.db.state.events
        if e.event.name == name
    ]


# --- the pure helpers ----------------------------------------------------------------------------


def test_a_plan_path_drops_the_hash_prefix() -> None:
    assert plan_path("sha256:" + "ab" * 32) == "plans/" + "ab" * 32 + ".json"
    assert plan_path("plain") == "plans/plain.json"


def test_a_product_type_is_served_as_its_admitted_kind_or_a_science_type() -> None:
    assert product_kind("lightcurve") is ProductType.TIMESERIES
    assert all(product_kind(kind) is served for kind, served in PRODUCT_KINDS.items())
    assert product_kind("image") is ProductType.IMAGE
    assert product_kind("catalogue") is ProductType.CATALOGUE
    assert product_kind("preview") is None, "a preview is listed beside products but no test reads it"
    assert product_kind("no-such-type") is None


def test_a_cutout_is_planned_at_the_finest_pixel_with_headers() -> None:
    # 1 arcsec at 0.031 arcsec a pixel: 33 pixels, plus one for the edge, at 4 bytes each.
    assert cutout_bytes(1.0) == 34 * 34 * 4 + CUTOUT_HEADER_BYTES == 62_224
    assert cutout_bytes(2.0) == 66 * 66 * 4 + CUTOUT_HEADER_BYTES
    assert CUTOUT_HEADER_BYTES == 57_600
    assert cutout_bytes(0.0) == 4 + CUTOUT_HEADER_BYTES


def test_identify_needs_its_catalogue_over_the_field_and_no_bytes() -> None:
    world = make_world()
    [identify, *_] = derive_requirements(world.field, world.profile, [ProperMotionExcessTest()])
    assert identify == EvidenceRequirement(
        "identify:catalogue:synthetic-gaia",
        "identify",
        frozenset({ProductType.CATALOGUE}),
        operations=frozenset({Operation.CONE}),
        needs_bytes=False,
    )


def test_a_test_catalogue_is_needed_over_the_field_region() -> None:
    world = make_world()
    needs = derive_requirements(world.field, world.profile, [ProperMotionExcessTest()])
    catalogue = next(n for n in needs if n.requirement_id == "pm-excess:catalogue:synthetic-gaia")
    assert catalogue.operations == frozenset({Operation.CONE}) and not catalogue.needs_bytes
    assert catalogue.test_kind == "pm-excess"


def test_a_test_that_declares_its_requirements_is_taken_at_its_word() -> None:
    world = make_world()
    declared = (
        EvidenceRequirement("pm-excess:product:custom", "pm-excess", frozenset({ProductType.SPECTRUM})),
        EvidenceRequirement("pm-excess:product:other", "pm-excess", frozenset({ProductType.IMAGE})),
    )
    test = ProperMotionExcessTest()
    object.__setattr__(test, "evidence_requirements", declared)
    needs = derive_requirements(world.field, world.profile, [test])
    assert [n.requirement_id for n in needs] == [
        "identify:catalogue:synthetic-gaia",
        "pm-excess:product:custom",
        "pm-excess:product:other",
    ]


def test_a_profile_and_its_tests_must_pair_up() -> None:
    world = make_world()
    with pytest.raises(ValueError):
        derive_requirements(world.field, world.profile, [ProperMotionExcessTest(), ProperMotionExcessTest()])


# --- PlanInvestigation ---------------------------------------------------------------------------


def test_a_plan_lists_every_candidate_it_considered() -> None:
    plan = make_world(LIGHTCURVES).plan()
    gaia = {
        "item_id": "candidate",
        "source_id": "synthetic-gaia",
        "operation": "cone",
        "expected_bytes": 100_000 * GAIA_COLUMNS * 8,
        "expected_rows": 100_000,
        "calibration_level": None,
        "access_status": "public",
        "licence": "CC-BY-SA-3.0 IGO",
    }
    twomass = {
        **gaia,
        "source_id": "synthetic-2mass",
        "expected_bytes": 50_000 * TWOMASS_COLUMNS * 8,
        "expected_rows": 50_000,
        "licence": "public domain",
    }
    plates = {
        "item_id": "candidate",
        "source_id": "fake-plates",
        "operation": "download",
        "expected_bytes": 512,
        "expected_rows": None,
        "calibration_level": None,
        "access_status": "public",
        "licence": "fixture licence",
        "requirement_id": "pm-excess:product:lightcurve",
        "test_kind": "pm-excess",
    }
    candidates = [{k: v for k, v in c.items() if k != "target"} for c in plan.constraints["candidates"]]
    assert candidates == [
        {**gaia, "requirement_id": "identify:catalogue:synthetic-gaia", "test_kind": "identify"},
        {**gaia, "requirement_id": "pm-excess:catalogue:synthetic-gaia", "test_kind": "pm-excess"},
        {**twomass, "requirement_id": "pm-excess:catalogue:synthetic-gaia", "test_kind": "pm-excess"},
        *[plates] * 5,
    ]
    targets = [c["target"] for c in plan.constraints["candidates"]]
    assert targets[0] == targets[1] != targets[2]
    assert targets[3:] == [f"lc-{i}" for i in range(5)]


def test_a_plan_records_its_seed_and_estimates() -> None:
    plan = make_world().plan()
    assert plan.constraints["seed"] == 11
    assert plan.constraints["estimates"] == {
        "unknown_size_bytes": 1_000_000,
        "cell_bytes": 8,
        "throughput_bytes_per_s": 1_000_000.0,
        "seconds_per_request": 1.0,
    }
    assert plan.constraints["profile"] == make_world().profile.to_document()


def test_a_plan_is_stored_as_json_and_drafting_is_recorded() -> None:
    world = make_world(LIGHTCURVES)
    plan = world.plan(actor="leigh")
    assert world.k.store.media_type(ArtifactId(plan.plan_id)) == JSON
    [(aggregate, at, payload)] = events(world, "PlanDrafted")
    assert aggregate == plan.plan_id and at is not None
    assert payload == {
        "field_id": world.field.field_id,
        "profile_hash": world.profile.profile_hash,
        "items": 7,
        "gaps": 0,
        "total_bytes": plan.cost.total_bytes,
        "requires_signed_approval": False,
        "actor": "leigh",
    }


def test_a_store_that_files_the_plan_elsewhere_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world()
    real_put = world.k.store.put
    monkeypatch.setattr(world.k.store, "put", lambda data, media_type, **kw: real_put(data + b" ", media_type, **kw))
    with pytest.raises(IntegrityError, match=r"^plan artifact sha256:[0-9a-f]{64} does not carry the plan id sha256:"):
        world.plan()


def test_the_cost_counts_requests_time_and_headroom() -> None:
    world = make_world(LIGHTCURVES)
    world.k.store.capacity = 10**9
    free = world.k.store.free_bytes()
    plan = world.plan()
    cost = plan.cost
    assert cost.total_bytes == 5 * 512 + 2_000_000 + 8_000_000
    assert dict(cost.requests_per_source) == {"fake-plates": 5, "synthetic-2mass": 1, "synthetic-gaia": 1}
    assert free is not None and cost.nas_headroom_after_bytes == free - cost.total_bytes
    descriptors = {d.source_id: d for d in planning_descriptors()}
    seconds = 0.0
    for item in plan.items:
        measured = descriptors[item.source_id].limits.throughput_bytes_per_s
        seconds += 1.0 + item.expected_bytes / (measured or 1_000_000.0)
    assert cost.estimated_wall_seconds == round(seconds, 3) == 12.003


def test_without_a_store_capacity_there_is_no_headroom() -> None:
    assert make_world().plan().cost.nas_headroom_after_bytes is None


def test_a_candidate_without_a_descriptor_has_no_registered_licence() -> None:
    _, twomass, _ = planning_descriptors()
    plan = make_world(descriptors=(twomass,)).plan()
    candidates = plan.constraints["candidates"]
    assert candidates[0]["source_id"] == "synthetic-gaia" and candidates[0]["licence"] == "not registered"


def test_a_source_without_a_descriptor_or_a_measured_rate_is_costed_at_the_default_throughput() -> None:
    gaia, twomass, plates = planning_descriptors()
    unmeasured = replace(twomass, limits=replace(twomass.limits, throughput_bytes_per_s=None))
    planner = make_world(descriptors=(gaia, unmeasured)).k.kernel.plan_investigation
    assert planner is not None
    assert planner._throughput(plates.source_id) == THROUGHPUT_BYTES_PER_S == 1_000_000.0, "not registered"
    assert planner._throughput(twomass.source_id) == THROUGHPUT_BYTES_PER_S, "registered without a rate"
    assert planner._throughput(gaia.source_id) == 2_000_000.0
    item = replace(make_world().plan().items[0], source_id=plates.source_id, expected_bytes=2_000_000)
    assert planner._cost([item]).estimated_wall_seconds == 1.0 + 2.0


def test_the_index_is_searched_around_the_field_for_the_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(LIGHTCURVES)
    seen: list[tuple[ObservationTarget, ObservationCriteria]] = []
    real = world.plates.search

    def search(target: ObservationTarget, criteria: ObservationCriteria) -> Any:
        seen.append((target, criteria))
        return real(target, criteria)

    monkeypatch.setattr(world.plates, "search", search)
    world.plan()
    [(target, criteria)] = seen
    selection = next(s for s in world.profile.archives if s.source_id == "fake-plates")
    assert target.key == "field"
    assert criteria.missions == selection.missions
    assert criteria.product_types == selection.product_types
    assert criteria.min_calibration_level == selection.min_calibration_level
    assert criteria.radius_arcsec > selection.match_radius_arcsec


def test_an_observation_source_without_a_gateway_is_refused() -> None:
    world = make_world(LIGHTCURVES)
    assert world.k.kernel.plan_investigation is not None
    gateways = world.k.kernel.plan_investigation.ports.observation_gateways
    assert isinstance(gateways, dict)
    del gateways["fake-plates"]
    with pytest.raises(InvariantViolation, match=r"^no ObservationArchiveGateway for 'fake-plates' \(INV-004\)$"):
        world.plan()


def test_a_product_of_unknown_size_is_planned_at_the_default_size() -> None:
    world = make_world(LIGHTCURVES)
    world.plates.records = tuple(replace(p, record=replace(p.record, size_bytes=None)) for p in world.plates.records)
    plan = world.plan()
    sizes = {i.expected_bytes for i in plan.items if i.source_id == "fake-plates"}
    assert sizes == {1_000_000}


def _plate_candidates(world: World) -> list[tuple[str, str]]:
    plan = world.plan()
    return [
        (c["requirement_id"], c["target"]) for c in plan.constraints["candidates"] if c["source_id"] == "fake-plates"
    ]


def test_each_product_need_gets_the_records_of_its_own_type() -> None:
    world = make_world(("astrometry", "lightcurve"))
    assert sorted(_plate_candidates(world)) == sorted(
        [("pm-excess:product:astrometry", f"pos-{i}") for i in range(5)]
        + [("pm-excess:product:lightcurve", f"lc-{i}") for i in range(5)]
    )


def test_a_record_served_alike_is_not_a_candidate_for_another_type() -> None:
    world = make_world(("lightcurve", "timeseries"))
    first = next(p for p in world.plates.records if p.record.product.product_id == "lc-0")
    series = replace(
        first,
        record=replace(
            first.record, product_type="timeseries", product=replace(first.record.product, product_id="ts-0")
        ),
    )
    world.plates.records = (series, *world.plates.records)
    world.plates.products["ts-0"] = world.plates.products["lc-0"]
    candidates = _plate_candidates(world)
    assert ("pm-excess:product:timeseries", "ts-0") in candidates
    assert ("pm-excess:product:lightcurve", "ts-0") not in candidates
    assert ("pm-excess:product:timeseries", "lc-0") not in candidates


# --- ApprovePlan ---------------------------------------------------------------------------------


def test_approving_an_unknown_plan_names_it() -> None:
    world = make_world()
    with pytest.raises(KeyError, match=r"no plan sha256:missing"):
        world.approve("sha256:missing")


def test_an_approval_naming_an_unknown_ledger_entry_is_refused() -> None:
    world = make_world()
    plan = world.plan()
    with pytest.raises(IntegrationRefused, match=r"^no ledger entry led-404 \(INT-014\)$"):
        world.approve(plan.plan_id, ledger_entry_id="led-404")


def test_an_entry_that_does_not_verify_is_refused_with_its_problems() -> None:
    world = make_world(LIGHTCURVES)
    plan = world.plan(budget=budget(threshold=1000))
    entry = world.k.ledger.append(
        kind="approval", subject_id="plan", subject_hash=plan.plan_id, signer="leigh", payload={}
    )
    problems = [f"{entry.entry_id}: signature does not verify", f"{entry.entry_id}: unknown key"]
    world.k.ledger.verify = lambda: [*problems, "other: elsewhere"]  # type: ignore[method-assign]
    with pytest.raises(IntegrationRefused) as caught:
        world.approve(plan.plan_id)
    assert str(caught.value) == f"ledger entry {entry.entry_id} does not verify: {'; '.join(problems)}"


def test_a_refusal_needs_no_ledger_entry_even_above_the_threshold() -> None:
    world = make_world(LIGHTCURVES)
    plan = world.plan(budget=budget(threshold=1000))
    world.k.ledger.find = lambda *a, **kw: pytest.fail("a refusal looked for a signed approval")  # type: ignore[method-assign]
    refused = world.approve(plan.plan_id, refuse_reason="too big")
    assert refused.approval is not None and not refused.approval.approves


def test_an_automatic_approval_reads_no_ledger() -> None:
    world = make_world()
    plan = world.plan()
    world.k.ledger.verify = lambda: pytest.fail("verified a ledger without an entry")  # type: ignore[method-assign]
    world.approve(plan.plan_id)


def test_a_decision_is_recorded_against_the_plan() -> None:
    world = make_world()
    plan = world.plan()
    approved = world.approve(plan.plan_id)
    assert approved.approval is not None
    [(aggregate, at, payload)] = events(world, "PlanApproved")
    assert aggregate == plan.plan_id and at is not None
    assert payload == approved.approval.to_document()


def _store(world: World, plan_id: str, plan: AcquisitionPlan) -> None:
    """Approve a drafted plan and put it under ``plan_id``, as a store that lost track of it would."""
    world.k.db.state.plans[plan_id]["plan"] = plan.decide(actor="leigh", ledger_entry=None).to_document()


# --- ExecutePlan ---------------------------------------------------------------------------------


def test_executing_an_unknown_plan_names_it() -> None:
    world = make_world()
    with pytest.raises(KeyError, match=r"no plan sha256:missing"):
        world.execute("sha256:missing")


def test_a_plan_whose_profile_does_not_hash_to_its_hash_is_refused() -> None:
    world = make_world()
    plan = world.approve(world.plan().plan_id)
    other = synthetic_profile(needs_products=LIGHTCURVES).to_document()
    _store(world, plan.plan_id, replace(plan, approval=None, constraints={**plan.constraints, "profile": other}))
    with pytest.raises(
        IntegrityError,
        match=rf"^plan sha256:[0-9a-f]{{64}} records a profile that does not hash to {plan.profile_hash}$",
    ):
        world.execute(plan.plan_id)


def test_execution_runs_with_the_planned_seed_and_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world()
    plan = world.approve(world.plan().plan_id)
    assert world.k.kernel.execute_plan is not None
    investigate = world.k.kernel.execute_plan.investigate
    commands: list[Any] = []
    real = investigate.execute

    def execute(command: Any) -> Any:
        commands.append(command)
        return real(command)

    monkeypatch.setattr(investigate, "execute", execute)
    world.execute(plan.plan_id)
    [command] = commands
    assert command.seed == 11 and command.actor == "leigh"


def test_a_plan_without_a_recorded_seed_runs_with_seed_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world()
    plan = world.approve(world.plan().plan_id)
    _store(
        world,
        plan.plan_id,
        replace(plan, approval=None, constraints={k: v for k, v in plan.constraints.items() if k != "seed"}),
    )
    assert world.k.kernel.execute_plan is not None
    investigate = world.k.kernel.execute_plan.investigate
    seeds: list[int] = []
    real = investigate.execute

    def execute(command: Any) -> Any:
        seeds.append(command.seed)
        return real(command)

    monkeypatch.setattr(investigate, "execute", execute)
    # the run is recorded against the stand-in plan's own id, which the store does not hold
    with pytest.raises(InvariantViolation, match="to record an execution for"):
        world.execute(plan.plan_id)
    assert seeds == [0]


def test_an_execution_is_recorded_against_the_plan() -> None:
    world = make_world(LIGHTCURVES)
    plan = world.approve(world.plan().plan_id)
    execution = world.execute(plan.plan_id)
    assert execution.plan_id == plan.plan_id
    [(aggregate, at, payload)] = events(world, "PlanExecuted")
    assert aggregate == plan.plan_id and at is not None
    record = world.k.kernel.query_plans.plan(plan.plan_id)  # type: ignore[union-attr]
    assert record is not None and record.set_id is not None
    assert payload == {**execution.to_document(), "set_id": record.set_id, "refused": []}


# --- the evidence UI's view ----------------------------------------------------------------------


def test_the_plan_view_has_the_shape_the_ui_reads() -> None:
    world = make_world()
    plan = world.plan()
    drafted = plan_view(PlanRecord(plan))
    assert drafted == {
        "id": plan.plan_id,
        "setId": None,
        "state": "drafted",
        "document": plan.to_document(),
        "execution": None,
    }
    approved = world.approve(plan.plan_id)
    world.execute(plan.plan_id)
    record = world.k.kernel.query_plans.plan(plan.plan_id)  # type: ignore[union-attr]
    assert record is not None
    view = plan_view(record)
    assert view["setId"] == record.set_id is not None
    assert view["document"] == approved.to_document()
    assert record.execution is not None and view["execution"] == record.execution.to_document()
