"""Boundary DTOs generated from contracts/schemas (ADR-007).

The JSON Schemas are the single source of truth. ``v1`` holds Pydantic models generated from
them; ``schemas`` holds a byte-identical copy of the schemas so validation needs no repository
checkout. :func:`load` validates against the schema first (which carries the conditional rules
Pydantic cannot express, and reports JSON-pointer paths), then builds the typed model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel
from referencing import Registry, Resource

from sdp.contracts.v1.canonical_astrometry_schema import CanonicalAstrometryRow
from sdp.contracts.v1.dataset_artifact_schema import DatasetArtifact
from sdp.contracts.v1.evidence_manifest_schema import EvidenceManifest
from sdp.contracts.v1.ledger_entry_schema import LedgerEntry
from sdp.contracts.v1.predeclaration_schema import Predeclaration
from sdp.contracts.v1.validation_report_schema import ValidationReport

SCHEMA_MAJOR = 1

MODELS: dict[str, type[BaseModel]] = {
    "canonical-astrometry": CanonicalAstrometryRow,
    "dataset-artifact": DatasetArtifact,
    "evidence-manifest": EvidenceManifest,
    "ledger-entry": LedgerEntry,
    "predeclaration": Predeclaration,
    "validation-report": ValidationReport,
}


@dataclass(frozen=True)
class ContractError:
    path: str
    message: str


class ContractViolation(ValueError):
    def __init__(self, name: str, errors: list[ContractError]) -> None:
        self.errors = errors
        super().__init__(f"{name}: " + "; ".join(f"{e.path or '/'}: {e.message}" for e in errors))


@cache
def _schemas() -> dict[str, dict[str, Any]]:
    folder = resources.files("sdp.contracts") / "schemas"
    return {
        entry.name.removesuffix(".schema.json"): json.loads(entry.read_text("utf-8"))
        for entry in folder.iterdir()
        if entry.name.endswith(".schema.json")
    }


@cache
def _validator(name: str) -> Draft202012Validator:
    schemas = _schemas()
    pairs: list[tuple[str, Resource[Any]]] = []
    for key, document in schemas.items():
        resource: Resource[Any] = Resource.from_contents(document)
        pairs += [(f"{key}.schema.json", resource), (document["$id"], resource)]
    return Draft202012Validator(schemas[name], registry=Registry().with_resources(pairs))


def _pointer(error: ValidationError) -> str:
    parts = [str(p) for p in error.absolute_path]
    if error.validator == "required":
        parts.append(error.message.split("'")[1])
    return "/" + "/".join(parts) if parts else ""


def _leaves(error: ValidationError) -> list[ValidationError]:
    if not error.context:
        return [error]
    return [leaf for sub in error.context for leaf in _leaves(sub)]


def validate(name: str, document: Any) -> list[ContractError]:
    """Every violation of the named schema, as JSON pointers into ``document``."""
    if name not in MODELS:
        raise KeyError(f"unknown contract {name!r}; known: {sorted(MODELS)}")
    found: dict[tuple[str, str], ContractError] = {}
    for error in _validator(name).iter_errors(document):
        for leaf in [error, *_leaves(error)]:
            item = ContractError(_pointer(leaf), leaf.message)
            found[(item.path, item.message)] = item
    return sorted(found.values(), key=lambda e: (e.path, e.message))


def load(name: str, document: Any) -> BaseModel:
    """Validate, then build the typed model. Raises :class:`ContractViolation`."""
    errors = validate(name, document)
    if errors:
        raise ContractViolation(name, errors)
    return MODELS[name].model_validate(document)


def dump(model: BaseModel) -> dict[str, Any]:
    """The model as a JSON document, omitting only fields the input did not set."""
    data: dict[str, Any] = model.model_dump(mode="json", exclude_unset=True, by_alias=True)
    return data


def schema_major(document: dict[str, Any]) -> int:
    version = str(document.get("schema_version", "0.0.0"))
    return int(version.split(".", 1)[0])


__all__ = [
    "MODELS",
    "SCHEMA_MAJOR",
    "ContractError",
    "ContractViolation",
    "dump",
    "load",
    "schema_major",
    "validate",
]
