"""UnitOfWork contract: repositories, the event log and the evidence graph commit together (KRN-010)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from sdp.kernel.domain import events as ev
from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.experiment import EnvironmentRecord, Experiment, ExperimentDefinition, InputBinding
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.ids import ArtifactId, ProtocolId
from sdp.kernel.domain.provenance import ProvGraph
from sdp.kernel.ports.repositories import UnitOfWork
from sdp.testing.synthetic import SyntheticLinearProtocol

AT = datetime(2026, 10, 28, 12, tzinfo=UTC)
UowFactory = Callable[[], UnitOfWork]


def sample_experiment(experiment_id: str = "exp-contract") -> Experiment:
    definition = ExperimentDefinition(
        protocol=ProtocolId("synthetic-linear", "1.0.0"),
        predeclaration_hash="sha256:" + "a" * 64,
        inputs=(InputBinding("series", ArtifactId("sha256:" + "b" * 64), "application/json"),),
        parameters={"k": 1},
        seed=3,
        environment=EnvironmentRecord("rev", "sha256:" + "c" * 64, "cpu", "3.12", "linux"),
    )
    return Experiment.define(experiment_id, definition, AT)


class UnitOfWorkContract:
    @pytest.mark.req("KRN-010")
    def test_state_and_events_commit_together(self, uow_factory: UowFactory) -> None:
        exp = sample_experiment()
        with uow_factory() as uow:
            uow.experiments.save(exp)
            uow.events.append(exp.pull_events())
            uow.commit()
        with uow_factory() as uow:
            assert uow.experiments.get(exp.experiment_id) is not None
            names = [e.event.name for e in uow.events.for_aggregate(exp.experiment_id)]
        assert names == ["ExperimentDefined"]

    @pytest.mark.req("KRN-010")
    def test_rollback_discards_state_and_events_together(self, uow_factory: UowFactory) -> None:
        exp = sample_experiment("exp-rolled-back")
        with uow_factory() as uow:
            uow.experiments.save(exp)
            uow.events.append(exp.pull_events())
            # no commit: leaving the block rolls back
        with uow_factory() as uow:
            assert uow.experiments.get("exp-rolled-back") is None
            assert list(uow.events.for_aggregate("exp-rolled-back")) == []

    @pytest.mark.req("KRN-010")
    def test_exception_inside_unit_of_work_rolls_back(self, uow_factory: UowFactory) -> None:
        exp = sample_experiment("exp-crash")
        with pytest.raises(RuntimeError), uow_factory() as uow:  # noqa: PT012 - the crash must happen inside the unit of work
            uow.experiments.save(exp)
            uow.events.append(exp.pull_events())
            raise RuntimeError("crash between write and commit")
        with uow_factory() as uow:
            assert uow.experiments.get("exp-crash") is None

    def test_event_sequence_numbers_increase(self, uow_factory: UowFactory) -> None:
        with uow_factory() as uow:
            seqs = uow.events.append([ev.HypothesisRegistered("h", AT, {}), ev.HypothesisRegistered("h", AT, {"v": 2})])
            uow.commit()
        assert list(seqs) == sorted(seqs) and len(set(seqs)) == 2
        with uow_factory() as uow:
            after = uow.events.read(after=seqs[0])
        assert [e.sequence for e in after if e.event.aggregate_id == "h"] == [seqs[1]]

    @pytest.mark.req("KRN-001")
    def test_hypothesis_versions_are_retained(self, uow_factory: UowFactory) -> None:
        h1 = Hypothesis("H-contract", 1, "first statement")
        h2 = h1.revise(statement="second statement")
        with uow_factory() as uow:
            uow.hypotheses.add(h1)
            uow.hypotheses.add(h2)
            uow.commit()
        with uow_factory() as uow:
            assert [h.statement for h in uow.hypotheses.versions("H-contract")] == [
                "first statement",
                "second statement",
            ]
            assert uow.hypotheses.get("H-contract", 1) == h1
            assert uow.hypotheses.get("H-contract") == h2

    @pytest.mark.req("KRN-001")
    def test_a_version_cannot_be_overwritten(self, uow_factory: UowFactory) -> None:
        h1 = Hypothesis("H-overwrite", 1, "original")
        with uow_factory() as uow:
            uow.hypotheses.add(h1)
            uow.commit()
        with pytest.raises(InvariantViolation), uow_factory() as uow:  # noqa: PT012 - add or commit may refuse
            uow.hypotheses.add(Hypothesis("H-overwrite", 1, "rewritten"))
            uow.commit()

    def test_frozen_predeclarations_round_trip(self, uow_factory: UowFactory) -> None:
        frozen = SyntheticLinearProtocol().predeclare().freeze()
        with uow_factory() as uow:
            uow.predeclarations.save_frozen(frozen, "led-000001")
            uow.commit()
        with uow_factory() as uow:
            assert uow.predeclarations.get_frozen(frozen.hash) == frozen
            assert uow.predeclarations.get_frozen("sha256:" + "0" * 64) is None

    def test_experiment_round_trips_with_manifest(self, uow_factory: UowFactory) -> None:
        exp = sample_experiment("exp-roundtrip")
        with uow_factory() as uow:
            uow.experiments.save(exp)
            uow.experiments.save_manifest(exp.experiment_id, {"schema_version": "1.0.0", "x": [1, 2]})
            uow.commit()
        with uow_factory() as uow:
            loaded = uow.experiments.get("exp-roundtrip")
            assert loaded is not None and loaded.definition == exp.definition and loaded.state == exp.state
            assert uow.experiments.manifest("exp-roundtrip") == {"schema_version": "1.0.0", "x": [1, 2]}

    @pytest.mark.req("KRN-010")
    def test_evidence_graph_answers_lineage(self, uow_factory: UowFactory) -> None:
        g = ProvGraph()
        g.entity("raw-1")
        g.entity("curated-1")
        g.activity("curation-1")
        g.relate("used", "curation-1", "raw-1")
        g.relate("wasGeneratedBy", "curated-1", "curation-1")
        g.relate("wasDerivedFrom", "curated-1", "raw-1")
        with uow_factory() as uow:
            uow.evidence.record(g)
            uow.commit()
        with uow_factory() as uow:
            lineage = uow.evidence.lineage("curated-1")
        assert "sdp:curation-1" in lineage and "sdp:raw-1" in lineage
