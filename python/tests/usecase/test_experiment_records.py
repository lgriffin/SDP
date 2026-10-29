"""The exact records DefineExperiment, RunExperiment and ValidateExperiment leave behind: the provenance
graph, the event log with its timestamps, the tracker calls, the log records, the source of every input
and the wording of every refusal. Weaker assertions elsewhere let mutants of these records survive."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import pytest

from sdp.kernel.application import codec
from sdp.kernel.application.experiments import ManifestWriter
from sdp.kernel.domain.dataset import AcquisitionRecord, DatasetArtifact, ProductUse
from sdp.kernel.domain.errors import InvariantViolation, PlanInvalid, PredeclarationNotFrozen, VerdictInconsistent
from sdp.kernel.domain.experiment import Experiment, ExperimentState
from sdp.kernel.domain.findings import Finding, Findings
from sdp.kernel.domain.ids import ArtifactId, ProtocolId
from sdp.kernel.domain.integration import ProductType
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.ports.driving import (
    ApproveVerdictCommand,
    AssembleDossierCommand,
    DefineExperimentCommand,
    FreezePredeclarationCommand,
)
from sdp.kernel.science.io import ComputeResults, Params, ResolvedInputs
from sdp.kernel.science.plan import ComputePlan
from sdp.testing.fakes import InMemoryArtifactStore
from sdp.testing.fakes.integration import StaticCompatibilityRecords
from sdp.testing.integration import compatibility
from sdp.testing.synthetic import SyntheticLinearProtocol, make_series

from .conftest import Harness, harness_for
from .test_validate_experiment import OptimisticProtocol

pytestmark = pytest.mark.tier("T4")

MAST = SourceRef(SourceKind.ARCHIVE, "mast", "public", "MAST", release_label="mast:jwst")


def at(second: int) -> datetime:
    """The FixedClock's reading on its ``second``-th call."""
    return datetime(2026, 10, 28, 12, 0, second, tzinfo=UTC)


def exactly(message: str) -> str:
    return "^" + re.escape(message) + "$"


@dataclass
class RecordingTracker:
    params: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    metrics: list[tuple[str, dict[str, float]]] = field(default_factory=list)
    tags: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def log_params(self, experiment_id: str, params: Mapping[str, Any]) -> None:
        self.params.append((experiment_id, dict(params)))

    def log_metrics(self, experiment_id: str, metrics: Mapping[str, float]) -> None:
        self.metrics.append((experiment_id, dict(metrics)))

    def set_tags(self, experiment_id: str, tags: Mapping[str, str]) -> None:
        self.tags.append((experiment_id, dict(tags)))


def tracked(harness: Harness) -> RecordingTracker:
    tracker = RecordingTracker()
    harness.k.kernel.run_experiment.tracker = tracker
    harness.k.kernel.validate_experiment.tracker = tracker
    return tracker


def validated(harness: Harness) -> Experiment:
    experiment = harness.run()
    harness.k.kernel.validate_experiment.execute(experiment.experiment_id)
    return experiment


def unregistered(artifact: ArtifactId) -> SourceRef:
    return SourceRef(SourceKind.LOCAL, f"artifact {artifact}", "unknown", "unregistered artifact")


# -- provenance and events ----------------------------------------------------------------------


