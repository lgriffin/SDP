"""T1: the smaller domain records as stored and refused: dataset artifacts, numerical policy, the
event envelope and query regions. Each test pins a detail mutation testing showed nothing else
checked (docs/testing.md, "Killing a survivor")."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pytest

from sdp.kernel.domain import events as ev
from sdp.kernel.domain.archive import Box, Cone, region_from_document
from sdp.kernel.domain.dataset import (
    AcquisitionPart,
    AcquisitionRecord,
    ArtifactKind,
    DatasetArtifact,
    DatasetStatus,
    DialectRef,
    ProductUse,
)
from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.gates import Comparator, Gate
from sdp.kernel.domain.hypothesis import Hypothesis, HypothesisHistory
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.policy import BITWISE, DeterminismClass, NumericalPolicy, SeedHandling
from sdp.kernel.domain.predeclaration import FrozenPredeclaration, OracleTolerance, Predeclaration, Target
from sdp.kernel.domain.prediction import Prediction
from sdp.kernel.domain.provenance import ProvGraph
from sdp.kernel.domain.sources import SourceKind, SourceRef

pytestmark = pytest.mark.tier("T1")

AT = datetime(2026, 10, 28, 12, 30, tzinfo=UTC)
A = ArtifactId("sha256:" + "a" * 64)
B = ArtifactId("sha256:" + "b" * 64)
C = ArtifactId("sha256:" + "c" * 64)
SOURCE = SourceRef(SourceKind.ARCHIVE, "gaia", "CC-BY-4.0", "Gaia Collaboration 2023", release_label="gaia:DR3")


def violation(message: str) -> Any:
    return pytest.raises(InvariantViolation, match="^" + re.escape(message) + "$")


# -- Dataset artifacts ------------------------------------------------------------------------------


def _acquired() -> DatasetArtifact:
    record = AcquisitionRecord(
        "SELECT ra FROM gaiadr3.gaia_source",
        "idem-1",
        "gaia:DR3",
        AT,
        A,
        300,
        (AcquisitionPart(0, B, 100, 10), AcquisitionPart(1, C, 200, 20)),
    )
    return DatasetArtifact.acquired_raw(
        actual=A, media_type="application/x-parquet", byte_count=300, source_ref=SOURCE, acquisition=record
    )


def test_an_acquired_artifact_document_keeps_its_acquisition_and_parts() -> None:
    raw = _acquired()
    doc = raw.to_document()
    assert doc["acquisition"] == {
        "rendered_query": "SELECT ra FROM gaiadr3.gaia_source",
        "idempotency_key": "idem-1",
        "source_release": "gaia:DR3",
        "acquired_at": "2026-10-28T12:30:00Z",
        "checksum": A.value,
        "byte_count": 300,
        "parts": [
            {"part": 0, "artifact_id": B.value, "byte_count": 100, "row_count": 10},
            {"part": 1, "artifact_id": C.value, "byte_count": 200, "row_count": 20},
        ],
    }
    assert DatasetArtifact.from_document(doc) == raw


def test_a_curated_or_rejected_artifact_document_round_trips() -> None:
    raw = _acquired()
    curated = raw.curate(curated_id=B, byte_count=10, dialect=DialectRef("gaia-dr3", "1.0.0"), media_type="text/csv")
    assert DatasetArtifact.from_document(curated.to_document()) == curated
    assert curated.to_document()["parents"] == [A.value]
    rejected = DatasetArtifact.acquired_raw(
        actual=C,
        media_type="text/csv",
        byte_count=1,
        source_ref=SOURCE,
        acquisition=raw.acquisition,  # type: ignore[arg-type]
    )
    assert rejected.status is DatasetStatus.REJECTED
    assert DatasetArtifact.from_document(rejected.to_document()) == rejected


def test_an_artifact_document_without_optional_parts_takes_the_defaults() -> None:
    doc = _acquired().to_document()
    for key in ("parents", "rejection_reason"):
        del doc[key]
    del doc["acquisition"]["parts"]
    restored = DatasetArtifact.from_document(doc)
    assert restored.parents == () and restored.rejection_reason is None
    assert restored.acquisition is not None and restored.acquisition.parts == ()
    bare = DatasetArtifact(A, ArtifactKind.RAW, DatasetStatus.ACQUIRED, "text/csv", 1, SOURCE)
    assert DatasetArtifact.from_document(bare.to_document()) == bare


def test_a_product_use_names_its_adapter() -> None:
    with violation("a product use names its adapter"):
        ProductUse("", "1.0.0", "image")


# -- Numerical policy -------------------------------------------------------------------------------


def test_policy_refusals_say_why() -> None:
    with violation("unknown dtype 'float16'"):
        NumericalPolicy(dtype="float16")
    for rtol, atol in ((-1e-9, 0.0), (0.0, -1e-9)):
        with violation("tolerances must be non-negative"):
            NumericalPolicy(rtol=rtol, atol=atol)
    NumericalPolicy(rtol=0.0, atol=0.0)
    with violation("a bitwise policy cannot carry tolerances"):
        NumericalPolicy(rtol=1e-9, determinism=DeterminismClass.BITWISE)


def test_policy_agreement_in_every_branch() -> None:
    tolerant = NumericalPolicy(rtol=1e-6, atol=0.0)
    assert tolerant.agrees([1.0, 2.0], [1.0, 2.0])
    assert not tolerant.agrees([1.0, 2.0], [1.0, 2.0, 3.0])
    assert tolerant.agrees([1.0, np.nan], [1.0, np.nan])
    assert tolerant.agrees(1.0, 1.0 + 1e-7) and not tolerant.agrees(1.0, 1.0 + 1e-5)
    assert not tolerant.agrees(0.0, 1e-9)  # no absolute slack unless the policy grants it
    assert not tolerant.agrees(1_000_000_000, 1_000_000_001)  # integers compare exactly
    assert tolerant.agrees(np.array(["a", "b"]), np.array(["a", "b"]))
    assert BITWISE.agrees([np.nan, 1.0], [np.nan, 1.0])
    assert not BITWISE.agrees(1.0, 1.0 + 1e-15)
    assert not BITWISE.agrees([1.0], [1.0, 1.0])


def test_policy_document_round_trips() -> None:
    policy = NumericalPolicy("float32", 1e-5, 1e-8, DeterminismClass.TOLERANCE, SeedHandling.DERIVED)
    assert NumericalPolicy.from_document(policy.to_document()) == policy


# -- Event envelope ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ev.EVENT_NAMES)
def test_every_event_round_trips_through_its_envelope(name: str) -> None:
    cls = ev.EVENT_TYPES[name]
    assert cls.name == name and cls.__name__ == name and cls.__doc__ == f"Domain event {name}."
    assert issubclass(cls, ev.DomainEvent)
    event = cls("agg-1", AT, {"k": 1})
    doc = event.to_document()
    assert doc == {"name": name, "aggregate_id": "agg-1", "occurred_at": AT.isoformat(), "payload": {"k": 1}}
    again = ev.event_from_document(doc)
    assert type(again) is cls and again == event


def test_an_envelope_without_a_payload_has_an_empty_one() -> None:
    again = ev.event_from_document(
        {"name": "ExperimentDefined", "aggregate_id": "exp-1", "occurred_at": AT.isoformat()}
    )
    assert again == ev.ExperimentDefined("exp-1", AT, {})


# -- Query regions ----------------------------------------------------------------------------------


def test_box_bounds_and_area() -> None:
    Box(0.0, 10.0, 0.0, 10.0)  # exactly 100 square degrees
    with violation("box larger than 100 square degrees; remote queries must stay bounded"):
        Box(0.0, 10.0, 0.0, 10.01)
    with violation("box larger than 100 square degrees; remote queries must stay bounded"):
        Box(300.0, 311.0, 20.0, 29.5)  # the area is the width times the height
    Box(300.0, 309.0, 20.0, 31.0)
    for bounds in ((1.0, 1.0, 0.0, 1.0), (0.0, 1.0, 1.0, 1.0), (2.0, 1.0, 0.0, 1.0), (0.0, 1.0, 2.0, 1.0)):
        with violation("box bounds must be ordered"):
            Box(*bounds)


def test_regions_round_trip_through_their_documents() -> None:
    box = Box(10.0, 11.0, -5.0, -4.0)
    assert box.to_document() == {
        "shape": "box",
        "ra_min_deg": 10.0,
        "ra_max_deg": 11.0,
        "dec_min_deg": -5.0,
        "dec_max_deg": -4.0,
    }
    assert region_from_document(box.to_document()) == box
    assert region_from_document({"ra_deg": 1.0, "dec_deg": 2.0, "radius_deg": 0.5}) == Cone(1.0, 2.0, 0.5)
    assert region_from_document(Cone(1.0, 2.0, 0.5).to_document()) == Cone(1.0, 2.0, 0.5)


# -- Predeclarations --------------------------------------------------------------------------------


def _predeclaration(**changes: Any) -> Predeclaration:
    fields: dict[str, Any] = {
        "predeclaration_id": "pre-1",
        "protocol_id": "uc02-relativity",
        "protocol_version": "1.0.0",
        "title": "Light bending",
        "hypotheses": (Hypothesis("H-1", 1, "light bends by 1.75 arcsec at the limb"),),
        "gates": (Gate("REL-G-01", "deflection", "deflection", Comparator.LE, 0.5, oracle_ids=("eddington",)),),
        "oracle_tolerances": (OracleTolerance("eddington", 1e-3, 0.0, "arcsec"),),
        "targets": (Target("sobral", "region", "the 1919 plates", {"ra_deg": 100.0}),),
        "parameters": {"k": 1},
        "notes": "transcribed by hand",
    }
    fields.update(changes)
    return Predeclaration(**fields)


def test_predeclaration_refusals_say_why() -> None:
    with violation("a predeclaration states at least one hypothesis"):
        _predeclaration(hypotheses=())
    with violation("a predeclaration declares at least one gate"):
        _predeclaration(gates=())
    gate = _predeclaration().gates[0]
    with violation("gate ids must be unique"):
        _predeclaration(gates=(gate, gate))
    with violation("gates use oracles without a predeclared tolerance: ['eddington']"):
        _predeclaration(oracle_tolerances=())
    with violation("target kind must be region or source, got 'galaxy'"):
        Target("t", "galaxy", "why")
    Target("t", "source", "why")
    with violation("target 't' needs a recorded reason"):
        Target("t", "region", "  ")
    p = _predeclaration()
    with violation("frozen hash does not match the predeclaration's canonical JSON"):
        FrozenPredeclaration(p, "sha256:" + "0" * 64)


def test_predeclaration_document_round_trips_and_defaults() -> None:
    p = _predeclaration()
    assert Predeclaration.from_document(p.to_document()) == p
    doc = p.to_document()
    for key in ("targets", "parameters", "notes"):
        del doc[key]
    del doc["oracle_tolerances"][0]["unit"]
    bare = Predeclaration.from_document(doc)
    assert (bare.targets, bare.parameters, bare.notes) == ((), {}, None)
    assert bare.oracle_tolerances == (OracleTolerance("eddington", 1e-3, 0.0),)
    no_spec = {"target_id": "t", "kind": "region", "reason": "why"}
    assert Predeclaration.from_document({**doc, "targets": [no_spec]}).targets == (Target("t", "region", "why"),)
    free = _predeclaration(gates=(Gate("REL-G-03", "d", "f", Comparator.LE, 1.0),), oracle_tolerances=())
    free_doc = free.to_document()
    del free_doc["oracle_tolerances"]
    assert Predeclaration.from_document(free_doc) == free


def test_gates_are_found_by_id() -> None:
    two = _predeclaration(gates=(*_predeclaration().gates, Gate("REL-G-02", "d", "f", Comparator.GE, 0.0)))
    assert two.gate("REL-G-02").comparator is Comparator.GE
    with pytest.raises(KeyError, match="'REL-G-09'"):
        two.gate("REL-G-09")


# -- Hypotheses -------------------------------------------------------------------------------------


def test_hypothesis_refusals_say_why() -> None:
    with violation("hypothesis versions start at 1"):
        Hypothesis("H-1", 0, "s")
    with violation("a hypothesis needs a statement"):
        Hypothesis("H-1", 1, "  ")
    with violation("a hypothesis version supersedes exactly its predecessor"):
        Hypothesis("H-1", 3, "s", supersedes=1)


def test_a_revision_keeps_whatever_it_does_not_change() -> None:
    p1 = Prediction("gr", "1.0.0")
    h1 = Hypothesis("H-1", 1, "first", (p1,), ("chi2 < 2",))
    h2 = h1.revise(statement="second")
    assert (h2.version, h2.supersedes, h2.statement, h2.predictions, h2.evaluation_criteria) == (
        2,
        1,
        "second",
        (p1,),
        ("chi2 < 2",),
    )
    p2 = Prediction("gr", "1.1.0")
    h3 = h2.revise(predictions=(p2,), evaluation_criteria=("chi2 < 1",))
    assert (h3.statement, h3.predictions, h3.evaluation_criteria) == ("second", (p2,), ("chi2 < 1",))
    assert h3.revise().statement == "second"


def test_hypothesis_document_defaults() -> None:
    bare = Hypothesis.from_document({"hypothesis_id": "H-1", "version": 2, "statement": "s"})
    assert bare == Hypothesis("H-1", 2, "s", supersedes=1)


def test_a_history_holds_one_hypothesis_in_version_order() -> None:
    h1 = Hypothesis("H-1", 1, "first")
    history = HypothesisHistory("H-1")
    history.append(h1)
    with violation("expected version 2, got 3"):
        history.append(Hypothesis("H-1", 3, "skip", supersedes=2))
    with violation("expected version 2, got 1"):
        history.append(h1)
    with violation("history belongs to a single hypothesis"):
        history.append(Hypothesis("H-2", 2, "other", supersedes=1))
    h2 = h1.revise(statement="second")
    history.append(h2)
    assert history.versions == [h1, h2] and history.latest is h2


# -- Sources ----------------------------------------------------------------------------------------


def test_a_source_with_a_query_keeps_everything_else() -> None:
    doc = SourceRef(
        SourceKind.DOCUMENT,
        "eddington-1920",
        "public domain",
        "Dyson et al. 1920",
        doi="10.1098/rsta.1920.0009",
        bibcode="1920RSPTA.220..291D",
    )
    queried = doc.with_query("page 291")
    assert queried == SourceRef(
        SourceKind.DOCUMENT,
        "eddington-1920",
        "public domain",
        "Dyson et al. 1920",
        query_text="page 291",
        doi="10.1098/rsta.1920.0009",
        bibcode="1920RSPTA.220..291D",
    )
    assert SOURCE.with_query("SELECT 1").release_label == "gaia:DR3"
    assert SourceRef.from_document(queried.to_document()) == queried
    bare = {"kind": "archive", "identity": "gaia", "release_label": "gaia:DR3", "licence": "x", "citation": "y"}
    assert SourceRef.from_document(bare) == SourceRef(SourceKind.ARCHIVE, "gaia", "x", "y", release_label="gaia:DR3")


# -- Provenance -------------------------------------------------------------------------------------


def _graph() -> ProvGraph:
    g = ProvGraph()
    g.entity("raw", kind="raw")
    g.entity("curated")
    g.entity("result")
    g.activity("curate")
    g.activity("fit", backend="cpu")
    g.agent("leigh")
    g.relate("used", "curate", "raw")
    g.relate("wasGeneratedBy", "curated", "curate")
    g.relate("used", "fit", "curated")
    g.relate("wasGeneratedBy", "result", "fit")
    g.relate("wasDerivedFrom", "curated", "raw")
    g.relate("wasAssociatedWith", "fit", "leigh")
    g.relate("wasAttributedTo", "raw", "leigh")
    return g


def test_ancestors_are_every_upstream_node_nearest_first() -> None:
    g = _graph()
    assert g.ancestors("result") == ["sdp:fit", "sdp:curated", "sdp:curate", "sdp:raw"]
    g.relate("wasDerivedFrom", "raw", "result")  # a cycle back to the start is not an ancestor
    assert g.ancestors("result") == ["sdp:fit", "sdp:curated", "sdp:curate", "sdp:raw"]
    assert g.ancestors("raw") == ["sdp:result", "sdp:fit", "sdp:curated", "sdp:curate"]


def test_a_subgraph_explains_one_activity() -> None:
    g = _graph()
    g.activity("unrelated")
    g.relate("used", "unrelated", "raw")
    sub = g.subgraph("fit")
    assert sub.activities == {"sdp:fit": {"sdp:backend": "cpu"}, "sdp:curate": {}}
    assert sub.relations["used"] == [("sdp:fit", "sdp:curated")]
    assert sub.relations["wasGeneratedBy"] == [("sdp:result", "sdp:fit"), ("sdp:curated", "sdp:curate")]
    assert sub.relations["wasDerivedFrom"] == [("sdp:curated", "sdp:raw")]
    assert sub.relations["wasAttributedTo"] == [("sdp:raw", "sdp:leigh")]
    assert set(sub.entities) == {"sdp:result", "sdp:curated", "sdp:raw"}
    assert sub.entities["sdp:raw"] == {"sdp:kind": "raw"}
    assert sub.agents == {"sdp:leigh": {}}
    assert ProvGraph().subgraph("missing").activities == {}


def test_prov_json_names_each_relation_and_round_trips() -> None:
    g = _graph()
    doc = g.to_prov_json()
    assert doc["used"] == {
        "_:use0": {"prov:activity": "sdp:curate", "prov:entity": "sdp:raw"},
        "_:use1": {"prov:activity": "sdp:fit", "prov:entity": "sdp:curated"},
    }
    assert set(doc["wasDerivedFrom"]) == {"_:was0"}
    again = ProvGraph.from_prov_json(doc)
    assert again == g and again.agents == {"sdp:leigh": {}}
    assert ProvGraph.from_prov_json({}) == ProvGraph()


def test_a_relation_is_recorded_once_even_after_a_loader_appended_it_directly() -> None:
    g = ProvGraph()
    g.relate("used", "a/1", "e/1")
    g.relations["used"].append(("sdp:a/2", "sdp:e/2"))  # as the Postgres loader fills a graph
    g.relate("used", "a/2", "e/2")
    g.relate("used", "a/1", "e/1")
    other = ProvGraph()
    other.relate("used", "a/1", "e/1")
    other.relate("used", "a/3", "e/3")
    g.merge(other)
    assert g.relations["used"] == [("sdp:a/1", "sdp:e/1"), ("sdp:a/2", "sdp:e/2"), ("sdp:a/3", "sdp:e/3")]


def test_relating_scales_with_the_number_of_relations_not_its_square() -> None:
    import time

    g = ProvGraph()
    started = time.perf_counter()
    for i in range(50_000):  # a 190,000-star field opens tens of thousands of candidates (#67)
        g.relate("wasDerivedFrom", f"inv/{i}", f"sha256:{i:064x}")
    assert len(g.relations["wasDerivedFrom"]) == 50_000
    assert time.perf_counter() - started < 5.0, "a list scan per relation took minutes here"
