# Generated from contracts/schemas by contracts/tools/generate_python.sh. Do not edit.

from __future__ import annotations

from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field
from . import common_schema


class ValidationReport(BaseModel):
    """
    Every predeclared gate evaluated against the findings, and the verdict that follows from them.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: common_schema.SchemaVersion
    experiment_id: Annotated[str, Field(min_length=1)]
    protocol_id: Annotated[str, Field(min_length=1)]
    protocol_version: common_schema.SchemaVersion
    predeclaration_hash: common_schema.Sha256Id
    checks: list[common_schema.Check]
    verdict: common_schema.Verdict