def test_a_validated_run_records_exactly_this_provenance(harness: Harness) -> None:
    experiment = validated(harness)
    exp = f"sdp:experiment/{experiment.experiment_id}"
    pre = "sdp:" + experiment.definition.predeclaration_hash
    series = "sdp:" + experiment.definition.input("series").artifact_id.value
    fit, res, findings = ("sdp:" + experiment.outputs[k].value for k in ("result/fit", "result/residuals", "findings"))
    protocol = "sdp:protocol/synthetic-linear@1.0.0"

    doc = harness.k.db.state.graph.to_prov_json()

    assert doc["entity"] == {
        f"{exp}/report": {"sdp:type": "ValidationReport", "sdp:verdict": "VERIFIED"},
        findings: {"sdp:type": "Findings"},
        fit: {"sdp:type": "Checkpoint", "sdp:task_id": "fit"},
        res: {"sdp:type": "Checkpoint", "sdp:task_id": "residuals"},
        pre: {"sdp:type": "Predeclaration", "sdp:predeclaration_id": "synthetic-linear-v1"},
        series: {"sdp:role": "series"},
    }
    assert doc["activity"] == {exp: {"sdp:type": "Experiment", "sdp:protocol": "synthetic-linear@1.0.0"}}
    assert doc["agent"] == {
        "sdp:kernel": {"prov:type": "prov:SoftwareAgent"},
        "sdp:leigh": {"prov:type": "prov:Person"},
        protocol: {"prov:type": "prov:SoftwareAgent"},
    }
    assert list(doc["used"].values()) == [
        {"prov:activity": exp, "prov:entity": pre},
        {"prov:activity": exp, "prov:entity": series},
    ]
    assert list(doc["wasAssociatedWith"].values()) == [
        {"prov:activity": exp, "prov:agent": "sdp:kernel"},
        {"prov:activity": exp, "prov:agent": protocol},
    ]
    assert list(doc["wasGeneratedBy"].values()) == [
        {"prov:entity": fit, "prov:activity": exp},
        {"prov:entity": res, "prov:activity": exp},
        {"prov:entity": findings, "prov:activity": exp},
        {"prov:entity": f"{exp}/report", "prov:activity": exp},
    ]


def test_every_experiment_event_carries_the_clock_reading_and_payload(harness: Harness) -> None:
    experiment = validated(harness)
    pre = experiment.definition.predeclaration_hash
    tasks = experiment.tasks

    events = [
        (s.event.name, s.event.aggregate_id, s.event.occurred_at, s.event.payload) for s in harness.k.db.state.events
    ]

    assert experiment.experiment_id == "exp-000001"
    assert events[1:] == [
        (
            "ExperimentDefined",
            "exp-000001",
            at(2),
            {"protocol_id": "synthetic-linear", "protocol_version": "1.0.0", "predeclaration_hash": pre, "seed": 42},
        ),
        ("ExperimentStarted", "exp-000001", at(3), {"resumed": False, "ledger_entry_id": "led-000001"}),
        *(
            (
                "CheckpointWritten",
                "exp-000001",
                at(4 + i),
                {
                    "task_id": t,
                    "artifact_id": experiment.outputs[f"result/{t}"].value,
                    "duration_s": tasks[t].duration_s,
                },
            )
            for i, t in enumerate(("fit", "residuals"))
        ),
        ("ExperimentCompleted", "exp-000001", at(6), {"findings": ["residual_rms", "slope", "slope_abs_error"]}),
        (
            "VerdictIssued",
            "exp-000001",
            at(7),
            {"status": "VERIFIED", "gates": {"SYN-G-01": "pass", "SYN-G-02": "pass"}},
        ),
    ]
    assert all(isinstance(tasks[t].duration_s, float) for t in ("fit", "residuals"))
    stored = harness.k.kernel.query_evidence.experiment(experiment.experiment_id)
    assert stored is not None and (stored.started_at, stored.finished_at) == (at(3), at(6))


def test_checkpoints_findings_and_manifest_are_stored_with_their_media_types(harness: Harness) -> None:
    experiment = validated(harness)
    store = harness.k.store
    for name in ("result/fit", "result/residuals", "findings"):
        assert store.media_type(experiment.outputs[name]) == codec.MEDIA_TYPE
    assert experiment.findings is not None
    assert codec.loads(store.get(experiment.outputs["findings"])) == {
        f.name: f.to_document() for f in experiment.findings.items.values()
    }
    stored = harness.k.kernel.query_evidence.experiment(experiment.experiment_id)
    assert stored is not None and stored.manifest_id is not None
    assert store.media_type(stored.manifest_id) == "application/json"


# -- tracker and logs ---------------------------------------------------------------------------


class MixedFindingsProtocol(SyntheticLinearProtocol):
    """Adds an integer, a boolean and a string finding: only numbers that are not booleans are metrics."""

    def interpret(self, results: ComputeResults) -> Findings:
        base = super().interpret(results)
        extra = (Finding("points", 200), Finding("converged", True), Finding("band", "g"))
        return Findings.of(*base.items.values(), *extra, oracles=base.oracles)


