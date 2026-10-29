"""PlanInvestigation, ApprovePlan and ExecutePlan around Investigate's Retrieve (v0.4.0 §5, ADR-020).

Planning reads only metadata: the profile's tests say what evidence they need, the capability
registry says which sources can serve it and why, and each observation archive's index lists the
products over the field. The plan is written to the artifact store as its intent document, so its
artifact id is its plan id. Approval is automatic below the budget's threshold and a signed ledger
entry above it. Execution runs InvestigateField with a gate that authorises only what the approved
plan names; everything else is refused and recorded (INT-013 to INT-015).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sdp.kernel.application.investigate import (
    InvestigateFieldUseCase,
    InvestigatePorts,
    catalogue_query,
    identify_query,
    query_operation,
    query_target,
)
from sdp.kernel.application.media import JSON
from sdp.kernel.domain import events as ev
from sdp.kernel.domain.archive import Box, Cone
from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.domain.errors import IntegrityError, InvariantViolation
from sdp.kernel.domain.integration import (
    AccessStatus,
    AcquisitionBudget,
    AcquisitionPlan,
    EvidenceRequirement,
    IntegrationRefused,
    Operation,
    PlanCost,
    PlanGate,
    PlanItem,
    ProductType,
    order_matches,
    select_items,
)
from sdp.kernel.domain.investigation import ArchiveRole, Field, ObservationCriteria, TestProfile
from sdp.kernel.ports.determinism import Clock
from sdp.kernel.ports.integration import (
    ApprovePlanCommand,
    CapabilityDiscoveryService,
    ExecutePlanCommand,
    PlanExecution,
    PlanInvestigationCommand,
    PlanRecord,
)
from sdp.kernel.ports.investigate import InvestigateFieldCommand, ObservationTarget
from sdp.kernel.ports.ledger import Ledger
from sdp.kernel.ports.repositories import UnitOfWork
from sdp.kernel.ports.storage import ArtifactStore
from sdp.kernel.science.investigation import InvestigationTest
from sdp.kernel.science.products import ASTROMETRY, LIGHTCURVE

PLANNER_ID = "minimum-product-set"
PLANNER_VERSION = "1.0.0"

#: Investigate's canonical product kinds as the ObsCore product types a source declares.
PRODUCT_KINDS = {LIGHTCURVE: ProductType.TIMESERIES, ASTROMETRY: ProductType.MEASUREMENTS}

#: Estimates where a source publishes nothing better; each is recorded in the plan's constraints.
UNKNOWN_SIZE_BYTES = 1_000_000
CELL_BYTES = 8
THROUGHPUT_BYTES_PER_S = 1_000_000.0
SECONDS_PER_REQUEST = 1.0


#: The budget a deployment applies when it names none (SDP_PLAN_* in the adapters' settings).
DEFAULT_BUDGET = AcquisitionBudget(
    max_bytes=5_000_000_000,
    max_requests_per_source=200,
    max_wall_seconds=3600.0,
    min_nas_headroom_bytes=50_000_000_000,
    approval_threshold_bytes=500_000_000,
)


def plan_path(plan_id: str) -> str:
    return f"plans/{plan_id.removeprefix('sha256:')}.json"


def product_kind(product_type: str) -> ProductType | None:
    """The ObsCore type a test's product need is served as; None for one no source declares."""
    if product_type in PRODUCT_KINDS:
        return PRODUCT_KINDS[product_type]
    try:
        kind = ProductType(product_type)
    except ValueError:
        return None
    return kind if kind.is_science else None


def _region_operation(field_: Field) -> Operation:
    return Operation.CONE if isinstance(field_.query_region(), Cone) else Operation.REGION


