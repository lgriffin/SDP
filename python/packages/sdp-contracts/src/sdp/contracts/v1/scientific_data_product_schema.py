# Generated from contracts/schemas by contracts/tools/generate_python.sh. Do not edit.

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, RootModel
from . import common_schema


class ProductType(StrEnum):
    IMAGE = "image"
    SPECTRUM = "spectrum"
    CUBE = "cube"
    TIMESERIES = "timeseries"
    MEASUREMENTS = "measurements"
    CATALOGUE = "catalogue"
    PREVIEW = "preview"
    THUMBNAIL = "thumbnail"
    AUXILIARY = "auxiliary"


class TargetPosition(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    ra_deg: float | None
    dec_deg: float | None
    frame: Annotated[str, Field(min_length=1)]


class Time(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    start_mjd: float | None
    end_mjd: float | None
    scale: Annotated[str, Field(min_length=1)]


class CalibrationLevel(RootModel[int]):
    root: Annotated[int, Field(ge=0, le=4)]


class Dimension(RootModel[int]):
    root: Annotated[int, Field(ge=0)]


class Role(StrEnum):
    DATA = "data"
    ERROR = "error"
    QUALITY = "quality"
    VARIANCE = "variance"
    OTHER = "other"


class Extension(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str, Field(min_length=1)]
    role: Role
    unit: str | None


class AccessStatus(StrEnum):
    PUBLIC = "public"
    EXCLUSIVE_ACCESS = "exclusive_access"
    LOGIN = "login"


class ExclusiveAccessEnds(RootModel[str]):
    root: Annotated[str, Field(pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class Rights(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    licence: Annotated[str, Field(min_length=1)]
    citation: Annotated[str, Field(min_length=1)]
    acknowledgement: str


class Semantics(StrEnum):
    THIS = "#this"
    PROGENITOR = "#progenitor"
    AUXILIARY = "#auxiliary"
    PREVIEW = "#preview"
    CALIBRATION = "#calibration"
    DERIVATION = "#derivation"


class Relationship(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    semantics: Semantics
    product_id: Annotated[str, Field(min_length=1)]
    description: str


class CalibrationReference(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    keyword: Annotated[str, Field(min_length=1)]
    file: Annotated[str, Field(min_length=1)]


class ProductMetadata(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    product_id: Annotated[str, Field(min_length=1)]
    product_type: ProductType
    mission: Annotated[str, Field(min_length=1)]
    instrument: str | None
    detector: str | None
    filter_or_grating: str | None
    observation_id: Annotated[str, Field(min_length=1)]
    programme_id: str | None
    target_position: TargetPosition
    s_region: str | None
    time: Time
    spectral_range_m: Annotated[list[float | None], Field(max_length=2, min_length=2)]
    spatial_resolution_arcsec: float | None
    field_of_view_deg: float | None
    calibration_level: CalibrationLevel | None
    pipeline_version: str | None
    format: Annotated[str, Field(min_length=1)]
    dimensions: list[Dimension]
    units: str | None
    extensions: list[Extension]
    has_wcs: bool
    uncertainty_present: bool
    quality_present: bool
    access_status: AccessStatus
    exclusive_access_ends: ExclusiveAccessEnds | None
    rights: Rights
    source_id: Annotated[str, Field(min_length=1)]
    query: str
    checksum: common_schema.Sha256Id
    size_bytes: Annotated[int, Field(ge=0)]
    relationships: list[Relationship]
    calibration_references: list[CalibrationReference]


class Check(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    field: Annotated[str, Field(min_length=1)]
    passed: bool
    detail: str


class SemanticValidationReport(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    product_id: Annotated[str, Field(min_length=1)]
    validator_id: Annotated[str, Field(min_length=1)]
    validator_version: common_schema.SchemaVersion
    envelope_digest: Annotated[
        common_schema.Sha256Id | None,
        Field(
            description="The digest of the envelope the checks ran on (1.1.0); a report never validates a changed envelope."
        ),
    ] = None
    passed: bool
    checks: list[Check]


class ScientificDataProduct(BaseModel):
    """
    A raw artifact kept byte for byte plus its ObsCore 1.1-shaped envelope and semantic validation report (v0.4.0 §3, ADR-018).
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: common_schema.SchemaVersion
    raw_artifact_id: common_schema.Sha256Id
    curated_artifact_id: common_schema.Sha256Id | None
    metadata: ProductMetadata
    validation: SemanticValidationReport | None
