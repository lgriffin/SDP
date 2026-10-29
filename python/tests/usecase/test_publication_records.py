"""The exact records of freezing, dossier assembly, approval and publication: ledger entries, events,
provenance, every file of the RO-Crate bundle and its metadata, the DOI deposit, and each refusal."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import astropy.units as u
import pytest

from sdp.kernel.application.rocrate import read_crate
from sdp.kernel.domain.canonical import canonical_json, sha256_id
from sdp.kernel.domain.dossier import Dossier, Figure
from sdp.kernel.domain.errors import InvalidTransition
from sdp.kernel.domain.predeclaration import FrozenPredeclaration
from sdp.kernel.domain.prediction import Prediction
from sdp.kernel.ports.driving import (
    ApproveVerdictCommand,
    AssembleDossierCommand,
    FreezePredeclarationCommand,
    RegisterHypothesisCommand,
)
from sdp.kernel.ports.ledger import LedgerEntry

from .conftest import Harness

pytestmark = pytest.mark.tier("T4")


def at(second: int) -> datetime:
    return datetime(2026, 10, 28, 12, 0, second, tzinfo=UTC)


def exactly(message: str) -> str:
    return "^" + re.escape(message) + "$"


def events(harness: Harness, name: str) -> list[tuple[str, datetime, dict[str, object]]]:
    return [
        (s.event.aggregate_id, s.event.occurred_at, dict(s.event.payload))
        for s in harness.k.db.state.events
        if s.event.name == name
    ]


def prov(harness: Harness) -> dict[str, object]:
    return harness.k.db.state.graph.to_prov_json()


@dataclass
class Published:
    experiment_id: str
    frozen: FrozenPredeclaration
    dossier: Dossier
    approval: LedgerEntry
    figure: bytes


def assembled(harness: Harness) -> tuple[str, FrozenPredeclaration, Dossier, bytes]:
    frozen = harness.freeze()
    experiment = harness.k.kernel.run_experiment.execute(harness.define(frozen.hash).experiment_id)
    harness.k.kernel.validate_experiment.execute(experiment.experiment_id)
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
    figure = Figure("fit.svg", harness.k.store.put(svg, "image/svg+xml"), "The fitted line")
    dossier = harness.k.kernel.assemble_dossier.execute(
        AssembleDossierCommand(experiment.experiment_id, ("Synthetic data only",), (figure,), {"summary": "ok"})
    )
    return experiment.experiment_id, frozen, dossier, svg


def approved(harness: Harness) -> Published:
    experiment_id, frozen, dossier, svg = assembled(harness)
    approval = harness.k.kernel.publish_bundle.approve(ApproveVerdictCommand(experiment_id, "reviewer"))
    return Published(experiment_id, frozen, dossier, approval, svg)


# -- freezing and hypotheses --------------------------------------------------------------------


def test_freezing_signs_a_ledger_entry_records_the_signer_and_emits_one_event(harness: Harness) -> None:
    predeclaration = harness.protocol.predeclare()
    frozen, entry = harness.k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(predeclaration, "leigh"))
    again, same = harness.k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(predeclaration, "leigh"))

    assert (again.hash, same) == (frozen.hash, entry)
    assert (entry.kind, entry.subject_id, entry.subject_hash, entry.signer) == (
        "predeclaration",
        "synthetic-linear-v1",
        frozen.hash,
        "leigh",
    )
    assert entry.payload == {"protocol_id": "synthetic-linear", "protocol_version": "1.0.0"}
    assert events(harness, "PredeclarationFrozen") == [
        ("synthetic-linear-v1", at(1), {"hash": frozen.hash, "ledger_entry_id": entry.entry_id})
    ]
    doc = prov(harness)
    assert doc["entity"] == {
        "sdp:" + frozen.hash: {"sdp:type": "Predeclaration", "sdp:predeclaration_id": "synthetic-linear-v1"}
    }
    assert doc["agent"] == {"sdp:leigh": {"prov:type": "prov:Person"}}
    assert list(doc["wasAttributedTo"].values()) == [  # type: ignore[attr-defined]
        {"prov:entity": "sdp:" + frozen.hash, "prov:agent": "sdp:leigh"}
    ]
    with harness.k.db.unit_of_work() as uow:
        stored = uow.predeclarations.get_frozen(frozen.hash)
        uow.commit()
    assert stored is not None and stored.hash == frozen.hash
    assert harness.k.db.state.frozen[frozen.hash][1] == entry.entry_id


def test_registering_a_hypothesis_twice_keeps_both_versions_and_their_events(harness: Harness) -> None:
    register = harness.k.kernel.register_hypothesis
    prediction = Prediction("linear", "1.0.0", {"slope": 2.0}, 2.0 * u.dimensionless_unscaled)
    first = register.execute(RegisterHypothesisCommand("H1", "The slope is two.", (prediction,), ("|s - 2| < 0.1",)))
    second = register.execute(
        RegisterHypothesisCommand("H1", "The slope is near two.", (prediction,), ("|s - 2| < 0.2",))
    )

    assert (first.version, first.predictions, first.evaluation_criteria) == (1, (prediction,), ("|s - 2| < 0.1",))
    assert (second.version, second.statement, second.evaluation_criteria) == (
        2,
        "The slope is near two.",
        ("|s - 2| < 0.2",),
    )
    assert second.predictions == (prediction,)
    versions = harness.k.kernel.query_evidence.hypothesis_versions("H1")
    assert [(h.version, h.statement) for h in versions] == [(1, "The slope is two."), (2, "The slope is near two.")]
    assert events(harness, "HypothesisRegistered") == [("H1", at(0), {"version": 1}), ("H1", at(1), {"version": 2})]


def test_the_event_query_pages_after_a_sequence_with_a_limit(harness: Harness) -> None:
    harness.run()
    query = harness.k.kernel.query_evidence
    assert [s.sequence for s in query.events(after=1, limit=2)] == [2, 3]
    assert len(query.events()) == len(harness.k.db.state.events) > 3


# -- dossier and approval -----------------------------------------------------------------------


def test_a_dossier_keeps_its_figures_sections_and_event(harness: Harness) -> None:
    experiment_id, _frozen, dossier, _svg = assembled(harness)

    assert dossier.dossier_id == "dos-000001"
    assert harness.k.kernel.query_evidence.dossier(dossier.dossier_id) == dossier
    assert dossier.sections == {"summary": "ok"} and [f.name for f in dossier.figures] == ["fit.svg"]
    ((aggregate, occurred, payload),) = events(harness, "DossierAssembled")
    assert (aggregate, payload) == ("dos-000001", {"experiment_id": experiment_id})
    assert occurred > at(0)


def test_an_approval_by_another_reviewer_is_signed_recorded_and_attributed(harness: Harness) -> None:
    published = approved(harness)
    experiment_id, entry = published.experiment_id, published.approval
    stored = harness.k.kernel.query_evidence.experiment(experiment_id)
    assert stored is not None

    assert (entry.kind, entry.subject_id, entry.signer) == ("approval", experiment_id, "reviewer")
    assert entry.payload == {"experiment_id": experiment_id, "verdict_status": "VERIFIED", "self_approval": False}
    ((_, occurred, payload),) = events(harness, "ApprovalRecorded")
    assert (
        stored.approvals
        == [payload]
        == [{"ledger_entry_id": entry.entry_id, "reviewer": "reviewer", "recorded_at": occurred.isoformat()}]
    )
    doc = prov(harness)
    assert doc["agent"]["sdp:reviewer"] == {"prov:type": "prov:Person"}  # type: ignore[index]
    report = f"sdp:experiment/{experiment_id}/report"
    assert {"prov:entity": report, "prov:agent": "sdp:reviewer"} in doc["wasAttributedTo"].values()  # type: ignore[attr-defined]
    manifest = harness.k.kernel.query_evidence.manifest(experiment_id)
    assert manifest is not None and manifest["execution"]["state"] == "approved"


# -- the bundle ---------------------------------------------------------------------------------

DESCRIPTIONS = {
    "dossier.json": ("application/json", "Dossier"),
    "figures/fit.svg": ("image/svg+xml", "The fitted line"),
    "ledger.jsonl": ("application/x-ndjson", "Signed ledger entries for this experiment"),
    "manifest.json": ("application/json", "Evidence manifest (§7)"),
    "provenance.prov.json": ("application/json", "W3C PROV-JSON subgraph"),
    "validation-report.json": ("application/json", "Validation report with every gate outcome"),
}


def test_the_bundle_holds_exactly_the_evidence_files_and_describes_each(harness: Harness) -> None:
    published = approved(harness)
    experiment_id, frozen = published.experiment_id, published.frozen
    unrelated = harness.k.ledger.append(
        kind="approval", subject_id="exp-other", subject_hash="sha256:" + "3" * 64, signer="x", payload={}
    )
    finished = harness.k.kernel.query_evidence.experiment(experiment_id)
    assert finished is not None and finished.finished_at is not None and finished.report is not None
    harness.k.clock.start = datetime(2027, 1, 1, tzinfo=UTC)  # the bundle is dated by the run, not the clock

    metadata, files = read_crate(harness.k.kernel.publish_bundle.build_bundle(published.dossier.dossier_id))

    manifest = harness.k.kernel.query_evidence.manifest(experiment_id)
    assert manifest is not None
    ledger = [e for e in harness.k.ledger.entries() if e is not unrelated]
    assert [e.kind for e in ledger] == ["predeclaration", "approval"]
    assert files == {
        "manifest.json": canonical_json(manifest).encode(),
        "predeclaration.json": canonical_json(frozen.predeclaration.to_document()).encode(),
        "validation-report.json": canonical_json(finished.report.to_document()).encode(),
        "provenance.prov.json": json.dumps(manifest["provenance"], indent=2, sort_keys=True).encode(),
        "dossier.json": canonical_json(published.dossier.to_document()).encode(),
        "ledger.jsonl": "".join(canonical_json(e.to_document()) + "\n" for e in ledger).encode(),
        "figures/fit.svg": published.figure,
    }
    assert files["provenance.prov.json"].startswith(b'{\n  "activity": {\n    "sdp:experiment/')

    graph = {e["@id"]: e for e in metadata["@graph"]}
    paths = sorted(files)
    assert graph["./"] == {
        "@id": "./",
        "@type": "Dataset",
        "name": f"synthetic-linear {experiment_id}",
        "description": f"Evidence bundle for {experiment_id}: verdict VERIFIED",
        "license": "CC-BY-4.0",
        "datePublished": "2026-10-28",
        "author": [{"@id": "#author-0"}],
        "citation": ["series"],
        "hasPart": [{"@id": p} for p in paths],
    }
    assert graph["#author-0"] == {"@id": "#author-0", "@type": "Person", "name": "reviewer"}
    described = {**DESCRIPTIONS, "predeclaration.json": ("application/json", f"Frozen predeclaration {frozen.hash}")}
    for path in paths:
        media_type, description = described[path]
        assert graph[path] == {
            "@id": path,
            "@type": "File",
            "encodingFormat": media_type,
            "contentSize": str(len(files[path])),
            "sha256": sha256_id(files[path]).removeprefix("sha256:"),
            "description": description,
        }


def test_publication_deposits_the_bundle_and_records_the_doi(harness: Harness) -> None:
    published = approved(harness)
    experiment_id = published.experiment_id
    bundle = harness.k.kernel.publish_bundle.build_bundle(published.dossier.dossier_id)

    record = harness.k.kernel.publish_bundle.publish(published.dossier.dossier_id)

    assert harness.k.publisher.deposits == [
        {
            "filename": f"{experiment_id}.zip",
            "sha256": sha256_id(bundle),
            "metadata": {
                "title": f"SDP evidence bundle {experiment_id}",
                "license": "CC-BY-4.0",
                "upload_type": "dataset",
            },
        }
    ]
    stored = harness.k.kernel.query_evidence.experiment(experiment_id)
    assert stored is not None and stored.bundle_doi == record.doi
    bundle_id = stored.outputs["bundle"]
    assert bundle_id.value == sha256_id(bundle) and harness.k.store.media_type(bundle_id) == "application/zip"
    ((_, occurred, payload),) = events(harness, "BundlePublished")
    assert payload == {"doi": record.doi, "bundle": bundle_id.value} and occurred > at(0)
    doc = prov(harness)
    entity = "sdp:" + bundle_id.value
    assert doc["entity"][entity] == {"sdp:type": "PublicationBundle", "sdp:doi": record.doi}  # type: ignore[index]
    assert list(doc["wasDerivedFrom"].values()) == [  # type: ignore[attr-defined]
        {"prov:generatedEntity": entity, "prov:usedEntity": f"sdp:experiment/{experiment_id}/report"}
    ]
    manifest = harness.k.kernel.query_evidence.manifest(experiment_id)
    assert manifest is not None and manifest["execution"]["state"] == "published"


# -- refusals -----------------------------------------------------------------------------------


def test_approval_refuses_unknown_unvalidated_already_approved_and_unwritten(harness: Harness) -> None:
    approve = harness.k.kernel.publish_bundle.approve
    with pytest.raises(KeyError) as unknown:
        approve(ApproveVerdictCommand("exp-unknown", "reviewer"))
    assert unknown.value.args == ("exp-unknown",)

    completed = harness.run().experiment_id
    with pytest.raises(
        InvalidTransition, match=exactly(f"{completed} is completed; only validated verdicts are approved")
    ):
        approve(ApproveVerdictCommand(completed, "reviewer"))

    published = approved(harness)
    with pytest.raises(
        InvalidTransition, match=exactly(f"{published.experiment_id} is approved; only validated verdicts are approved")
    ):
        approve(ApproveVerdictCommand(published.experiment_id, "reviewer"))

    experiment_id, _frozen, _dossier, _svg = assembled(harness)
    harness.k.db.state.experiments[experiment_id]["manifest_id"] = None
    with pytest.raises(InvalidTransition, match=exactly("approval signs a manifest; none has been written")):
        approve(ApproveVerdictCommand(experiment_id, "reviewer"))


@pytest.mark.parametrize("missing", ["experiment", "manifest", "frozen", "report"])
def test_a_bundle_needs_every_piece_of_evidence(harness: Harness, missing: str) -> None:
    published = approved(harness)
    state = harness.k.db.state
    if missing == "experiment":
        del state.experiments[published.experiment_id]
    elif missing == "manifest":
        del state.manifests[published.experiment_id]
    elif missing == "frozen":
        del state.frozen[published.frozen.hash]
    else:
        state.experiments[published.experiment_id]["report"] = None
    message = "a bundle needs the experiment, its manifest, report and frozen predeclaration"
    with pytest.raises(InvalidTransition, match=exactly(message)):
        harness.k.kernel.publish_bundle.build_bundle(published.dossier.dossier_id)


def test_publication_refuses_an_unknown_dossier_and_an_unapproved_experiment(harness: Harness) -> None:
    publish = harness.k.kernel.publish_bundle
    with pytest.raises(KeyError) as unknown:
        publish.build_bundle("dos-unknown")
    assert unknown.value.args == ("dos-unknown",)
    experiment_id, _frozen, dossier, _svg = assembled(harness)
    with pytest.raises(
        InvalidTransition, match=exactly(f"{experiment_id} is validated; publication needs an approval")
    ):
        publish.publish(dossier.dossier_id)
    assert harness.k.publisher.deposits == []


def test_a_dossier_names_an_unknown_experiment(harness: Harness) -> None:
    with pytest.raises(KeyError) as unknown:
        harness.k.kernel.assemble_dossier.execute(AssembleDossierCommand("exp-unknown", ()))
    assert unknown.value.args == ("exp-unknown",)
