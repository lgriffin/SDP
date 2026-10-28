"""The evidence manifest: the durable contract between kernel and harness (§7)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sdp.kernel.domain.dataset import ProductUse
from sdp.kernel.domain.experiment import Experiment
from sdp.kernel.domain.integration import AdapterCompatibilityRecord
from sdp.kernel.domain.predeclaration import FrozenPredeclaration
from sdp.kernel.domain.provenance import ProvGraph
from sdp.kernel.domain.sources import SourceRef

SCHEMA_VERSION = "1.3.0"


def iso(at: datetime | None) -> str | None:
    return at.isoformat().replace("+00:00", "Z") if at else None


def build_manifest(
    experiment: Experiment,
    frozen: FrozenPredeclaration | None,
    input_sources: Mapping[str, SourceRef],
    provenance: ProvGraph,
    output_media_types: Mapping[str, str] | None = None,
    input_products: Mapping[str, ProductUse] | None = None,
    compatibility: Sequence[AdapterCompatibilityRecord] = (),
) -> dict[str, Any]:
    """Every field group of §7: identity, intent, inputs, environment, execution, outputs,
    findings and checks, verdict, provenance.

    An input acquired through an integration adapter names the adapter and product type, and the
    environment carries the compatibility records for those pairs (Integration v0.4.0 §6). Both are
    left out when no input came through one, so earlier manifests keep their shape.
    """
    d = experiment.definition
    media = dict(output_media_types or {})
    products = dict(input_products or {})
    environment = d.environment.to_document()
    if compatibility:
        environment["adapter_compatibility"] = [r.to_document() for r in compatibility]
    predeclaration_doc = (
        frozen.predeclaration.to_document()
        if frozen
        else {
            "schema_version": "1.0.0",
            "predeclaration_id": "unfrozen",
            "protocol_id": d.protocol.name,
            "protocol_version": d.protocol.version,
            "title": "Predeclaration not found among frozen predeclarations",
            "hypotheses": [],
            "gates": [],
            "oracle_tolerances": [],
            "targets": [],
            "parameters": {},
            "notes": None,
        }
    )
    verdict = None
    if experiment.report is not None:
        verdict = {
            **experiment.report.verdict.to_document(),
            "limitations": list(experiment.limitations),
            "approvals": list(experiment.approvals),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "identity": {
            "experiment_id": experiment.experiment_id,
            "protocol_id": d.protocol.name,
            "protocol_version": d.protocol.version,
        },
        "intent": {
            "predeclaration_hash": d.predeclaration_hash,
            "ledger_entry_id": experiment.ledger_entry_id or "none",
            "predeclaration": predeclaration_doc,
        },
        "inputs": [
            {
                "role": b.role,
                "artifact_id": b.artifact_id.value,
                "media_type": b.media_type,
                "source_ref": input_sources[b.role].to_document(),
                "dialect_id": None,
                "acquisition_key": None,
                **(
                    {"adapter": products[b.role].adapter, "product_type": products[b.role].product_type}
                    if b.role in products
                    else {}
                ),
            }
            for b in d.inputs
        ],
        "environment": environment,
        "execution": {
            "seed": d.seed,
            "state": experiment.state.value,
            "parameters": dict(d.parameters),
            "tasks": [
                {
                    "task_id": t.task_id,
                    "task_kind": t.task_kind,
                    "depends_on": list(t.depends_on),
                    "policy": dict(t.policy),
                    "status": t.status,
                    "checkpoint_artifact_id": t.checkpoint.value if t.checkpoint else None,
                    "duration_s": t.duration_s,
                }
                for t in experiment.tasks.values()
            ],
            "started_at": iso(experiment.started_at),
            "finished_at": iso(experiment.finished_at),
            "failure": (
                {"task_id": experiment.failure["task_id"], "reason": experiment.failure["reason"]}
                if experiment.failure
                else ({"task_id": None, "reason": experiment.refusal} if experiment.refusal else None)
            ),
        },
        "outputs": [
            {"name": name, "artifact_id": artifact.value, "media_type": media.get(name, "application/octet-stream")}
            for name, artifact in sorted(experiment.outputs.items())
        ],
        "findings": [f.to_document() for f in experiment.findings] if experiment.findings else [],
        "oracles": (
            [
                {"oracle_id": o.oracle_id, "verified": o.verified, "note": o.note}
                for _, o in sorted(experiment.findings.oracles.items())
            ]
            if experiment.findings
            else []
        ),
        "checks": [c.to_document() for c in experiment.report.checks] if experiment.report else [],
        "verdict": verdict,
        "provenance": provenance.to_prov_json(),
    }
