# Generated from contracts/schemas by contracts/tools/generate_python.sh. Do not edit.

from __future__ import annotations

from typing import Annotated, Any
from pydantic import BaseModel, ConfigDict, Field
from . import adapter_compatibility_schema, common_schema, predeclaration_schema
from enum import StrEnum


class Identity(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    experiment_id: Annotated[str, Field(min_length=1)]
    protocol_id: Annotated[str, Field(min_length=1)]
    protocol_version: common_schema.SchemaVersion


class ProductType(StrEnum):
    """
    The product type the experiment used this input as (1.3.0).
    """

    IMAGE = "image"
    SPECTRUM = "spectrum"
    CUBE = "cube"
    TIMESERIES = "timeseries"
    MEASUREMENTS = "measurements"
    CATALOGUE = "catalogue"


class Input(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    role: Annotated[str, Field(min_length=1)]
    artifact_id: common_schema.Sha256Id
    media_type: str
    source_ref: common_schema.SourceRef
    dialect_id: str | None = None
    acquisition_key: common_schema.Sha256Id | None = None
    adapter: Annotated[
        str | None,
        Field(
            description="The SDP adapter that acquired this input, when it came through an integration adapter (1.3.0); the harness checks it against environment.adapter_compatibility (INT-016)."
        ),
    ] = None
    product_type: Annotated[
        ProductType | None,
        Field(description="The product type the experiment used this input as (1.3.0)."),
    ] = None


class Backend(StrEnum):
    CPU = "cpu"
    CUDA = "cuda"


class Status(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Task(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    task_id: Annotated[str, Field(min_length=1)]
    task_kind: Annotated[str, Field(min_length=1)]
    depends_on: list[str]
    policy: common_schema.NumericalPolicy
    status: Status
    checkpoint_artifact_id: common_schema.Sha256Id | None = None
    duration_s: Annotated[float | None, Field(ge=0.0)] = None


class State(StrEnum):
    DEFINED = "defined"
    REFUSED = "refused"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETED = "completed"
    VALIDATED = "validated"
    APPROVED = "approved"
    PUBLISHED = "published"


class Failure(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    task_id: str | None
    reason: str


class Execution(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    seed: int
    state: State
    parameters: dict[str, Any] | None = None
    tasks: list[Task]
    started_at: common_schema.Timestamp | None = None
    finished_at: common_schema.Timestamp | None = None
    failure: Failure | None = None


class Output(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str, Field(min_length=1)]
    artifact_id: common_schema.Sha256Id
    media_type: str


class Finding(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str, Field(min_length=1)]
    value: float | bool | str | list[Any] | dict[str, Any] | None
    uncertainty: float | None = None
    unit: str | None = None


class Approval(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    ledger_entry_id: str
    reviewer: str
    recorded_at: common_schema.Timestamp


class ManifestVerdict(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    status: common_schema.VerdictStatus
    protocol_version: common_schema.SchemaVersion
    reasons: list[str]
    limitations: list[str]
    approvals: list[Approval]


class ProvDocument(BaseModel):
    """
    A W3C PROV-JSON document (subset: prefix, entity, activity, agent and the five relations SDP uses).
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    prefix: dict[str, str]
    entity: dict[str, dict[str, Any]]
    activity: dict[str, dict[str, Any]]
    agent: dict[str, dict[str, Any]]
    used: dict[str, dict[str, Any]] | None = None
    wasGeneratedBy: dict[str, dict[str, Any]] | None = None
    wasAssociatedWith: dict[str, dict[str, Any]] | None = None
    wasDerivedFrom: dict[str, dict[str, Any]] | None = None
    wasAttributedTo: dict[str, dict[str, Any]] | None = None


class OracleRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    oracle_id: Annotated[str, Field(min_length=1)]
    verified: bool
    note: str | None = None


class Environment(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    code_revision: Annotated[str, Field(min_length=1)]
    lock_digest: Annotated[str, Field(min_length=1)]
    image_digest: str | None = None
    backend: Backend
    driver_version: str | None = None
    cuda_version: str | None = None
    agreement_test_id: str | None = None
    python_version: str
    platform: str
    constants_version: str | None = None
    packages: dict[str, str] | None = None
    adapter_compatibility: list[adapter_compatibility_schema.AdapterCompatibilityRecord] | None = None


class Intent(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    predeclaration_hash: common_schema.Sha256Id
    ledger_entry_id: Annotated[str, Field(min_length=1)]
    predeclaration: predeclaration_schema.Predeclaration
    plan_artifact_id: common_schema.Sha256Id | None = None


class EvidenceManifest(BaseModel):
    """
    The durable contract between the kernel and the harness: one document per experiment, written atomically to the NAS and mirrored in PostgreSQL (§7).
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: common_schema.SchemaVersion
    identity: Identity
    intent: Intent
    inputs: list[Input]
    environment: Environment
    execution: Execution
    outputs: list[Output]
    findings: list[Finding]
    oracles: Annotated[
        list[OracleRecord] | None,
        Field(
            description="Oracle verification status as the findings recorded it, so the harness can re-derive every check (TST-007). Added in 1.1.0; absent means no oracle records."
        ),
    ] = None
    checks: list[common_schema.Check]
    verdict: ManifestVerdict | None
    provenance: ProvDocument
