# Generated from contracts/schemas by contracts/tools/generate_python.sh. Do not edit.

from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated, Any
from . import common_schema


class Kind(StrEnum):
    PREDECLARATION = "predeclaration"
    APPROVAL = "approval"


class Method(StrEnum):
    SSH = "ssh"
    GPG = "gpg"
    TEST_HMAC = "test-hmac"
    UNSIGNED = "unsigned"


class Signature(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Method
    value: str


class LedgerEntry(BaseModel):
    """
    One signed, append-only, hash-chained entry in ledger/*.jsonl: a frozen predeclaration or a reviewer approval of a verdict.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: common_schema.SchemaVersion
    entry_id: Annotated[str, Field(min_length=1)]
    sequence: Annotated[int, Field(ge=0)]
    kind: Kind
    subject_id: Annotated[str, Field(min_length=1)]
    subject_hash: common_schema.Sha256Id
    recorded_at: common_schema.Timestamp
    signer: Annotated[str, Field(min_length=1)]
    signature: Signature
    previous_entry_hash: common_schema.Sha256Id | None
    payload: dict[str, Any]
