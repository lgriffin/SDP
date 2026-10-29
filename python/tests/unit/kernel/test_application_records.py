"""Exact output of the application tier's pure helpers: the RO-Crate metadata, zip layout and verifier,
the checkpoint codec's refusals, the lossless experiment, findings and dossier records, and the
manifest fields that only an unfrozen, refused or untyped experiment exercises."""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import fields, replace
from typing import Any

import numpy as np
import pytest

from sdp.kernel.application import codec
from sdp.kernel.application.manifest import build_manifest, iso
from sdp.kernel.application.records import (
    dossier_from_record,
    experiment_from_record,
    experiment_to_record,
    findings_from_record,
    findings_to_record,
)
from sdp.kernel.application.rocrate import build_crate_metadata, verify_crate, zip_crate
from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.domain.dossier import Figure
from sdp.kernel.domain.errors import InvariantViolation, PredeclarationNotFrozen
from sdp.kernel.domain.experiment import Experiment
from sdp.kernel.domain.findings import Finding, Findings, OracleStatus
from sdp.kernel.domain.provenance import ProvGraph
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.ports.driving import (
    ApproveVerdictCommand,
    AssembleDossierCommand,
    DefineExperimentCommand,
    FreezePredeclarationCommand,
)
from sdp.testing.synthetic import SyntheticLinearProtocol, make_series
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T1")

FILES = {
    "b.json": (b'{"b": 1}', "application/json", "Second"),
    "a.txt": (b"alpha", "text/plain", "First"),
}


def exactly(message: str) -> str:
    return "^" + re.escape(message) + "$"


# -- RO-Crate -----------------------------------------------------------------------------------


def test_crate_metadata_is_exactly_this_ro_crate_document() -> None:
    metadata = build_crate_metadata(
        name="n",
        description="d",
        licence="CC0-1.0",
        files=FILES,
        authors=["ann", "bob"],
        date_published="2026-10-29",
        citations=["series"],
    )
    assert metadata == {
        "@context": "https://w3id.org/ro/crate/1.1/context",
        "@graph": [
            {
                "@id": "ro-crate-metadata.json",
                "@type": "CreativeWork",
                "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
                "about": {"@id": "./"},
            },
            {
                "@id": "./",
                "@type": "Dataset",
                "name": "n",
                "description": "d",
                "license": "CC0-1.0",
                "datePublished": "2026-10-29",
                "author": [{"@id": "#author-0"}, {"@id": "#author-1"}],
                "citation": ["series"],
                "hasPart": [{"@id": "a.txt"}, {"@id": "b.json"}],
            },
            {"@id": "#author-0", "@type": "Person", "name": "ann"},
            {"@id": "#author-1", "@type": "Person", "name": "bob"},
            {
                "@id": "a.txt",
                "@type": "File",
                "encodingFormat": "text/plain",
                "contentSize": "5",
                "sha256": sha256_id(b"alpha").removeprefix("sha256:"),
                "description": "First",
            },
            {
                "@id": "b.json",
                "@type": "File",
                "encodingFormat": "application/json",
                "contentSize": "8",
                "sha256": sha256_id(b'{"b": 1}').removeprefix("sha256:"),
                "description": "Second",
            },
        ],
    }


def test_the_crate_zip_is_sorted_dated_at_the_epoch_deflated_and_world_readable() -> None:
    metadata = {"@graph": [{"@id": "./", "about": 1}], "@context": "c"}
    with zipfile.ZipFile(io.BytesIO(zip_crate(metadata, FILES))) as zf:
        infos = zf.infolist()
        assert [i.filename for i in infos] == ["a.txt", "b.json", "ro-crate-metadata.json"]
        for info in infos:
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.compress_type == zipfile.ZIP_DEFLATED
            assert info.external_attr == 0o644 << 16
        assert zf.read("ro-crate-metadata.json") == (
            b'{\n  "@context": "c",\n  "@graph": [\n    {\n      "@id": "./",\n      "about": 1\n    }\n  ]\n}'
        )
        assert zf.read("a.txt") == b"alpha"