def test_the_tracker_gets_seed_parameters_numeric_metrics_and_the_verdict_tag() -> None:
    harness = harness_for(MixedFindingsProtocol())
    tracker = tracked(harness)
    experiment = harness.run(label="first")
    harness.k.kernel.validate_experiment.execute(experiment.experiment_id)

    assert experiment.findings is not None
    values = {name: f.value for name, f in experiment.findings.items.items()}
    assert tracker.params == [("exp-000001", {"seed": 42, "label": "first"})]
    assert tracker.metrics == [
        (
            "exp-000001",
            {
                "slope": values["slope"],
                "slope_abs_error": values["slope_abs_error"],
                "residual_rms": values["residual_rms"],
                "points": 200.0,
            },
        )
    ]
    assert all(type(v) is float for v in tracker.metrics[0][1].values())
    assert tracker.tags == [("exp-000001", {"verdict": "VERIFIED"})]


def test_the_completion_log_names_the_experiment_duration_tasks_and_memory(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="sdp.kernel.runner")
    harness.run()
    (record,) = [r for r in caplog.records if r.getMessage() == "experiment completed"]
    fields: dict[str, Any] = record.__dict__
    assert fields["experiment_id"] == "exp-000001" and fields["executed_tasks"] == 2
    assert 0 <= fields["duration_s"] < 60
    assert fields["resource_maxrss_kb"] > 0


def test_the_failure_log_names_the_experiment_and_task(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="sdp.kernel.runner")
    harness.run(inject_failure="residuals")
    (record,) = [r for r in caplog.records if r.getMessage() == "experiment failed"]
    fields: dict[str, Any] = record.__dict__
    assert (fields["experiment_id"], fields["task_id"]) == ("exp-000001", "residuals")


# -- failures before and after the compute plan -------------------------------------------------


class UnplannableProtocol(SyntheticLinearProtocol):
    def plan(self, inputs: ResolvedInputs, params: Params, seed: int) -> ComputePlan:
        raise PlanInvalid("no tasks for this series")


class UninterpretableProtocol(SyntheticLinearProtocol):
    def interpret(self, results: ComputeResults) -> Findings:
        raise ValueError("the fit did not converge")


@pytest.mark.parametrize(
    ("protocol", "reason", "diagnostics"),
    [
        (UnplannableProtocol(), "PlanInvalid: no tasks for this series", {}),
        (UninterpretableProtocol(), "ValueError: the fit did not converge", {"stage": "interpret"}),
    ],
    ids=["plan", "interpret"],
)
def test_a_failure_outside_a_task_is_recorded_with_its_stage(
    protocol: SyntheticLinearProtocol, reason: str, diagnostics: dict[str, Any]
) -> None:
    harness = harness_for(protocol)
    experiment = harness.run()

    assert experiment.experiment_id == "exp-000001" and experiment.state is ExperimentState.FAILED
    assert experiment.failure == {"task_id": None, "reason": reason, "diagnostics": diagnostics}
    stored = harness.k.kernel.query_evidence.experiment(experiment.experiment_id)
    assert stored is not None and stored.failure == experiment.failure and stored.finished_at is not None


@dataclass
class Seen:
    inputs: ResolvedInputs | None = None
    seed: int | None = None


class ObservingProtocol(SyntheticLinearProtocol):
    """Records what plan() was handed, and asks for no named outputs so every task's value is kept."""

    def __init__(self) -> None:
        super().__init__()
        self.seen = Seen()

    def plan(self, inputs: ResolvedInputs, params: Params, seed: int) -> ComputePlan:
        self.seen = Seen(inputs, seed)
        return ComputePlan(super().plan(inputs, params, seed).tasks)


def test_plan_receives_the_resolved_input_and_seed_and_an_unnamed_plan_keeps_every_task() -> None:
    protocol = ObservingProtocol()
    harness = harness_for(protocol)
    experiment = harness.run()

    assert protocol.seen.seed == 42 and protocol.seen.inputs is not None
    (resolved,) = protocol.seen.inputs.items.values()
    artifact = experiment.definition.input("series").artifact_id
    assert (resolved.role, resolved.artifact_id, resolved.media_type) == ("series", artifact, "application/json")
    assert resolved.source == unregistered(artifact)
    assert sorted(experiment.outputs) == ["findings", "result/fit", "result/residuals"]


