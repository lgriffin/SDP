"""Integration ports (Integration Architecture v0.4.0 §9).

Driven: CapabilityRegistry, CapabilityDiscoveryService, AssociationService, plus ProductNormalizer
and ProductValidator, which extend the Investigate ``ProductDialect`` seam rather than run beside it.
Driving: PlanInvestigation, ApprovePlan, ExecutePlan (ADR-020). Each driven port ships a fake and a
contract suite in ``sdp.testing``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from sdp.kernel.domain.association import AssociationGraph, AssociationPolicy, AssociationRecord
from sdp.kernel.domain.integration import (
    AcquisitionBudget,
    AcquisitionPlan,
    CapabilityMatch,
    EvidenceRequirement,
    ProductMetadata,
    SemanticValidationReport,
    SourceCapabilityDescriptor,
)
from sdp.kernel.domain.investigation import FetchedProduct, Field, TestProfile
from sdp.kernel.ports.investigate import CuratedProduct

# ---------------------------------------------------------------------------------------------
# Driven ports


class CapabilityRegistry(Protocol):
    """Descriptors with their verification dates (fake: InMemoryCapabilityRegistry; real:
    PostgresCapabilityRegistry). A descriptor is replaced, never edited in place."""

    def put(self, descriptor: SourceCapabilityDescriptor) -> None: ...

    def get(self, source_id: str) -> SourceCapabilityDescriptor | None: ...

    def all(self) -> Sequence[SourceCapabilityDescriptor]: ...

    def stale(self, older_than: date) -> Sequence[SourceCapabilityDescriptor]:
        """Descriptors verified before ``older_than``: the weekly freshness smoke reads these."""
        ...


class CapabilityDiscoveryService(Protocol):
    """Read descriptors from the VO (RegTAP, VOSI, ObsCore, DataLink, SODA) and match requirements."""

    def describe(self, source_id: str) -> SourceCapabilityDescriptor: ...

    def match(self, field: Field, need: EvidenceRequirement) -> tuple[CapabilityMatch, ...]:
        """One CapabilityMatch per registered source, with a reason per descriptor field (INT-003)."""
        ...


class AssociationService(Protocol):
    """Hypotheses with posteriors from cross-match candidates (ADR-019).

    Pure: records in, an AssociationGraph out, listing every competing hypothesis. The CPU reference
    is BayesFactorAssociation; LikelihoodRatioAssociation is the second implementation (INT-012).
    """

    @property
    def method(self) -> str: ...

    @property
    def method_version(self) -> str: ...

    def associate(self, records: Sequence[AssociationRecord], policy: AssociationPolicy) -> AssociationGraph: ...


@dataclass(frozen=True)
class RawProduct:
    """What a normaliser reads: the product as Investigate fetched it, its registered raw artifact,
    and the metadata rows recorded for it (v0.4.0 §3)."""

    fetched: FetchedProduct
    raw_artifact_id: str
    """The SHA-256 the kernel computed from ``fetched.data`` when it registered the raw artifact."""
    rendered_query: str
    inventory_row: Mapping[str, Any] = field(default_factory=dict)
    """The archive's product-inventory row (``productType``, ``calib_level``, ``dataRights``, ...)."""
    obscore_row: Mapping[str, Any] = field(default_factory=dict)
    """The ObsCore row from the service's ObsTAP view, empty when the service has none."""
    links: tuple[Mapping[str, Any], ...] = ()
    """DataLink rows for the product (``semantics``, ``access_url`` or ``ID``, ``description``)."""

    @property
    def data(self) -> bytes:
        return self.fetched.data

    @property
    def source_id(self) -> str:
        return self.fetched.product.archive


class ProductNormalizer(Protocol):
    """Fills the ObsCore-shaped envelope from ObsCore, inventory and FITS keywords (v0.4.0 §3).

    A dialect in the v0.3.0 sense, one per mission and release. It extends the Investigate
    ``ProductDialect``: ``curate`` stays the dialect's, ``normalize`` adds the envelope (D33).
    """

    def curate(self, product_type: str, raw: FetchedProduct) -> CuratedProduct | None: ...

    @property
    def dialect_id(self) -> str: ...

    @property
    def dialect_version(self) -> str: ...

    def normalize(self, product: RawProduct) -> ProductMetadata: ...


class ProductValidator(Protocol):
    """Checks a filled envelope against the schema and the envelope rules; the report is stored
    beside the product and a failed one blocks curation (INT-006, INT-007)."""

    @property
    def validator_id(self) -> str: ...

    @property
    def validator_version(self) -> str: ...

    def validate(self, metadata: ProductMetadata, product: RawProduct) -> SemanticValidationReport: ...


# ---------------------------------------------------------------------------------------------
# Driving ports


@dataclass(frozen=True)
class PlanInvestigationCommand:
    field: Field
    profile: TestProfile
    budget: AcquisitionBudget
    actor: str = "kernel"


class PlanInvestigation(Protocol):
    """Derive requirements from the profile's tests, match capabilities, select the minimum product
    set and write the content-addressed plan (INT-013). Emits PlanDrafted."""

    def execute(self, command: PlanInvestigationCommand) -> AcquisitionPlan: ...


@dataclass(frozen=True)
class ApprovePlanCommand:
    plan_id: str
    actor: str
    ledger_entry_id: str | None = None
    refuse_reason: str | None = None


class ApprovePlan(Protocol):
    """Budget check; auto-approve below threshold, a signed ledger entry above (INT-014).
    Emits PlanApproved or PlanRefused."""

    def execute(self, command: ApprovePlanCommand) -> AcquisitionPlan: ...


@dataclass(frozen=True)
class ExecutePlanCommand:
    plan_id: str
    actor: str = "kernel"


@dataclass(frozen=True)
class PlanExecution:
    plan_id: str
    acquired: tuple[str, ...]
    """Artifact ids, one per plan item fetched."""
    unavailable: tuple[str, ...]
    """Item ids that could not be fetched, with the reason in the PlanExecuted event."""


class ExecutePlan(Protocol):
    """Checkpointed acquisition through the gateways for approved items only (INT-015).
    Emits DatasetAcquired per item and PlanExecuted with the unavailable-evidence list."""

    def execute(self, command: ExecutePlanCommand) -> PlanExecution: ...
