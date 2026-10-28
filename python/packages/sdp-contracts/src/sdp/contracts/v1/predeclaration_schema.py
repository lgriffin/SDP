# Generated from contracts/schemas by contracts/tools/generate_python.sh. Do not edit.

from __future__ import annotations

from typing import Annotated, Any
from pydantic import BaseModel, ConfigDict, Field
from . import common_schema
from enum import StrEnum


class Prediction(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    model_id: Annotated[str, Field(min_length=1)]
    model_version: common_schema.SchemaVersion
    parameters: dict[str, Any]
    predicted: common_schema.Quantity | None = None
    geometry_assumptions: list[str]


class HypothesisRef(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    hypothesis_id: Annotated[str, Field(pattern="^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    version: Annotated[int, Field(ge=1)]
    statement: Annotated[str, Field(min_length=1)]
    predictions: list[Prediction]
    evaluation_criteria: list[str]


class OracleTolerance(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    oracle_id: Annotated[str, Field(min_length=1)]
    rtol: Annotated[float, Field(ge=0.0)]
    atol: Annotated[float, Field(ge=0.0)]
    unit: str | None = None


class Kind(StrEnum):
    REGION = "region"
    SOURCE = "source"


class Target(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    target_id: Annotated[str, Field(min_length=1)]
    kind: Kind
    reason: Annotated[str, Field(min_length=1)]
    spec: dict[str, Any]


class Predeclaration(BaseModel):
    """
    Hypotheses, gates, oracle tolerances and targets, hashed over canonical JSON and frozen before any data are touched (ADR-004).
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: common_schema.SchemaVersion
    predeclaration_id: Annotated[str, Field(pattern="^[a-z0-9][a-z0-9._-]*$")]
    protocol_id: Annotated[str, Field(pattern="^[a-z0-9][a-z0-9._-]*$")]
    protocol_version: common_schema.SchemaVersion
    title: Annotated[str, Field(min_length=1)]
    hypotheses: Annotated[list[HypothesisRef], Field(min_length=1)]
    gates: Annotated[list[common_schema.Gate], Field(min_length=1)]
    oracle_tolerances: list[OracleTolerance]
    targets: list[Target]
    parameters: dict[str, Any]
    notes: str | None = None
