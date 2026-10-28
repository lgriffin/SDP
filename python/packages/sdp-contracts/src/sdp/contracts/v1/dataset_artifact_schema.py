# Generated from contracts/schemas by contracts/tools/generate_python.sh. Do not edit.

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field
from . import common_schema


class Kind(StrEnum):
    RAW = "raw"
    CURATED = "curated"
    DERIVED = "derived"


class Status(StrEnum):
    ACQUIRED = "acquired"
    REJECTED = "rejected"
    CURATED = "curated"


class DialectRef(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    dialect_id: Annotated[str, Field(min_length=1)]
    dialect_version: common_schema.SchemaVersion


class AcquisitionPart(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    part: Annotated[int, Field(ge=0)]
    artifact_id: common_schema.Sha256Id
    byte_count: Annotated[int, Field(ge=0)]
    row_count: Annotated[int, Field(ge=0)]


class AcquisitionRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    rendered_query: Annotated[str, Field(min_length=1)]
    idempotency_key: common_schema.Sha256Id
    source_release: Annotated[str, Field(min_length=1)]
    acquired_at: common_schema.Timestamp
    checksum: common_schema.Sha256Id
    byte_count: Annotated[int, Field(ge=0)]
    parts: list[AcquisitionPart]


class ProductType(StrEnum):
    IMAGE = "image"
    SPECTRUM = "spectrum"
    CUBE = "cube"
    TIMESERIES = "timeseries"
    MEASUREMENTS = "measurements"
    CATALOGUE = "catalogue"


class ProductUse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    adapter: Annotated[str, Field(min_length=1)]
    adapter_version: common_schema.SchemaVersion
    product_type: ProductType


class DatasetArtifact(BaseModel):
    """
    Metadata for one content-addressed dataset: raw artifacts are immutable, curated artifacts cite their raw parents and the dialect that produced them.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: common_schema.SchemaVersion
    artifact_id: common_schema.Sha256Id
    kind: Kind
    status: Status
    media_type: Annotated[str, Field(min_length=1)]
    byte_count: Annotated[int, Field(ge=0)]
    source_ref: common_schema.SourceRef
    dialect: DialectRef | None
    parents: list[common_schema.Sha256Id]
    acquisition: AcquisitionRecord | None
    rejection_reason: str | None = None
    product: Annotated[
        ProductUse | None,
        Field(
            description="Which adapter acquired the artifact and as which product type (1.1.0); the kernel copies it to the manifest's inputs so admission can be checked (INT-016)."
        ),
    ] = None
