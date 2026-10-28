"""Domain <-> DTO mappers (CTR-006). Each maps through the domain's own document form, which the
schemas describe, so a mapper is a validation step plus a typed model, never a second encoding."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from sdp.contracts import dump, load
from sdp.contracts.v1.dataset_artifact_schema import DatasetArtifact as DatasetArtifactDto
from sdp.contracts.v1.ledger_entry_schema import LedgerEntry as LedgerEntryDto
from sdp.contracts.v1.predeclaration_schema import Predeclaration as PredeclarationDto
from sdp.contracts.v1.validation_report_schema import ValidationReport as ValidationReportDto
from sdp.kernel.domain.dataset import DatasetArtifact
from sdp.kernel.domain.predeclaration import Predeclaration
from sdp.kernel.domain.verdict import ValidationReport
from sdp.kernel.ports.ledger import LedgerEntry


def _to(name: str, document: dict[str, Any], model: type[BaseModel]) -> Any:
    dto = load(name, document)
    assert isinstance(dto, model)
    return dto


def predeclaration_to_dto(value: Predeclaration) -> PredeclarationDto:
    dto: PredeclarationDto = _to("predeclaration", value.to_document(), PredeclarationDto)
    return dto


def predeclaration_from_dto(dto: PredeclarationDto) -> Predeclaration:
    return Predeclaration.from_document(dump(dto))


def report_to_dto(value: ValidationReport) -> ValidationReportDto:
    dto: ValidationReportDto = _to("validation-report", value.to_document(), ValidationReportDto)
    return dto


def report_from_dto(dto: ValidationReportDto) -> ValidationReport:
    return ValidationReport.from_document(dump(dto))


def ledger_entry_to_dto(value: LedgerEntry) -> LedgerEntryDto:
    dto: LedgerEntryDto = _to("ledger-entry", value.to_document(), LedgerEntryDto)
    return dto


def ledger_entry_from_dto(dto: LedgerEntryDto) -> LedgerEntry:
    return LedgerEntry.from_document(dump(dto))


def dataset_to_dto(value: DatasetArtifact) -> DatasetArtifactDto:
    dto: DatasetArtifactDto = _to("dataset-artifact", value.to_document(), DatasetArtifactDto)
    return dto


def dataset_from_dto(dto: DatasetArtifactDto) -> DatasetArtifact:
    return DatasetArtifact.from_document(dump(dto))
