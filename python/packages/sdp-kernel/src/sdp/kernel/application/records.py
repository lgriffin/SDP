"""Lossless JSON records of aggregates, used by every repository adapter (and by the fakes, so
they never hand out shared mutable objects)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sdp.kernel.domain.dossier import Dossier, Figure
from sdp.kernel.domain.experiment import (
    EnvironmentRecord,
    Experiment,
    ExperimentDefinition,
    ExperimentState,
    InputBinding,
    TaskRecord,
)
from sdp.kernel.domain.findings import Finding, Findings, OracleStatus
from sdp.kernel.domain.ids import ArtifactId, ProtocolId
from sdp.kernel.domain.verdict import ValidationReport


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _from_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def findings_to_record(findings: Findings) -> dict[str, Any]:
    return {
        "items": [f.to_document() for f in findings],
        "oracles": [
            {"oracle_id": o.oracle_id, "verified": o.verified, "note": o.note} for o in findings.oracles.values()
        ],
        "narrative": dict(findings.narrative),
    }


def findings_from_record(record: dict[str, Any]) -> Findings:
    return Findings.of(
        *(Finding.from_document(f) for f in record["items"]),
        oracles={
            o["oracle_id"]: OracleStatus(o["oracle_id"], o["verified"], o.get("note", "")) for o in record["oracles"]
        },
        narrative=record.get("narrative", {}),
    )


def experiment_to_record(e: Experiment) -> dict[str, Any]:
    d = e.definition
    return {
        "experiment_id": e.experiment_id,
        "definition": {
            "protocol": {"name": d.protocol.name, "version": d.protocol.version},
            "predeclaration_hash": d.predeclaration_hash,
            "inputs": [
                {"role": b.role, "artifact_id": b.artifact_id.value, "media_type": b.media_type} for b in d.inputs
            ],
            "parameters": dict(d.parameters),
            "seed": d.seed,
            "environment": d.environment.to_document(),
        },
        "state": e.state.value,
        "tasks": [
            {
                "task_id": t.task_id,
                "task_kind": t.task_kind,
                "depends_on": list(t.depends_on),
                "policy": dict(t.policy),
                "status": t.status,
                "checkpoint": t.checkpoint.value if t.checkpoint else None,
                "duration_s": t.duration_s,
            }
            for t in e.tasks.values()
        ],
        "findings": findings_to_record(e.findings) if e.findings is not None else None,
        "report": e.report.to_document() if e.report else None,
        "outputs": {k: v.value for k, v in e.outputs.items()},
        "failure": e.failure,
        "refusal": e.refusal,
        "ledger_entry_id": e.ledger_entry_id,
        "approvals": list(e.approvals),
        "limitations": list(e.limitations),
        "started_at": _dt(e.started_at),
        "finished_at": _dt(e.finished_at),
        "manifest_id": e.manifest_id.value if e.manifest_id else None,
        "bundle_doi": e.bundle_doi,
    }


def experiment_from_record(r: dict[str, Any]) -> Experiment:
    d = r["definition"]
    definition = ExperimentDefinition(
        protocol=ProtocolId(d["protocol"]["name"], d["protocol"]["version"]),
        predeclaration_hash=d["predeclaration_hash"],
        inputs=tuple(InputBinding(b["role"], ArtifactId(b["artifact_id"]), b["media_type"]) for b in d["inputs"]),
        parameters=dict(d["parameters"]),
        seed=int(d["seed"]),
        environment=EnvironmentRecord.from_document(d["environment"]),
    )
    return Experiment(
        experiment_id=r["experiment_id"],
        definition=definition,
        state=ExperimentState(r["state"]),
        tasks={
            t["task_id"]: TaskRecord(
                t["task_id"],
                t["task_kind"],
                tuple(t["depends_on"]),
                dict(t["policy"]),
                t["status"],
                ArtifactId(t["checkpoint"]) if t["checkpoint"] else None,
                t["duration_s"],
            )
            for t in r["tasks"]
        },
        findings=findings_from_record(r["findings"]) if r["findings"] is not None else None,
        report=ValidationReport.from_document(r["report"]) if r["report"] else None,
        outputs={k: ArtifactId(v) for k, v in r["outputs"].items()},
        failure=r["failure"],
        refusal=r["refusal"],
        ledger_entry_id=r["ledger_entry_id"],
        approvals=list(r["approvals"]),
        limitations=list(r["limitations"]),
        started_at=_from_dt(r["started_at"]),
        finished_at=_from_dt(r["finished_at"]),
        manifest_id=ArtifactId(r["manifest_id"]) if r["manifest_id"] else None,
        bundle_doi=r["bundle_doi"],
    )


def dossier_from_record(r: dict[str, Any]) -> Dossier:
    return Dossier(
        dossier_id=r["dossier_id"],
        experiment_id=r["experiment_id"],
        manifest_id=ArtifactId(r["manifest_id"]),
        verdict=r["verdict"],
        findings=dict(r["findings"]),
        limitations=tuple(r["limitations"]),
        figures=tuple(
            Figure(f["name"], ArtifactId(f["artifact_id"]), f["caption"], f["media_type"]) for f in r["figures"]
        ),
        sections=dict(r["sections"]),
        approvals=tuple(r["approvals"]),
    )
