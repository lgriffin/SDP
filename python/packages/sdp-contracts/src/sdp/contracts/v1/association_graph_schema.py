# Generated from contracts/schemas by contracts/tools/generate_python.sh. Do not edit.

from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, RootModel
from enum import StrEnum
from . import common_schema


class Member(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class Hypothes(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    hypothesis_id: Annotated[str, Field(min_length=1)]
    members: Annotated[list[Member], Field(min_length=1)]


class Covariance(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    sigma_ra_arcsec: Annotated[float, Field(gt=0.0)]
    sigma_dec_arcsec: Annotated[float, Field(gt=0.0)]
    rho: Annotated[float, Field(ge=-1.0, le=1.0)]


class Photometry(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    band: Annotated[str, Field(min_length=1)]
    magnitude: float
    sigma: float


class Motion(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    pmra_mas_yr: float
    pmdec_mas_yr: float
    parallax_mas: float | None
    sigma_pmra_mas_yr: float
    sigma_pmdec_mas_yr: float
    sigma_parallax_mas: float | None


class Detection(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["detection"]
    record_id: Annotated[str, Field(min_length=1)]
    product_id: Annotated[str, Field(min_length=1)]
    extension: Annotated[str, Field(min_length=1)]
    ra_deg: float
    dec_deg: float
    covariance: Covariance
    epoch_jyear: float
    resolution_arcsec: Annotated[float, Field(gt=0.0)]
    instrument: Annotated[str, Field(min_length=1)]
    photometry: list[Photometry]


class CatalogueSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["catalogue_source"]
    record_id: Annotated[str, Field(min_length=1)]
    catalogue: Annotated[str, Field(min_length=1)]
    release_label: Annotated[str, Field(min_length=1)]
    source_key: Annotated[str, Field(min_length=1)]
    ra_deg: float
    dec_deg: float
    covariance: Covariance
    epoch_jyear: float
    motion: Motion | None
    resolution_arcsec: Annotated[float, Field(ge=0.0)]
    photometry: list[Photometry]


class CompetingItem(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class State(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class Policy(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    policy_id: Annotated[str, Field(min_length=1)]
    version: common_schema.SchemaVersion
    common_epoch_jyear: float
    search_radius_arcsec: Annotated[float, Field(gt=0.0)]
    min_posterior: Annotated[float, Field(ge=0.0, lt=0.5)]
    photometric_terms: bool


class Association(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    record_id: Annotated[str, Field(min_length=1)]
    hypothesis_id: Annotated[str, Field(min_length=1)]
    method: Annotated[str, Field(min_length=1)]
    method_version: common_schema.SchemaVersion
    prior: Annotated[float, Field(ge=0.0, le=1.0)]
    likelihood: float
    posterior: Annotated[float, Field(ge=0.0, le=1.0)]
    bayes_factor: float | None
    competing: list[CompetingItem]
    evidence: dict[str, Any]
    state: State
    ledger_entry_id: str | None
    reviewer: str | None
    reason: str


class AssociationGraph(BaseModel):
    """
    Detections, catalogue sources, object hypotheses and every retained association with its posterior (v0.4.0 §4, ADR-019).
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: common_schema.SchemaVersion
    graph_id: common_schema.Sha256Id
    method: Annotated[str, Field(min_length=1)]
    method_version: common_schema.SchemaVersion
    policy: Policy
    source_density_per_arcsec2: Annotated[float, Field(ge=0.0)]
    records: list[Detection | CatalogueSource]
    hypotheses: list[Hypothes]
    associations: list[Association]