def derive_requirements(
    field_: Field, profile: TestProfile, tests: Sequence[InvestigationTest]
) -> tuple[EvidenceRequirement, ...]:
    """What the profile's tests need, never a hand-written archive list (v0.4.0 §5).

    Identify needs its catalogue over the field. A test that declares ``evidence_requirements``
    is taken at its word; otherwise each ``catalogue:<name>`` input needs catalogue rows over the
    field and each product type the profile selects it with needs that product's bytes.
    """
    region_op = _region_operation(field_)
    catalogue = frozenset({ProductType.CATALOGUE})
    out: dict[str, EvidenceRequirement] = {}

    def add(need: EvidenceRequirement) -> None:
        out.setdefault(need.requirement_id, need)

    add(
        EvidenceRequirement(
            f"identify:catalogue:{profile.identify.source_id}",
            "identify",
            catalogue,
            operations=frozenset({region_op}),
            needs_bytes=False,
        )
    )
    for selection, test in zip(profile.tests, tests, strict=True):
        declared: Sequence[EvidenceRequirement] | None = getattr(test, "evidence_requirements", None)
        if declared:
            for need in declared:
                add(need)
            continue
        for name in test.inputs:
            kind, _, source = name.partition(":")
            if kind == "catalogue" and source:
                add(
                    EvidenceRequirement(
                        f"{test.name}:catalogue:{source}",
                        test.name,
                        catalogue,
                        operations=frozenset({region_op}),
                        needs_bytes=False,
                    )
                )
        for product_type in sorted(set(selection.needs_products)):
            served_as = product_kind(product_type)
            if served_as is not None:
                add(EvidenceRequirement(f"{test.name}:product:{product_type}", test.name, frozenset({served_as})))
    return tuple(out.values())


def _field_radius_arcsec(field_: Field) -> float:
    region = field_.query_region()
    if isinstance(region, Cone):
        return region.radius_deg * 3600.0
    assert isinstance(region, Box)
    _, dec = region.centre
    width = (region.ra_max_deg - region.ra_min_deg) * math.cos(math.radians(dec))
    return math.hypot(width, region.dec_max_deg - region.dec_min_deg) / 2 * 3600.0


