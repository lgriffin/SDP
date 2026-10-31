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
from dataclasses import dataclass, replace
from typing import Any

from sdp.kernel.application.investigate import (
    InvestigateFieldUseCase,
    InvestigatePorts,
    catalogue_query,
    field_search_cone,
    identify_query,
    query_operation,
    query_target,
)
from sdp.kernel.application.media import JSON
from sdp.kernel.domain import events as ev
from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.domain.dataset import ADMITTED_AS
from sdp.kernel.domain.errors import IntegrityError, InvariantViolation
from sdp.kernel.domain.integration import (
    AccessStatus,
    AcquisitionBudget,
    AcquisitionPlan,
    EvidenceRequirement,
    IntegrationRefused,
    Operation,
    PlanCost,
    PlanExclusion,
    PlanGate,
    PlanItem,
    ProductOption,
    ProductType,
    order_matches,
    select_items,
    select_products,
)
from sdp.kernel.domain.investigation import (
    ArchiveRole,
    ArchiveSelection,
    Field,
    ObservationCriteria,
    ObservationRecord,
    TestProfile,
)
from sdp.kernel.ports.determinism import Clock
from sdp.kernel.ports.integration import (
    ApprovePlanCommand,
    CapabilityDiscoveryService,
    ExecutePlanCommand,
    PlanExecution,
    PlanInvestigationCommand,
    PlanRecord,
)
from sdp.kernel.ports.investigate import (
    CutoutCosts,
    InvestigateFieldCommand,
    ObservationTarget,
    ProductCutouts,
    cutout_product_id,
)
from sdp.kernel.ports.ledger import Ledger
from sdp.kernel.ports.repositories import UnitOfWork
from sdp.kernel.ports.storage import ArtifactStore
from sdp.kernel.science.investigation import InvestigationTest

PLANNER_ID = "minimum-product-set"
PLANNER_VERSION = "1.0.0"

#: Investigate's canonical product kinds as the ObsCore product types a source declares.
#: The one mapping admission also reads (``dataset.ADMITTED_AS``), so planning and admission agree.
PRODUCT_KINDS = {kind: ProductType(admitted) for kind, admitted in ADMITTED_AS.items()}

#: Estimates where a source publishes nothing better; each is recorded in the plan's constraints.
UNKNOWN_SIZE_BYTES = 1_000_000
#: The finest pixel a cutout is sized for (NIRCam short wavelength, 0.031 arcsec), so a planned cutout
#: never under-states what it stores: a square of this many pixels a side, at 4 bytes a pixel, plus headers.
CUTOUT_PIXEL_ARCSEC = 0.031
CUTOUT_HEADER_BYTES = 2 * 28_800
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
        catalogues = [name.partition(":")[2] for name in test.inputs if name.startswith("catalogue:")]
        for name in test.inputs:
            kind, _, source = name.partition(":")
            if kind == "catalogue" and source:
                # A test names its primary catalogue generically ("gaia"); the profile says which
                # source serves it through the test's ``source_id`` threshold.
                if source == catalogues[0] and selection.thresholds.get("source_id"):
                    source = str(selection.thresholds["source_id"])
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


def effective_budget(deployment: AcquisitionBudget, profile: TestProfile) -> AcquisitionBudget:
    """The deployment's budget, tightened by the profile's own cap; a profile never loosens it (INT-025)."""
    if profile.budget is None:
        return deployment
    cap = min(deployment.max_bytes, profile.budget.max_bytes)
    return replace(deployment, max_bytes=cap, approval_threshold_bytes=min(deployment.approval_threshold_bytes, cap))


def nominal_bytes(budget: AcquisitionBudget, profile: TestProfile) -> int:
    """What the planner may fill: the effective cap less the profile's reserve, or the whole cap without
    one. The reserve applies to the cap that binds, so a tighter deployment keeps it too."""
    if profile.budget is None:
        return budget.max_bytes
    return math.floor(budget.max_bytes * (1 - profile.budget.reserve_fraction))


