"""Dossier aggregate: assembled only from a validated experiment (§5)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sdp.kernel.domain.errors import InvalidTransition
from sdp.kernel.domain.experiment import Experiment, ExperimentState
from sdp.kernel.domain.ids import ArtifactId


@dataclass(frozen=True)
class Figure:
    name: str
    artifact_id: ArtifactId
    caption: str
    media_type: str = "image/svg+xml"


@dataclass(frozen=True)
class Dossier:
    dossier_id: str
    experiment_id: str
    manifest_id: ArtifactId
    verdict: str
    findings: Mapping[str, Any]
    limitations: tuple[str, ...]
    figures: tuple[Figure, ...] = ()
    sections: Mapping[str, str] = field(default_factory=dict)
    approvals: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def assemble(
        cls,
        *,
        dossier_id: str,
        experiment: Experiment,
        limitations: tuple[str, ...],
        figures: tuple[Figure, ...] = (),
        sections: Mapping[str, str] | None = None,
    ) -> Dossier:
        if experiment.state not in (ExperimentState.VALIDATED, ExperimentState.APPROVED, ExperimentState.PUBLISHED):
            raise InvalidTransition(
                f"a dossier needs a validated experiment, {experiment.experiment_id} is {experiment.state}"
            )
        if experiment.manifest_id is None or experiment.report is None or experiment.findings is None:
            raise InvalidTransition("a dossier needs the experiment's manifest, findings and report")
        return cls(
            dossier_id=dossier_id,
            experiment_id=experiment.experiment_id,
            manifest_id=experiment.manifest_id,
            verdict=experiment.report.verdict.status.value,
            findings={f.name: f.to_document() for f in experiment.findings},
            limitations=limitations,
            figures=figures,
            sections=dict(sections or {}),
            approvals=tuple(experiment.approvals),
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "dossier_id": self.dossier_id,
            "experiment_id": self.experiment_id,
            "manifest_id": self.manifest_id.value,
            "verdict": self.verdict,
            "findings": dict(self.findings),
            "limitations": list(self.limitations),
            "figures": [
                {"name": f.name, "artifact_id": f.artifact_id.value, "caption": f.caption, "media_type": f.media_type}
                for f in self.figures
            ],
            "sections": dict(self.sections),
            "approvals": [dict(a) for a in self.approvals],
        }