def crate(metadata: dict[str, Any], files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ro-crate-metadata.json", json.dumps(metadata))
        for path, data in files.items():
            zf.writestr(path, data)
    return buf.getvalue()


def described(path: str, data: bytes) -> dict[str, Any]:
    return {"@id": path, "contentSize": str(len(data)), "sha256": sha256_id(data).removeprefix("sha256:")}


def test_the_verifier_names_every_problem() -> None:
    assert verify_crate(crate({}, {})) == ["no root dataset"]
    assert verify_crate(crate({"@graph": [{"@id": "./"}]}, {"x": b"1"})) == ["x present but not described"]
    root = {"@id": "./", "hasPart": [{"@id": p} for p in ("gone", "lost", "bare", "bent", "long")]}
    graph = [
        root,
        described("gone", b""),
        described("lost", b""),
        {**described("bent", b"abc"), "sha256": "0" * 64},
        {**described("long", b"abc"), "contentSize": "4"},
    ]
    assert verify_crate(crate({"@graph": graph}, {"bare": b"b", "bent": b"abc", "long": b"abc"})) == [
        "gone listed but missing",
        "lost listed but missing",
        "bare listed but missing",
        "bent checksum mismatch",
        "long size mismatch",
        "bare present but not described",
    ]


# -- codec --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (np.array([object()], dtype=object), "object arrays cannot be checkpointed; use lists"),
        ({1: "x"}, "checkpoint dict keys must be strings, got int"),
        ({"__sdp__": "x"}, "__sdp__ is a reserved key"),
        ({1, 2}, "set cannot be checkpointed"),
    ],
    ids=["object-array", "int-key", "reserved-key", "set"],
)
def test_the_codec_refusals_say_what_cannot_be_checkpointed(value: Any, message: str) -> None:
    with pytest.raises(InvariantViolation, match=exactly(message)):
        codec.dumps(value)


def test_numpy_scalars_checkpoint_as_their_python_value() -> None:
    assert codec.loads(codec.dumps(np.float32(2.5))) == 2.5
    assert codec.loads(codec.dumps(np.int32(7))) == 7


def test_an_unknown_codec_tag_is_named() -> None:
    with pytest.raises(InvariantViolation, match=exactly("unknown codec tag 'mystery'")):
        codec.loads(b'{"__sdp__": "mystery"}')


# -- records ------------------------------------------------------------------------------------


def test_findings_records_keep_oracle_notes_and_narrative_and_default_them_when_absent() -> None:
    findings = Findings.of(
        Finding("slope", 2.0, 0.1),
        oracles={"o": OracleStatus("o", True, "checked twice")},
        narrative={"summary": "fine"},
    )
    record = findings_to_record(findings)
    assert record == {
        "items": [Finding("slope", 2.0, 0.1).to_document()],
        "oracles": [{"oracle_id": "o", "verified": True, "note": "checked twice"}],
        "narrative": {"summary": "fine"},
    }
    back = findings_from_record(record)
    assert (back.oracles, dict(back.narrative)) == (findings.oracles, {"summary": "fine"})
    bare = findings_from_record({"items": record["items"], "oracles": [{"oracle_id": "o", "verified": False}]})
    assert bare.oracles == {"o": OracleStatus("o", False, "")}
    assert dict(bare.narrative) == {}


def kernel_with_published_experiment() -> tuple[InMemoryKernel, str, str]:
    k = build_in_memory_kernel()
    frozen, _ = k.kernel.freeze_predeclaration.execute(
        FreezePredeclarationCommand(SyntheticLinearProtocol().predeclare(), "leigh")
    )
    series = k.store.put(make_series(), "application/json")
    experiment = k.kernel.define_experiment.execute(
        DefineExperimentCommand("synthetic-linear", frozen.hash, {"series": series}, {"label": "a"}, seed=7)
    )
    k.kernel.run_experiment.execute(experiment.experiment_id)
    k.kernel.validate_experiment.execute(experiment.experiment_id)
    figure = Figure("fit.svg", k.store.put(b"<svg/>", "image/svg+xml"), "The fit", "image/svg+xml")
    dossier = k.kernel.assemble_dossier.execute(
        AssembleDossierCommand(experiment.experiment_id, ("Synthetic",), (figure,), {"summary": "ok"})
    )
    k.kernel.publish_bundle.approve(ApproveVerdictCommand(experiment.experiment_id, "reviewer"))
    k.kernel.publish_bundle.publish(dossier.dossier_id)
    return k, experiment.experiment_id, dossier.dossier_id


def same(a: Experiment, b: Experiment) -> None:
    for f in fields(Experiment):
        if f.name != "_pending":
            assert getattr(a, f.name) == getattr(b, f.name), f.name