def cutout_bytes(side_arcsec: float) -> int:
    """What a cutout of this side is planned to store (an upper bound for a 32-bit image)."""
    pixels = math.ceil(side_arcsec / CUTOUT_PIXEL_ARCSEC) + 1
    return pixels * pixels * 4 + CUTOUT_HEADER_BYTES


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
        budget = effective_budget(command.budget, profile)
        nominal = nominal_bytes(budget, profile)
        candidates, excluded, selection = self._candidates(field_, profile, requirements, nominal)
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
            budget=budget,
            gaps=gaps,
            constraints={
                "profile": profile.to_document(),
                "seed": command.seed,
                "candidates": [c.to_document() for c in candidates],
                # Only when a profile selects, so plans drafted before selection keep their ids.
                **({"selection": selection, "excluded": [e.to_document() for e in excluded]} if selection else {}),
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
        self, field_: Field, profile: TestProfile, requirements: Sequence[EvidenceRequirement], nominal: int
    ) -> tuple[list[PlanItem], list[PlanExclusion], dict[str, Any]]:
        """Every acquisition that could serve a requirement, read from metadata alone; where a profile
        selects, only the products its selection keeps, with the others and why (INT-025)."""
        out: list[PlanItem] = []
        excluded: list[PlanExclusion] = []
        selections: dict[str, Any] = {}

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
        # Identify's own query serves a test that reads the identify catalogue: the bundles carry
        # its rows, so the plan names the one query under both requirements and fetches it once.
        identify_suffix = f":catalogue:{identify.source_id}"
        for need in catalogue_needs:
            if need.requirement_id.endswith(identify_suffix):
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
        product_needs = [r for r in requirements if r.needs_bytes]
        # Catalogue queries first, so a selection knows every query it shares the budget with.
        catalogues_first = sorted(profile.archives, key=lambda a: a.role is not ArchiveRole.CATALOGUE)
        committed: dict[tuple[str, Operation, str], int] = {}
        for selection in catalogues_first:
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
            ra, dec, radius = field_search_cone(field_)
            index = gateway.search(
                ObservationTarget("field", ra, dec),
                ObservationCriteria(
                    missions=selection.missions,
                    product_types=selection.product_types,
                    radius_arcsec=radius + selection.match_radius_arcsec,
                    min_calibration_level=selection.min_calibration_level,
                ),
            )
            side = selection.cutout_arcsec if isinstance(gateway, ProductCutouts) else None

            def needs_of(record: ObservationRecord) -> list[EvidenceRequirement]:
                served_as = product_kind(record.product_type)
                # A derived requirement names the product type its test reads; a record of another
                # type served as the same ObsCore type is not what the test reads.
                return [
                    need
                    for need in product_needs
                    if served_as is not None
                    and served_as in need.product_types
                    and need.requirement_id.partition(":product:")[2] in ("", record.product_type)
                ]

            records = [r for r in index.records if needs_of(r)]
            priced: dict[str, int] = {}
            if selection.select is not None:
                # A listing cut at the gateway's row limit would hide products from the selection
                # without a record of them, so it is refused rather than planned as complete.
                limit = getattr(gateway, "max_records", None)
                if isinstance(limit, int) and len(index.records) >= limit:
                    raise IntegrationRefused(
                        f"{selection.source_id} listed {len(index.records)} products, its limit of {limit}; "
                        "narrow the field or the match radius so the selection sees every product (INT-025)"
                    )
                # Products share what the catalogue queries leave of the nominal budget.
                # Products share what the catalogue queries and earlier selections leave; products of
                # an archive that does not select are not counted, since nothing says which survive.
                for c in out:
                    if c.operation not in (Operation.CUTOUT, Operation.DOWNLOAD):
                        committed.setdefault((c.source_id, c.operation, c.target), c.expected_bytes)
                spent = sum(committed.values())
                priced, dropped, probes = self._select(selection, gateway, records, (ra, dec, side), nominal, spent)
                records = [r for r in records if r.product.product_id in priced]
                for pid, size in priced.items():
                    committed[
                        (selection.source_id, Operation.CUTOUT if side is not None else Operation.DOWNLOAD, pid)
                    ] = size
                excluded.extend(dropped)
                selections[selection.source_id] = {
                    "policy": selection.select.to_document(),
                    "nominal_bytes": nominal,
                    "spent_before_bytes": spent,
                    "probes": probes,
                }
            for record in records:
                for need in needs_of(record):
                    size = record.size_bytes if record.size_bytes is not None else UNKNOWN_SIZE_BYTES
                    if record.product.product_id in priced:
                        size = priced[record.product.product_id]
                    out.append(
                        candidate(
                            selection.source_id,
                            Operation.CUTOUT,
                            cutout_product_id(record.product.product_id, ra, dec, side),
                            size if record.product.product_id in priced else min(size, cutout_bytes(side)),
                            None,
                            record.licence,
                            need,
                        )
                        if side is not None
                        else candidate(
                            selection.source_id,
                            Operation.DOWNLOAD,
                            record.product.product_id,
                            size,
                            None,
                            record.licence,
                            need,
                        )
                    )
        return out, excluded, ({"nominal_bytes": nominal, "archives": selections} if selections else {})

    def _select(
        self,
        selection: ArchiveSelection,
        gateway: object,
        records: Sequence[ObservationRecord],
        cut: tuple[float, float, float | None],
        nominal: int,
        spent: int,
    ) -> tuple[dict[str, int], list[PlanExclusion], list[dict[str, Any]]]:
        """The products a selection keeps and their bytes, the ones it leaves out, and every probe made.

        A cutout is costed at what it reads when the profile asks and the gateway can say from the
        headers; a product the cutout cannot be taken from is left out with the gateway's reason."""
        policy = selection.select
        assert policy is not None
        ra, dec, side = cut
        missions = {m.upper(): n for n, m in enumerate(selection.missions)}
        by_id = {r.product.product_id: r for r in records}
        values = {"mission": lambda r: r.mission, "instrument": lambda r: r.instrument, "band": lambda r: r.band}
        options = [
            ProductOption(
                selection.source_id,
                r.product.product_id,
                tuple((key, values[key](r) or None) for key in policy.one_per),
                missions.get(r.mission.upper(), len(missions)),
                r.single_exposure,
                r.epoch_mjd,
            )
            for r in records
        ]
        probes: list[dict[str, Any]] = []
        probe = policy.measure_reads and side is not None and isinstance(gateway, CutoutCosts)

        def cost(option: ProductOption) -> int | str:
            record = by_id[option.target]
            listed = record.size_bytes if record.size_bytes is not None else UNKNOWN_SIZE_BYTES
            if side is None:
                return listed
            if not probe:
                return min(listed, cutout_bytes(side))
            assert isinstance(gateway, CutoutCosts)
            try:
                priced = gateway.cutout_cost(record.product, ra, dec, side)
            except LookupError as exc:
                probes.append({"target": option.target, "refused": str(exc)})
                return f"the cutout misses the image: {exc}"
            except ValueError as exc:  # RangeUnsupported, a failed probe, and other refusals to cut
                probes.append({"target": option.target, "refused": str(exc)})
                return f"the product cannot be cut: {exc}"
            probes.append(
                {"target": option.target, "read_bytes": priced.read_bytes, "coverage": round(priced.coverage, 4)}
            )
            if priced.coverage < policy.min_coverage:
                return f"the image holds {priced.coverage:.0%} of the cutout, under the profile's {policy.min_coverage:.0%}"
            return priced.read_bytes

        kept, dropped = select_products(
            options,
            cost,
            combined_only=policy.combined_only,
            max_products=policy.max_products,
            nominal_bytes=nominal,
            spent_bytes=spent,
        )
        return kept, list(dropped), probes

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
        if entry is not None:
            # The entry must verify: signed by a key the ledger trusts for its signer (INT-014).
            problems = [p for p in self.ledger.verify() if p.startswith(f"{entry.entry_id}:")]
            if problems:
                raise IntegrationRefused(f"ledger entry {entry.entry_id} does not verify: {'; '.join(problems)}")
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
