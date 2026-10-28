"""PublishBundle: record reviewer approvals in the ledger, build the RO-Crate, mint the DOI."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from sdp.kernel.application.experiments import ManifestWriter, _persist
from sdp.kernel.application.rocrate import build_crate_metadata, zip_crate
from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.domain.errors import InvalidTransition
from sdp.kernel.domain.experiment import ExperimentState
from sdp.kernel.domain.provenance import ProvGraph
from sdp.kernel.ports.determinism import Clock
from sdp.kernel.ports.driving import ApproveVerdictCommand
from sdp.kernel.ports.ledger import Ledger, LedgerEntry
from sdp.kernel.ports.publication import BundlePublisher, DoiRecord
from sdp.kernel.ports.repositories import UnitOfWork
from sdp.kernel.ports.storage import ArtifactStore

BUNDLE_MEDIA_TYPE = "application/zip"


@dataclass
class PublishBundleUseCase:
    uow: Callable[[], UnitOfWork]
    ledger: Ledger
    store: ArtifactStore
    publisher: BundlePublisher
    clock: Clock
    manifests: ManifestWriter
    licence: str = "CC-BY-4.0"

    def approve(self, command: ApproveVerdictCommand) -> LedgerEntry:
        """A reviewer's signed approval of a verdict is a ledger entry (ADR-004)."""
        with self.uow() as uow:
            experiment = uow.experiments.get(command.experiment_id)
            if experiment is None:
                raise KeyError(command.experiment_id)
            if experiment.state is not ExperimentState.VALIDATED or experiment.report is None:
                raise InvalidTransition(
                    f"{command.experiment_id} is {experiment.state}; only validated verdicts are approved"
                )
            if experiment.manifest_id is None:
                raise InvalidTransition("approval signs a manifest; none has been written")
            predeclaration_entry = self.ledger.find(experiment.definition.predeclaration_hash, "predeclaration")
            entry = self.ledger.append(
                kind="approval",
                subject_id=command.experiment_id,
                subject_hash=experiment.manifest_id.value,
                signer=command.reviewer,
                payload={
                    "experiment_id": command.experiment_id,
                    "verdict_status": experiment.report.verdict.status.value,
                    "self_approval": bool(predeclaration_entry and predeclaration_entry.signer == command.reviewer),
                },
            )
            experiment.approve(ledger_entry_id=entry.entry_id, reviewer=command.reviewer, at=self.clock.now())
            graph = ProvGraph()
            graph.agent(command.reviewer, **{"prov:type": "prov:Person"})
            graph.relate("wasAttributedTo", f"experiment/{command.experiment_id}/report", command.reviewer)
            uow.evidence.record(graph)
            _persist(uow, experiment, self.manifests)
            uow.commit()
        return entry

    def build_bundle(self, dossier_id: str) -> bytes:
        with self.uow() as uow:
            dossier = uow.dossiers.get(dossier_id)
            if dossier is None:
                raise KeyError(dossier_id)
            experiment = uow.experiments.get(dossier.experiment_id)
            manifest = uow.experiments.manifest(dossier.experiment_id)
            frozen = uow.predeclarations.get_frozen(experiment.definition.predeclaration_hash) if experiment else None
            uow.commit()
        if experiment is None or manifest is None or frozen is None or experiment.report is None:
            raise InvalidTransition("a bundle needs the experiment, its manifest, report and frozen predeclaration")
        ledger_lines = "".join(
            canonical_json(e.to_document()) + "\n"
            for e in self.ledger.entries()
            if e.subject_hash == frozen.hash or e.subject_id == experiment.experiment_id
        ).encode()
        files: dict[str, tuple[bytes, str, str]] = {
            "manifest.json": (canonical_json(manifest).encode(), "application/json", "Evidence manifest (§7)"),
            "predeclaration.json": (
                canonical_json(frozen.predeclaration.to_document()).encode(),
                "application/json",
                f"Frozen predeclaration {frozen.hash}",
            ),
            "validation-report.json": (
                canonical_json(experiment.report.to_document()).encode(),
                "application/json",
                "Validation report with every gate outcome",
            ),
            "provenance.prov.json": (
                json.dumps(manifest["provenance"], indent=2, sort_keys=True).encode(),
                "application/json",
                "W3C PROV-JSON subgraph",
            ),
            "dossier.json": (canonical_json(dossier.to_document()).encode(), "application/json", "Dossier"),
            "ledger.jsonl": (ledger_lines, "application/x-ndjson", "Signed ledger entries for this experiment"),
        }
        for figure in dossier.figures:
            files[f"figures/{figure.name}"] = (self.store.get(figure.artifact_id), figure.media_type, figure.caption)
        metadata = build_crate_metadata(
            name=f"{experiment.definition.protocol.name} {experiment.experiment_id}",
            description=f"Evidence bundle for {experiment.experiment_id}: verdict {experiment.report.verdict.status.value}",
            licence=self.licence,
            files=files,
            authors=sorted({a["reviewer"] for a in experiment.approvals}),
            date_published=(experiment.finished_at or self.clock.now()).date().isoformat(),
            citations=sorted({b.role for b in experiment.definition.inputs}),
        )
        return zip_crate(metadata, files)

    def publish(self, dossier_id: str) -> DoiRecord:
        """Only an Approved experiment reaches Published."""
        bundle = self.build_bundle(dossier_id)
        with self.uow() as uow:
            dossier = uow.dossiers.get(dossier_id)
            assert dossier is not None
            experiment = uow.experiments.get(dossier.experiment_id)
            assert experiment is not None
            if experiment.state is not ExperimentState.APPROVED:
                raise InvalidTransition(
                    f"{experiment.experiment_id} is {experiment.state}; publication needs an approval"
                )
            uow.commit()
        bundle_id = self.store.put(bundle, BUNDLE_MEDIA_TYPE, logical_path=f"publications/{dossier.experiment_id}.zip")
        record = self.publisher.publish(
            bundle,
            f"{dossier.experiment_id}.zip",
            {
                "title": f"SDP evidence bundle {dossier.experiment_id}",
                "license": self.licence,
                "upload_type": "dataset",
            },
        )
        with self.uow() as uow:
            experiment = uow.experiments.get(dossier.experiment_id)
            assert experiment is not None
            experiment.publish(doi=record.doi, bundle=bundle_id, at=self.clock.now())
            graph = ProvGraph()
            graph.entity(bundle_id.value, type="PublicationBundle", doi=record.doi)
            graph.relate("wasDerivedFrom", bundle_id.value, f"experiment/{experiment.experiment_id}/report")
            uow.evidence.record(graph)
            _persist(uow, experiment, self.manifests)
            uow.commit()
        return record
