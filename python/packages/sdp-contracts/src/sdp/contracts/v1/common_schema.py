# Generated from contracts/schemas by contracts/tools/generate_python.sh. Do not edit.

from __future__ import annotations

from typing import Annotated, Any
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel
from enum import StrEnum


class Common(RootModel[Any]):
    root: Annotated[
        Any,
        Field(
            description="Shared definitions referenced by every SDP contract schema.",
            title="Common",
        ),
    ]


class SchemaVersion(RootModel[str]):
    root: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$")]


class Sha256Id(RootModel[str]):
    root: Annotated[str, Field(pattern="^sha256:[0-9a-f]{64}$")]


class Timestamp(RootModel[AwareDatetime]):
    root: AwareDatetime


class Quantity(BaseModel):
    """
    A physical value as it crosses the boundary: value plus astropy unit string.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    value: float
    unit: str


class Dtype(StrEnum):
    FLOAT32 = "float32"
    FLOAT64 = "float64"
    INT64 = "int64"
    BOOL = "bool"
    OBJECT = "object"


class Determinism(StrEnum):
    BITWISE = "bitwise"
    TOLERANCE = "tolerance"
    STATISTICAL = "statistical"


class SeedHandling(StrEnum):
    NONE = "none"
    EXPLICIT = "explicit"
    DERIVED = "derived"


class NumericalPolicy(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    dtype: Dtype
    rtol: Annotated[float, Field(ge=0.0)]
    atol: Annotated[float, Field(ge=0.0)]
    determinism: Determinism
    seed_handling: SeedHandling


class Kind(StrEnum):
    ARCHIVE = "archive"
    DOCUMENT = "document"
    LOCAL = "local"
    SYNTHETIC = "synthetic"


class SourceRef(BaseModel):
    """
    Identity of an archive release, a document, a local store or a synthetic generator. Archives must name a release; documents must name a DOI or a bibliographic reference.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Kind
    identity: Annotated[str, Field(min_length=1)]
    release_label: Annotated[str | None, Field(pattern="^[a-z0-9]+:[A-Za-z0-9._-]+$")] = None
    query_text: str | None = None
    doi: str | None = None
    bibcode: str | None = None
    licence: Annotated[str, Field(min_length=1)]
    citation: Annotated[str, Field(min_length=1)]


class Comparator(StrEnum):
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    EQ = "eq"
    NE = "ne"


class Severity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"


class GateId(RootModel[str]):
    root: Annotated[str, Field(pattern="^[A-Z]{2,4}-G-[0-9]{2}$")]


class Gate(BaseModel):
    """
    A predeclared check: finding <comparator> threshold.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    gate_id: GateId
    description: Annotated[str, Field(min_length=1)]
    finding: Annotated[str, Field(min_length=1)]
    comparator: Comparator
    threshold: float | bool
    severity: Severity
    oracle_ids: list[str]


class CheckOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    MISSING = "missing"
    ORACLE_UNVERIFIED = "oracle_unverified"


class Check(BaseModel):
    """
    A gate evaluated against a finding value.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    gate_id: GateId
    finding: str
    value: float | bool | None
    unit: str | None = None
    comparator: Comparator
    threshold: float | bool
    severity: Severity
    oracle_ids: list[str]
    oracles_verified: bool
    outcome: CheckOutcome


class VerdictStatus(StrEnum):
    VERIFIED = "VERIFIED"
    CANDIDATE = "CANDIDATE"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class Verdict(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    status: VerdictStatus
    protocol_version: SchemaVersion
    reasons: list[str]
