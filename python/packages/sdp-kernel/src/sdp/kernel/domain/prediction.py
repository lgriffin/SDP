"""Predictions made by a model under stated assumptions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import astropy.units as u

from sdp.kernel.domain.ids import check_semver
from sdp.kernel.domain.quantity import quantity_document, quantity_from_document


@dataclass(frozen=True)
class Prediction:
    """Model id and version, parameters, predicted Quantity and geometry assumptions.

    The model version is part of identity: a prediction from v1.1 is a different prediction.
    """

    model_id: str
    model_version: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    predicted: u.Quantity | None = None
    geometry_assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check_semver(self.model_version)

    @property
    def identity(self) -> tuple[str, str]:
        return (self.model_id, self.model_version)

    def to_document(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "parameters": dict(self.parameters),
            "predicted": quantity_document(self.predicted) if self.predicted is not None else None,
            "geometry_assumptions": list(self.geometry_assumptions),
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> Prediction:
        predicted = doc.get("predicted")
        return cls(
            model_id=doc["model_id"],
            model_version=doc["model_version"],
            parameters=dict(doc.get("parameters", {})),
            predicted=quantity_from_document(predicted) if predicted else None,
            geometry_assumptions=tuple(doc.get("geometry_assumptions", ())),
        )
