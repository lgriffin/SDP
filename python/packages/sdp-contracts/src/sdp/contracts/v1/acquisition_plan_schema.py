# Generated from contracts/schemas by contracts/tools/generate_python.sh. Do not edit.

from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, RootModel
from typing_extensions import TypeAliasType
from enum import StrEnum
from . import common_schema


RequestsPerSourceAdditionalProperty = TypeAliasType("RequestsPerSourceAdditionalProperty", Annotated[int, Field(ge=0)])


class Cost(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    total_bytes: Annotated[int, Field(ge=0)]
    requests_per_source: dict[str, RequestsPerSourceAdditionalProperty]
    estimated_wall_seconds: Annotated[float, Field(ge=0.0)]
    nas_headroom_after_bytes: int | None


class Budget(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    max_bytes: Annotated[int, Field(ge=0)]
    max_requests_per_source: Annotated[int, Field(ge=0)]
    max_wall_seconds: Annotated[float, Field(gt=0.0)]
    min_nas_headroom_bytes: Annotated[int, Field(ge=0)]
    approval_threshold_bytes: Annotated[int, Field(ge=0)]


class Constraints(BaseModel):
    budget: Budget


class Gap(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    requirement_id: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]


class ProductType(StrEnum):
    IMAGE = "image"
    SPECTRUM = "spectrum"
    CUBE = "cube"
    TIMESERIES = "timeseries"
    MEASUREMENTS = "measurements"
    CATALOGUE = "catalogue"


class MinCalibrationLevel(RootModel[int]):
    root: Annotated[int, Field(ge=0, le=4)]


class Mission(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class Instrument(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class Operation(StrEnum):
    CONE = "cone"
    REGION = "region"
    CRITERIA = "criteria"
    PRODUCT_LISTING = "product_listing"
    DOWNLOAD = "download"
    CUTOUT = "cutout"


class EvidenceRequirement(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    requirement_id: Annotated[str, Field(min_length=1)]
    test_kind: Annotated[str, Field(min_length=1)]
    product_types: Annotated[list[ProductType], Field(min_length=1)]
    min_calibration_level: MinCalibrationLevel | None
    missions: list[Mission]
    instruments: list[Instrument]
    epoch_min_mjd: float | None
    epoch_max_mjd: float | None
    em_min_m: float | None
    em_max_m: float | None
    operations: list[Operation]
    needs_bytes: bool
    public_only: bool


class Status(StrEnum):
    SATISFIED = "satisfied"
    PARTIAL = "partial"
    UNSATISFIED = "unsatisfied"


class Outcome(StrEnum):
    SATISFIED = "satisfied"
    PARTIAL = "partial"
    UNSATISFIED = "unsatisfied"


class Reason(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    field: Annotated[str, Field(min_length=1)]
    outcome: Outcome
    reason: Annotated[str, Field(min_length=1)]


class CapabilityMatch(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    source_id: Annotated[str, Field(min_length=1)]
    requirement_id: Annotated[str, Field(min_length=1)]
    status: Status
    reasons: Annotated[list[Reason], Field(min_length=1)]
    descriptor_verified_on: Annotated[str, Field(pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class PlanItem(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    item_id: Annotated[str, Field(min_length=1)]
    source_id: Annotated[str, Field(min_length=1)]
    operation: Operation
    target: Annotated[str, Field(min_length=1)]
    expected_bytes: Annotated[int, Field(ge=0)]
    expected_rows: int | None
    calibration_level: int | None
    access_status: Literal["public"]
    licence: Annotated[str, Field(min_length=1)]
    requirement_id: Annotated[str, Field(min_length=1)]
    test_kind: Annotated[str, Field(min_length=1)]


class Decision(StrEnum):
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    REFUSED = "refused"


class Intent(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    field: dict[str, Any]
    profile_hash: common_schema.Sha256Id
    planner_id: Annotated[str, Field(min_length=1)]
    planner_version: common_schema.SchemaVersion
    requirements: list[EvidenceRequirement]


class PlanApproval(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    plan_id: common_schema.Sha256Id
    decision: Decision
    actor: Annotated[str, Field(min_length=1)]
    threshold_bytes: Annotated[int, Field(ge=0)]
    ledger_entry_id: str | None
    reason: str


class AcquisitionPlan(BaseModel):
    """
    The content-addressed plan between PlanInvestigation and ApprovePlan: intent, discovery, items, cost, constraints, gaps and approval (v0.4.0 §5, ADR-020).
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: common_schema.SchemaVersion
    plan_id: common_schema.Sha256Id
    intent: Intent
    discovery: list[CapabilityMatch]
    items: list[PlanItem]
    cost: Cost
    constraints: Constraints
    gaps: list[Gap]
    approval: PlanApproval | None
