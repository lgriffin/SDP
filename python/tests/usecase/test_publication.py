from __future__ import annotations

import io
import json
import zipfile

import pytest

from sdp.kernel.application.rocrate import verify_crate
from sdp.kernel.domain.errors import InvalidTransition
from sdp.kernel.domain.experiment import ExperimentState
from sdp.kernel.ports.driving import ApproveVerdictCommand, AssembleDossierCommand

from .conftest import Harness

pytestmark = pytest.mark.tier("T4")


def validated(harness: Harness) -> str:
    experiment = harness.run()
    harness.k.kernel.validate_experiment.execute(experiment.experiment_id)
    return experiment.experiment_id


def test_dossier_carries_verdict_and_limitations(harness: Harness) -> None:
    experiment_id = validated(harness)
    dossier = harness.k.kernel.assemble_dossier.execute(AssembleDossierCommand(experiment_id, ("Synthetic data only",)))
    assert dossier.experiment_id == experiment_id
    assert dossier.limitations == ("Synthetic data only",)
    assert "DossierAssembled" in harness.event_names()
    manifest = harness.k.kernel.query_evidence.manifest(experiment_id)
    assert manifest is not None and manifest["verdict"]["limitations"] == ["Synthetic data only"]


def test_approval_is_a_signed_ledger_entry_flagging_self_approval(harness: Harness) -> None:
    experiment_id = validated(harness)
    before = harness.k.kernel.query_evidence.experiment(experiment_id)
    assert before is not None and before.manifest_id is not None
    entry = harness.k.kernel.publish_bundle.approve(ApproveVerdictCommand(experiment_id, "leigh"))

    assert entry.kind == "approval" and entry.signer == "leigh"
    assert entry.payload["self_approval"] is True and entry.payload["verdict_status"] == "VERIFIED"
    stored = harness.k.kernel.query_evidence.experiment(experiment_id)
    assert stored is not None and stored.state is ExperimentState.APPROVED
    # the reviewer signs the manifest they reviewed; the manifest then records the approval
    assert entry.subject_hash == before.manifest_id.value != (stored.manifest_id and stored.manifest_id.value)
    harness.k.ledger.verify()


def test_publication_needs_approval_and_produces_a_verifiable_crate(harness: Harness) -> None:
    experiment_id = validated(harness)
    dossier = harness.k.kernel.assemble_dossier.execute(AssembleDossierCommand(experiment_id, ("Synthetic data only",)))
    with pytest.raises(InvalidTransition):
        harness.k.kernel.publish_bundle.publish(dossier.dossier_id)

    harness.k.kernel.publish_bundle.approve(ApproveVerdictCommand(experiment_id, "reviewer"))
    record = harness.k.kernel.publish_bundle.publish(dossier.dossier_id)

    assert record.doi.startswith("10.5072/")
    stored = harness.k.kernel.query_evidence.experiment(experiment_id)
    assert stored is not None and stored.state is ExperimentState.PUBLISHED
    bundle_id = harness.k.store.resolve(f"publications/{experiment_id}.zip")
    assert bundle_id is not None and stored.outputs["bundle"] == bundle_id and stored.bundle_doi == record.doi
    bundle = harness.k.store.get(bundle_id)
    assert harness.k.publisher.deposits[-1]["sha256"] == bundle_id.value
    assert verify_crate(bundle) == []
    with zipfile.ZipFile(io.BytesIO(bundle)) as crate:
        names = set(crate.namelist())
        report = json.loads(crate.read("validation-report.json"))
    assert {"ro-crate-metadata.json", "manifest.json", "predeclaration.json", "ledger.jsonl"} <= names
    assert report["verdict"]["status"] == "VERIFIED"


def test_bundles_are_byte_reproducible(harness: Harness) -> None:
    experiment_id = validated(harness)
    dossier = harness.k.kernel.assemble_dossier.execute(AssembleDossierCommand(experiment_id, ("Synthetic data only",)))
    assert harness.k.kernel.publish_bundle.build_bundle(
        dossier.dossier_id
    ) == harness.k.kernel.publish_bundle.build_bundle(dossier.dossier_id)