def test_a_registered_input_keeps_its_archive_source_in_plan_and_manifest() -> None:
    protocol = ObservingProtocol()
    harness = harness_for(protocol)
    k = harness.k
    frozen = harness.freeze()
    data = make_series()
    series = k.store.put(data, "application/json")
    acquired = AcquisitionRecord("SELECT 1", "sha256:" + "0" * 64, "mast:jwst", at(0), series, len(data))
    raw = DatasetArtifact.acquired_raw(
        actual=series, media_type="application/json", byte_count=len(data), source_ref=MAST, acquisition=acquired
    )
    with k.db.unit_of_work() as uow:
        uow.datasets.add(raw)
        uow.commit()
    experiment = k.kernel.define_experiment.execute(
        DefineExperimentCommand("synthetic-linear", frozen.hash, {"series": series}, {}, seed=42)
    )
    k.kernel.run_experiment.execute(experiment.experiment_id)

    assert protocol.seen.inputs is not None and protocol.seen.inputs.items["series"].source == MAST
    manifest = k.kernel.query_evidence.manifest(experiment.experiment_id)
    assert manifest is not None and manifest["inputs"][0]["source_ref"] == MAST.to_document()


def test_an_unregistered_input_is_cited_as_a_local_artifact_in_the_manifest(harness: Harness) -> None:
    experiment = harness.run()
    artifact = experiment.definition.input("series").artifact_id
    manifest = harness.k.kernel.query_evidence.manifest(experiment.experiment_id)
    assert manifest is not None and manifest["inputs"][0]["source_ref"] == unregistered(artifact).to_document()
    assert [(o["name"], o["media_type"]) for o in manifest["outputs"]] == [
        ("findings", codec.MEDIA_TYPE),
        ("result/fit", codec.MEDIA_TYPE),
        ("result/residuals", codec.MEDIA_TYPE),
    ]


# -- refusals -----------------------------------------------------------------------------------


def command(harness: Harness, inputs: Mapping[str, ArtifactId], **changes: Any) -> DefineExperimentCommand:
    return replace(DefineExperimentCommand("synthetic-linear", harness.freeze().hash, inputs, {}, seed=42), **changes)


def test_define_refuses_missing_unknown_absent_and_mistyped_inputs(harness: Harness) -> None:
    define = harness.k.kernel.define_experiment
    series = harness.k.store.put(make_series(), "application/json")
    absent = ArtifactId("sha256:" + "1" * 64)
    csv = harness.k.store.put(b"x,y\n1,2\n", "text/csv")
    refusals = [
        ({}, "experiment is missing inputs ['series']"),
        ({"series": series, "extra": series}, "protocol synthetic-linear@1.0.0 does not take inputs ['extra']"),
        ({"series": absent}, f"input series names an artifact the store does not hold: {absent}"),
        ({"series": csv}, "input series is text/csv, protocol expects application/json"),
    ]
    for inputs, message in refusals:
        with pytest.raises(InvariantViolation, match=exactly(message)):
            define.execute(command(harness, inputs))


def test_define_refuses_another_protocol_version_and_a_predeclaration_frozen_for_one(harness: Harness) -> None:
    define = harness.k.kernel.define_experiment
    series = harness.k.store.put(make_series(), "application/json")
    with pytest.raises(InvariantViolation, match=exactly("synthetic-linear 9.9.9 requested, 1.0.0 registered")):
        define.execute(command(harness, {"series": series}, protocol_version="9.9.9"))

    older = replace(harness.protocol.predeclare(), protocol_version="0.9.0")
    frozen, _ = harness.k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(older, "leigh"))
    with pytest.raises(
        InvariantViolation, match=exactly("the predeclaration was frozen for a different protocol version")
    ):
        define.execute(command(harness, {"series": series}, predeclaration_hash=frozen.hash))


class NextVersion(SyntheticLinearProtocol):
    protocol_id = ProtocolId("synthetic-linear", "2.0.0")