@dataclass
class PlanInvestigationUseCase:
    """PlanInvestigation: requirements, matches, candidates, the minimum set, the plan artifact."""

    uow: Callable[[], UnitOfWork]
    store: ArtifactStore
    clock: Clock
    discovery: CapabilityDiscoveryService
    ports: InvestigatePorts

    def execute(self, command: PlanInvestigationCommand) -> AcquisitionPlan:
        profile, field_ = command.profile, command.field
        tests = self.ports.tests.validate(profile.tests)
        requirements = derive_requirements(field_, profile, tests)
        matches = order_matches(m for need in requirements for m in self.discovery.match(field_, need))
        candidates = self._candidates(field_, profile, requirements)
        items, gaps = select_items(requirements, matches, candidates)
        plan = AcquisitionPlan(
            field=field_.to_document(),
            profile_hash=profile.profile_hash,
            planner_id=PLANNER_ID,
            planner_version=PLANNER_VERSION,
            requirements=requirements,
            matches=matches,
            items=items,
            cost=self._cost(items),
            budget=command.budget,
            gaps=gaps,
            constraints={
                "profile": profile.to_document(),
                "seed": command.seed,
                "candidates": [c.to_document() for c in candidates],
                "estimates": {
                    "unknown_size_bytes": UNKNOWN_SIZE_BYTES,
                    "cell_bytes": CELL_BYTES,
                    "throughput_bytes_per_s": THROUGHPUT_BYTES_PER_S,
                    "seconds_per_request": SECONDS_PER_REQUEST,
                },
            },
        )
        artifact = self.store.put(canonical_json(plan.intent_document()).encode(), JSON)
        if artifact.value != plan.plan_id:
            raise IntegrityError(f"plan artifact {artifact.value} does not carry the plan id {plan.plan_id}")
        self.store.link(plan_path(plan.plan_id), artifact)
        with self.uow() as uow:
            if uow.plans.get(plan.plan_id) is None:
                uow.plans.add(plan)
                uow.events.append(
                    [
                        ev.PlanDrafted(
                            plan.plan_id,
                            self.clock.now(),
                            {
                                "field_id": field_.field_id,
                                "profile_hash": plan.profile_hash,
                                "items": len(items),
                                "gaps": len(gaps),
                                "total_bytes": plan.cost.total_bytes,
                                "requires_signed_approval": plan.requires_signed_approval,
                                "actor": command.actor,
                            },
                        )
                    ]
                )
            uow.commit()
        return plan

    def _candidates(
        self, field_: Field, profile: TestProfile, requirements: Sequence[EvidenceRequirement]
    ) -> list[PlanItem]:
        """Every acquisition that could serve a requirement, read from metadata alone."""
        out: list[PlanItem] = []

        def candidate(
            source_id: str,
            operation: Operation,
            target: str,
            expected_bytes: int,
            expected_rows: int | None,
            licence: str,
            need: EvidenceRequirement,
        ) -> PlanItem:
            return PlanItem(
                "candidate",
                source_id,
                operation,
                target,
                expected_bytes,
                expected_rows,
                None,
                AccessStatus.PUBLIC,
                licence,
                need.requirement_id,
                need.test_kind,
            )

        identify = profile.identify
        query = identify_query(field_, profile)
        need = requirements[0]
        out.append(
            candidate(
                identify.source_id,
                query_operation(query),
                query_target(query),
                identify.row_cap * len(identify.columns) * CELL_BYTES,
                identify.row_cap,
                self._licence(identify.source_id),
                need,
            )
        )
        catalogue_needs = [
            r for r in requirements[1:] if ProductType.CATALOGUE in r.product_types and not r.needs_bytes
        ]
        product_needs = [r for r in requirements if r.needs_bytes]
        for selection in profile.archives:
            if selection.role is ArchiveRole.CATALOGUE:
                query = catalogue_query(field_, selection)
                for need in catalogue_needs:
                    out.append(
                        candidate(
                            selection.source_id,
                            query_operation(query),
                            query_target(query),
                            query.row_cap * len(selection.columns) * CELL_BYTES,
                            query.row_cap,
                            self._licence(selection.source_id),
                            need,
                        )
                    )
                continue
            gateway = self.ports.observation_gateways.get(selection.source_id)
            if gateway is None:
                raise InvariantViolation(f"no ObservationArchiveGateway for {selection.source_id!r} (INV-004)")
            region = field_.query_region()
            ra, dec = (region.ra_deg, region.dec_deg) if isinstance(region, Cone) else region.centre
            index = gateway.search(
                ObservationTarget("field", ra, dec),
                ObservationCriteria(
                    missions=selection.missions,
                    product_types=selection.product_types,
                    radius_arcsec=_field_radius_arcsec(field_) + selection.match_radius_arcsec,
                ),
            )
            for record in index.records:
                served_as = product_kind(record.product_type)
                for need in product_needs:
                    if served_as is None or served_as not in need.product_types:
                        continue
                    # A derived requirement names the product type its test reads; a record of
                    # another type served as the same ObsCore type is not what the test reads.
                    derived = need.requirement_id.partition(":product:")[2]
                    if derived and derived != record.product_type:
                        continue
                    out.append(
                        candidate(
                            selection.source_id,
                            Operation.DOWNLOAD,
                            record.product.product_id,
                            record.size_bytes if record.size_bytes is not None else UNKNOWN_SIZE_BYTES,
                            None,
                            record.licence,
                            need,
                        )
                    )
        return out

    def _licence(self, source_id: str) -> str:
        try:
            return self.discovery.describe(source_id).rights.licence
        except KeyError:
            return "not registered"

    def _throughput(self, source_id: str) -> float:
        try:
            measured = self.discovery.describe(source_id).limits.throughput_bytes_per_s
        except KeyError:
            measured = None
        return measured or THROUGHPUT_BYTES_PER_S

    def _cost(self, items: Sequence[PlanItem]) -> PlanCost:
        requests: dict[str, int] = {}
        seconds = 0.0
        for item in items:
            requests[item.source_id] = requests.get(item.source_id, 0) + 1
            seconds += SECONDS_PER_REQUEST + item.expected_bytes / self._throughput(item.source_id)
        total = sum(i.expected_bytes for i in items)
        free = self.store.free_bytes()
        return PlanCost(total, requests, round(seconds, 3), free - total if free is not None else None)


