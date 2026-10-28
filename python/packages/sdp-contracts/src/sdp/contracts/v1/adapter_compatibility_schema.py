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


class Kind(StrEnum):
    CONTRACT = "contract"
    OPERATIONAL = "operational"
    SEMANTIC = "semantic"
    SCIENTIFIC = "scientific"


class Result(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Kind
    passed: bool
    ci_run_id: Annotated[str, Field(min_length=1)]


class LiveSmokeOn(RootModel[str]):
    root: Annotated[str, Field(pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class AdapterCompatibilityRecord(BaseModel):
    """
    Four-kind conformance for one adapter and one product type, carried in the manifest's environment group and checked by the harness before any verdict (v0.4.0 §6, ADR-021).
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: common_schema.SchemaVersion
    adapter: Annotated[str, Field(min_length=1)]
    adapter_version: common_schema.SchemaVersion
    product_type: ProductType
    upstream_versions: dict[str, str]
    results: list[Result]
    passed: bool
    live_smoke_on: LiveSmokeOn | None
    fixture_digests: list[common_schema.Sha256Id]