def test_run_and_validate_load_the_version_the_experiment_was_defined_with(harness: Harness) -> None:
    registry = harness.k.kernel.run_experiment.protocols
    defined = harness.define()
    completed = harness.run()
    registry.protocols["synthetic-linear"] = NextVersion()  # type: ignore[attr-defined]

    refusal = exactly("synthetic-linear 1.0.0 requested, 2.0.0 registered")
    with pytest.raises(InvariantViolation, match=refusal):
        harness.k.kernel.run_experiment.execute(defined.experiment_id)
    with pytest.raises(InvariantViolation, match=refusal):
        harness.k.kernel.validate_experiment.execute(completed.experiment_id)


def test_a_ledger_entry_without_a_frozen_predeclaration_is_refused(harness: Harness) -> None:
    unfrozen = "sha256:" + "2" * 64
    harness.k.ledger.append(
        kind="predeclaration", subject_id="draft", subject_hash=unfrozen, signer="leigh", payload={}
    )
    experiment = harness.define(predeclaration_hash=unfrozen)
    with pytest.raises(PredeclarationNotFrozen):
        harness.k.kernel.run_experiment.execute(experiment.experiment_id)
    stored = harness.k.kernel.query_evidence.experiment(experiment.experiment_id)
    assert stored is not None and stored.state is ExperimentState.REFUSED


def test_run_and_validate_name_an_unknown_experiment(harness: Harness) -> None:
    for use_case in (harness.k.kernel.run_experiment, harness.k.kernel.validate_experiment):
        with pytest.raises(KeyError) as error:
            use_case.execute("exp-unknown")
        assert error.value.args == ("exp-unknown",)


def test_validate_refuses_an_experiment_that_is_not_completed(harness: Harness) -> None:
    validate = harness.k.kernel.validate_experiment
    defined = harness.define()
    with pytest.raises(
        InvariantViolation, match=exactly(f"{defined.experiment_id} is defined; only completed experiments validate")
    ):
        validate.execute(defined.experiment_id)
    done = validated(harness)
    with pytest.raises(
        InvariantViolation, match=exactly(f"{done.experiment_id} is validated; only completed experiments validate")
    ):
        validate.execute(done.experiment_id)


def test_validate_names_the_predeclaration_that_is_no_longer_frozen(harness: Harness) -> None:
    experiment = harness.run()
    pre = experiment.definition.predeclaration_hash
    del harness.k.db.state.frozen[pre]
    with pytest.raises(PredeclarationNotFrozen) as error:
        harness.k.kernel.validate_experiment.execute(experiment.experiment_id)
    assert error.value.args == (pre,)


def test_validate_names_the_protocol_whose_checks_disagree() -> None:
    harness = harness_for(OptimisticProtocol(planted_slope=3.0))
    experiment = harness.run()
    message = "synthetic-linear@1.0.0 reported checks that differ from the kernel's evaluation of the frozen gates"
    with pytest.raises(VerdictInconsistent, match=exactly(message)):
        harness.k.kernel.validate_experiment.execute(experiment.experiment_id)


# -- manifest writer ----------------------------------------------------------------------------


def test_compatibility_records_are_listed_in_adapter_order() -> None:
    mast, gaia = compatibility(), compatibility("gaia-tap", product_type=ProductType.CATALOGUE)
    writer = ManifestWriter(InMemoryArtifactStore(), StaticCompatibilityRecords([mast, gaia]))
    products = {
        "image": ProductUse("mast-observations", "1.0.0", "image"),
        "catalogue": ProductUse("gaia-tap", "1.0.0", "catalogue"),
    }
    assert writer.records_for(products) == (gaia, mast)


def test_a_published_bundle_is_listed_as_a_zip(harness: Harness) -> None:
    experiment = validated(harness)
    kernel = harness.k.kernel
    dossier = kernel.assemble_dossier.execute(AssembleDossierCommand(experiment.experiment_id, ()))
    kernel.publish_bundle.approve(ApproveVerdictCommand(experiment.experiment_id, "reviewer"))
    kernel.publish_bundle.publish(dossier.dossier_id)
    manifest = kernel.query_evidence.manifest(experiment.experiment_id)
    assert manifest is not None
    media = {o["name"]: o["media_type"] for o in manifest["outputs"]}
    assert media["bundle"] == "application/zip" and media["findings"] == codec.MEDIA_TYPE