@dataclass
class ApprovePlanUseCase:
    """ApprovePlan: automatic below the threshold, a signed ledger entry above it (INT-014)."""

    uow: Callable[[], UnitOfWork]
    ledger: Ledger
    clock: Clock

    def execute(self, command: ApprovePlanCommand) -> AcquisitionPlan:
        with self.uow() as uow:
            record = uow.plans.get(command.plan_id)
        if record is None:
            raise KeyError(f"no plan {command.plan_id}")
        plan = record.plan
        if plan.approval is not None:
            raise IntegrationRefused(f"plan {plan.plan_id} is already {plan.approval.decision.value}")
        entry = None
        if command.ledger_entry_id is not None:
            entry = next((e for e in self.ledger.entries() if e.entry_id == command.ledger_entry_id), None)
            if entry is None:
                raise IntegrationRefused(f"no ledger entry {command.ledger_entry_id} (INT-014)")
        elif plan.requires_signed_approval and not command.refuse_reason:
            entry = self.ledger.find(plan.plan_id, kind="approval")
        decided = plan.decide(actor=command.actor, ledger_entry=entry, refuse_reason=command.refuse_reason)
        assert decided.approval is not None
        approval = decided.approval
        event = ev.PlanRefused if not approval.approves else ev.PlanApproved
        with self.uow() as uow:
            uow.plans.decide(decided)
            uow.events.append([event(plan.plan_id, self.clock.now(), approval.to_document())])
            uow.commit()
        return decided


@dataclass
class ExecutePlanUseCase:
    """ExecutePlan: InvestigateField under a gate that authorises only the approved plan's items."""

    uow: Callable[[], UnitOfWork]
    clock: Clock
    investigate: InvestigateFieldUseCase

    def execute(self, command: ExecutePlanCommand) -> PlanExecution:
        with self.uow() as uow:
            record = uow.plans.get(command.plan_id)
        if record is None:
            raise KeyError(f"no plan {command.plan_id}")
        if record.execution is not None:
            raise IntegrationRefused(f"plan {command.plan_id} was already executed (set {record.set_id})")
        plan = record.plan
        gate = PlanGate(plan)  # refuses a plan that is not approved
        profile = TestProfile.from_document(plan.constraints["profile"])
        if profile.profile_hash != plan.profile_hash:
            raise IntegrityError(f"plan {plan.plan_id} records a profile that does not hash to {plan.profile_hash}")
        run = self.investigate.execute(
            InvestigateFieldCommand(
                Field.from_document(plan.field),
                profile,
                seed=int(plan.constraints.get("seed", 0)),
                actor=command.actor,
                gate=gate,
            )
        )
        execution = PlanExecution(
            plan.plan_id, tuple(gate.acquired[i] for i in sorted(gate.acquired)), gate.unavailable
        )
        set_id = run.investigation_set.set_id
        with self.uow() as uow:
            uow.plans.executed(plan.plan_id, set_id, execution)
            uow.events.append(
                [
                    ev.PlanExecuted(
                        plan.plan_id,
                        self.clock.now(),
                        {**execution.to_document(), "set_id": set_id, "refused": list(gate.refused)},
                    )
                ]
            )
            uow.commit()
        return execution


@dataclass
class QueryPlansUseCase:
    uow: Callable[[], UnitOfWork]

    def plans(self) -> Sequence[PlanRecord]:
        with self.uow() as uow:
            return uow.plans.list()

    def plan(self, plan_id: str) -> PlanRecord | None:
        with self.uow() as uow:
            return uow.plans.get(plan_id)


def plan_view(record: PlanRecord) -> dict[str, Any]:
    """The shape the evidence UI reads (agreed with Stage E): id, setId, state, document, execution."""
    return {
        "id": record.plan.plan_id,
        "setId": record.set_id,
        "state": record.state,
        "document": record.plan.to_document(),
        "execution": record.execution.to_document() if record.execution else None,
    }