def test_experiment_and_dossier_records_round_trip_every_field() -> None:
    k, experiment_id, dossier_id = kernel_with_published_experiment()
    experiment = k.kernel.query_evidence.experiment(experiment_id)
    assert experiment is not None and experiment.started_at and experiment.finished_at and experiment.approvals
    record = experiment_to_record(experiment)
    assert record["started_at"] == experiment.started_at.isoformat()
    assert record["finished_at"] == experiment.finished_at.isoformat()
    same(experiment_from_record(record), experiment)
    assert all(t.duration_s is not None for t in experiment_from_record(record).tasks.values())

    dossier = k.kernel.query_evidence.dossier(dossier_id)
    assert dossier is not None and dossier.figures and dossier.sections
    signed = replace(dossier, approvals=({"reviewer": "reviewer", "ledger_entry_id": "led-000002"},))
    assert dossier_from_record(signed.to_document()) == signed


def test_a_failed_experiment_record_keeps_its_failure_and_ledger_entry() -> None:
    k = build_in_memory_kernel()
    frozen, _ = k.kernel.freeze_predeclaration.execute(
        FreezePredeclarationCommand(SyntheticLinearProtocol().predeclare(), "leigh")
    )
    series = k.store.put(make_series(), "application/json")
    defined = k.kernel.define_experiment.execute(
        DefineExperimentCommand("synthetic-linear", frozen.hash, {"series": series}, {"inject_failure": "residuals"})
    )
    failed = k.kernel.run_experiment.execute(defined.experiment_id)
    back = experiment_from_record(experiment_to_record(failed))
    assert back.failure == failed.failure and back.failure is not None
    assert back.ledger_entry_id == failed.ledger_entry_id is not None
    same(back, failed)


# -- manifest fields ----------------------------------------------------------------------------


def test_an_unfrozen_refused_experiment_gets_placeholder_intent_and_its_refusal() -> None:
    k = build_in_memory_kernel()
    series = k.store.put(make_series(), "application/json")
    unfrozen = "sha256:" + "4" * 64
    defined = k.kernel.define_experiment.execute(
        DefineExperimentCommand("synthetic-linear", unfrozen, {"series": series}, {})
    )
    with pytest.raises(PredeclarationNotFrozen):
        k.kernel.run_experiment.execute(defined.experiment_id)
    manifest = k.kernel.query_evidence.manifest(defined.experiment_id)

    assert manifest is not None
    assert manifest["intent"] == {
        "predeclaration_hash": unfrozen,
        "ledger_entry_id": "none",
        "predeclaration": {
            "schema_version": "1.0.0",
            "predeclaration_id": "unfrozen",
            "protocol_id": "synthetic-linear",
            "protocol_version": "1.0.0",
            "title": "Predeclaration not found among frozen predeclarations",
            "hypotheses": [],
            "gates": [],
            "oracle_tolerances": [],
            "targets": [],
            "parameters": {},
            "notes": None,
        },
    }
    assert manifest["execution"]["failure"] == {
        "task_id": None,
        "reason": f"predeclaration {unfrozen} is not in the ledger",
    }
    assert manifest["execution"]["started_at"] is None
    assert (manifest["findings"], manifest["oracles"], manifest["checks"], manifest["verdict"]) == ([], [], [], None)


def test_manifest_times_tasks_failure_and_untyped_outputs() -> None:
    k = build_in_memory_kernel()
    frozen, _ = k.kernel.freeze_predeclaration.execute(
        FreezePredeclarationCommand(SyntheticLinearProtocol().predeclare(), "leigh")
    )
    series = k.store.put(make_series(), "application/json")
    defined = k.kernel.define_experiment.execute(
        DefineExperimentCommand("synthetic-linear", frozen.hash, {"series": series}, {"inject_failure": "residuals"})
    )
    failed = k.kernel.run_experiment.execute(defined.experiment_id)
    source = SourceRef(SourceKind.LOCAL, "series", "unknown", "test")
    checkpoint = failed.tasks["fit"].checkpoint
    assert checkpoint is not None
    failed.outputs["partial"] = checkpoint

    manifest = build_manifest(failed, frozen, {"series": source}, ProvGraph())

    execution = manifest["execution"]
    assert execution["started_at"] == iso(failed.started_at) and execution["started_at"].endswith("Z")
    assert execution["finished_at"] == iso(failed.finished_at) and execution["finished_at"].endswith("Z")
    assert [(t["task_id"], t["status"], t["checkpoint_artifact_id"]) for t in execution["tasks"]] == [
        ("fit", "completed", checkpoint.value),
        ("residuals", "failed", None),
    ]
    assert failed.failure is not None
    assert execution["failure"] == {"task_id": "residuals", "reason": failed.failure["reason"]}
    assert manifest["outputs"] == [
        {"name": "partial", "artifact_id": checkpoint.value, "media_type": "application/octet-stream"}
    ]
    assert iso(None) is None
