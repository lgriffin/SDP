# Generated from contracts/schemas by contracts/tools/generate_python.sh. Do not edit.

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, RootModel
from . import common_schema


class Origin(StrEnum):
    REGISTRY = "registry"
    HAND_WRITTEN = "hand-written"


class Protocol(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    standard: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]
    base_url: Annotated[str, Field(min_length=1)]


class DataModel(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


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


class Mission(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class Instrument(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class CalibrationLevel(RootModel[int]):
    root: Annotated[int, Field(ge=0, le=4)]


class Region(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    shape: Literal["cone"]
    ra_deg: Annotated[float, Field(ge=0.0, lt=360.0)]
    dec_deg: Annotated[float, Field(ge=-90.0, le=90.0)]
    radius_deg: Annotated[float, Field(gt=0.0, le=10.0)]


class Coverage(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    all_sky: bool | None
    regions: list[Region]
    t_min_mjd: float | None
    t_max_mjd: float | None
    em_min_m: float | None
    em_max_m: float | None


class Operation(StrEnum):
    CONE = "cone"
    REGION = "region"
    CRITERIA = "criteria"
    PRODUCT_LISTING = "product_listing"
    DOWNLOAD = "download"
    CUTOUT = "cutout"


class CutoutSupport(StrEnum):
    SODA = "soda"
    ASTROCUT = "astrocut"
    PER_PRODUCT = "per-product"
    NATIVE = "native"
    NONE = "none"


class Access(StrEnum):
    PUBLIC = "public"
    EXCLUSIVE_ACCESS = "exclusive_access"
    LOGIN = "login"


class Limits(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    max_rows: int | None
    page_size: int | None
    requests_per_second: float | None
    max_concurrent: int | None
    upload: bool
    throughput_bytes_per_s: float | None


class Rights(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    licence: Annotated[str, Field(min_length=1)]
    citation: Annotated[str, Field(min_length=1)]
    acknowledgement: str


class LastCompatibilityCheck(RootModel[str]):
    root: Annotated[str, Field(pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class Adapter(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str, Field(min_length=1)]
    version: common_schema.SchemaVersion
    last_compatibility_check: LastCompatibilityCheck | None
    conformance: dict[str, bool]


class SourceCapabilityDescriptor(BaseModel):
    """
    What a source can do, read from RegTAP, VOSI and ObsCore where published, or hand-written for a native service (v0.4.0 §2, ADR-017).
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: common_schema.SchemaVersion
    source_id: Annotated[str, Field(min_length=1)]
    ivoid: str | None
    title: Annotated[str, Field(min_length=1)]
    publisher: Annotated[str, Field(min_length=1)]
    origin: Origin
    verified_on: Annotated[str, Field(pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
    protocols: list[Protocol]
    data_models: list[DataModel]
    product_types: list[ProductType]
    missions: list[Mission]
    instruments: list[Instrument]
    calibration_levels: Annotated[
        list[CalibrationLevel] | None,
        Field(
            description="ObsCore calib_level values the source serves (1.1.0); empty when it does not publish them, which makes a calibration requirement partial."
        ),
    ] = None
    coverage: Coverage
    operations: list[Operation]
    cutout_support: dict[str, CutoutSupport]
    access: Access
    limits: Limits
    rights: Rights
    adapter: Adapter | None
