"""T1: the Investigate domain: Field, TestProfile, identification records, results and the
Investigation aggregate's states and promotion invariant."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from sdp.kernel.domain.archive import Box, Cone
from sdp.kernel.domain.errors import InvalidTransition, InvariantViolation, PromotionRefused
from sdp.kernel.domain.gates import Comparator, Gate
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.investigation import (
    EXPLORATORY,
    UNMATCHED,
    EvidenceBundle,
    EvidenceUsed,
    Field,
    IdentificationMethod,
    IdentifiedObject,
    Investigation,
    InvestigationSet,
    InvestigationState,
    NullFieldResult,
    ObjectSet,
    Polygon,
    RankedCandidate,
    RankingPolicy,
    Stage,
    Subject,
    TestProfile,
    TestResult,
    TestSelection,
    parse_region,
    rank_by_significance,
)
from sdp.kernel.domain.predeclaration import Predeclaration
from sdp.testing.investigate import synthetic_profile

pytestmark = pytest.mark.tier("T1")

AT = datetime(2026, 10, 28, 12, tzinfo=UTC)
A = ArtifactId("sha256:" + "a" * 64)
B = ArtifactId("sha256:" + "b" * 64)


def result(key: str = "gaia:DR3:1", p: float | None = 0.001, name: str = "pm-excess") -> TestResult:
    return TestResult(name, "1.0.0", Subject.of_object(key), 7.0, seed=3, p_value=p)


def investigation(region: Cone | None = None) -> Investigation:
    r = result()
    return Investigation.open(
        investigation_id="inv-1",
        set_id="invset-1",
        field_id="field-1",
        candidate=RankedCandidate(r.subject, 1.0, (r,)),
        evidence_bundle_ids=("bundle-1",),
        policy=RankingPolicy("significance", "1.0.0", 0.5),
        evidence_used=EvidenceUsed(
            archives=("gaia",),
            releases=("gaia:DR3",),
            region=(region or Cone(120.0, -10.0, 0.5)).to_document(),
            epoch_min_jyear=1890.0,
            epoch_max_jyear=2016.0,
        ),
        at=AT,
    )


def confirmatory(*evidence: dict[str, str]) -> Predeclaration:
    return Predeclaration(
        predeclaration_id="pre-confirm",
        protocol_id="uc01-stellar",
        protocol_version="1.0.0",
        title="Confirm the candidate",
        hypotheses=(Hypothesis("H-1", 1, "the group is co-moving"),),
        gates=(Gate("STL-G-01", "membership", "membership", Comparator.GE, 0.5),),
        parameters={"confirmatory_evidence": list(evidence)},
    )


# -- Field ----------------------------------------------------------------------------------------


@pytest.mark.req("INV-001")
def test_cli_region_text_parses_to_a_bounded_field() -> None:
    cone = parse_region("icrs;circle(120.0,-10.0,0.5)")
    assert cone == Cone(120.0, -10.0, 0.5)
    box = parse_region("box(10, 0, 2, 1)")
    assert isinstance(box, Box) and box.dec_min_deg == -0.5
    poly = parse_region("icrs;polygon(10,0,11,0,11,1,10,1)")
    assert isinstance(poly, Polygon) and poly.contains(10.5, 0.5) and not poly.contains(12, 0.5)
    with pytest.raises(InvariantViolation):
        parse_region("icrs;circle(120,-10,30)")
    with pytest.raises(InvariantViolation):
        parse_region("ellipse(1,2,3,4,5)")


def test_field_identity_is_its_content_and_round_trips() -> None:
    f = Field(parse_region("icrs;polygon(10,0,11,0,11,1)"), epoch_jyear=2016.0, label="tri")
    assert Field.from_document(f.to_document()) == f
    assert f.field_id == Field.from_document(f.to_document()).field_id
    assert f.field_id != replace(f, epoch_jyear=2017.0).field_id
    q = f.query_region()
    assert isinstance(q, Cone) and q.radius_deg > 0


def test_polygon_query_region_covers_every_vertex() -> None:
    poly = Polygon(((10.0, 0.0), (11.0, 0.0), (11.0, 1.0), (10.0, 1.0)))
    cone = poly.bounding_cone()
    f = Field(poly)
    for ra, dec in poly.vertices:
        assert Field(cone).contains(ra, dec)
    assert f.contains(10.5, 0.5)


# -- Profile --------------------------------------------------------------------------------------


@pytest.mark.req("INV-001")
def test_profile_hash_is_stable_and_changes_with_any_threshold() -> None:
    p = synthetic_profile()
    again = TestProfile.from_document(p.to_document())
    assert again.profile_hash == p.profile_hash
    tweaked = replace(p, tests=(TestSelection("pm-excess", "1.0.0", {"score": 4.0}),))
    assert tweaked.profile_hash != p.profile_hash


def test_profile_refuses_duplicates_and_empty_test_lists() -> None:
    p = synthetic_profile()
    with pytest.raises(InvariantViolation):
        replace(p, tests=())
    with pytest.raises(InvariantViolation):
        replace(p, tests=(p.tests[0], p.tests[0]))
    with pytest.raises(InvariantViolation):
        replace(p, version="v1")


# -- Identify -------------------------------------------------------------------------------------


@pytest.mark.req("INV-003")
def test_identification_records_name_method_source_and_gaia_key() -> None:
    obj = IdentifiedObject("gaia:DR3:1", IdentificationMethod.CATALOGUE, 1.0, 2.0, (A,), gaia_source_key="gaia:DR3:1")
    assert IdentifiedObject.from_document(obj.to_document()) == obj
    with pytest.raises(InvariantViolation, match="artifacts"):
        IdentifiedObject("gaia:DR3:1", IdentificationMethod.CATALOGUE, 1.0, 2.0, (), gaia_source_key="gaia:DR3:1")
    with pytest.raises(InvariantViolation, match="Gaia source key"):
        IdentifiedObject("x", IdentificationMethod.CATALOGUE, 1.0, 2.0, (A,))


@pytest.mark.req("INV-003")
def test_unmatched_detections_are_flagged_and_matches_stay_within_radius() -> None:
    common: dict[str, Any] = {"pixel_xy": (10.0, 20.0), "wcs_artifact": B, "match_radius_arcsec": 1.0}
    unmatched = IdentifiedObject(
        "field-x:0:3", IdentificationMethod.DETECTION, 1.0, 2.0, (A, B), flags=(UNMATCHED,), **common
    )
    assert unmatched.unmatched and unmatched.gaia_source_key is None
    with pytest.raises(InvariantViolation, match="flagged"):
        IdentifiedObject("field-x:0:4", IdentificationMethod.DETECTION, 1.0, 2.0, (A, B), **common)
    with pytest.raises(InvariantViolation, match="within"):
        IdentifiedObject(
            "gaia:DR3:2",
            IdentificationMethod.DETECTION,
            1.0,
            2.0,
            (A, B),
            gaia_source_key="gaia:DR3:2",
            match_distance_arcsec=1.5,
            **common,
        )
    with pytest.raises(InvariantViolation, match="WCS"):
        IdentifiedObject("gaia:DR3:2", IdentificationMethod.CATALOGUE, 1.0, 2.0, (A,), "gaia:DR3:2", (1.0, 1.0))
    with pytest.raises(InvariantViolation, match="unique"):
        ObjectSet("f", (unmatched, unmatched))


# -- Retrieve -------------------------------------------------------------------------------------


@pytest.mark.req("INV-005")
def test_a_bundle_cannot_hold_rows_without_their_registered_artifact() -> None:
    obj = IdentifiedObject("gaia:DR3:1", IdentificationMethod.CATALOGUE, 1.0, 2.0, (A,), gaia_source_key="gaia:DR3:1")
    rows = {"gaia": ({"source_key": "gaia:DR3:1", "ra_deg": 1.0},)}
    with pytest.raises(InvariantViolation, match="INV-005"):
        EvidenceBundle(obj, rows, {})
    bundle = EvidenceBundle(obj, rows, {"gaia": A}, releases=("gaia:DR3",))
    assert EvidenceBundle.from_document(bundle.to_document()) == bundle
    assert bundle.bundle_id.startswith("bundle-")


# -- Test results ---------------------------------------------------------------------------------


@pytest.mark.req("INV-008")
def test_results_and_investigations_are_exploratory_and_carry_no_verdict() -> None:
    r = result()
    assert r.label == EXPLORATORY and TestResult.from_document(r.to_document()) == r
    with pytest.raises(InvariantViolation, match="INV-008"):
        replace(r, label="VERIFIED")
    inv = investigation()
    assert inv.label == EXPLORATORY
    assert not hasattr(inv, "verdict") and "verdict" not in inv.to_document()
    with pytest.raises(InvariantViolation):
        replace(inv, label="CONFIRMED")


def test_subjects_are_objects_or_structures_never_both() -> None:
    s = Subject(structure_id="g1", members=("a", "b", "c"), membership=(0.9, 0.8, 0.7))
    assert s.key == "structure:g1"
    with pytest.raises(InvariantViolation):
        Subject(object_key="a", structure_id="g1", members=("a", "b"))
    with pytest.raises(InvariantViolation):
        Subject(structure_id="g1", members=("a",))
    with pytest.raises(InvariantViolation):
        Subject(structure_id="g1", members=("a", "b"), membership=(0.5,))


def test_ranking_keeps_every_result_including_those_against() -> None:
    results = [
        result("a", 0.001),
        result("a", 0.6, name="cmd"),
        result("b", 0.5),
        result("c", None),
    ]
    ranked = rank_by_significance(results, RankingPolicy("significance", "1.0.0", 0.5, alpha=0.01))
    assert [c.subject.key for c in ranked] == ["a", "b", "c"]
    assert ranked[0].score == 0.5 and len(ranked[0].results) == 2
    assert ranked[2].score == 0.0


def test_null_field_rate() -> None:
    n = NullFieldResult("pm-excess", "1.0.0", trials=10, examined=200, false_positives=4, alpha=0.01, null_model="x")
    assert n.false_positive_rate == 0.02
    with pytest.raises(InvariantViolation):
        replace(n, false_positives=201)


# -- Investigation aggregate ----------------------------------------------------------------------


def test_triage_transitions_record_reason_and_actor() -> None:
    inv = investigation()
    inv.park("waiting for Gaia DR4", "leigh", AT)
    inv.reopen("DR4 landed", "leigh", AT)
    inv.annotate("looks like a binary", "leigh", AT)
    inv.close("image artefact", "leigh", AT)
    assert [t.to_state for t in inv.transitions] == [
        InvestigationState.OPEN,
        InvestigationState.PARKED,
        InvestigationState.OPEN,
        InvestigationState.CLOSED,
    ]
    names = [e.name for e in inv.pull_events()]
    assert names == [
        "InvestigationOpened",
        "InvestigationParked",
        "InvestigationReopened",
        "InvestigationAnnotated",
        "InvestigationClosed",
    ]
    with pytest.raises(InvalidTransition):
        inv.reopen("again", "leigh", AT)
    with pytest.raises(InvalidTransition):
        inv.annotate("too late", "leigh", AT)
    restored = Investigation.from_document(inv.to_document())
    assert restored.to_document() == inv.to_document()


def test_transitions_need_a_reason() -> None:
    with pytest.raises(InvariantViolation, match="reason"):
        investigation().park("  ", "leigh", AT)


@pytest.mark.req("INV-009")
def test_promotion_needs_evidence_the_investigation_did_not_use() -> None:
    same = confirmatory({"kind": "archive", "value": "gaia"}, {"kind": "release", "value": "gaia:DR3"}).freeze()
    inv = investigation()
    with pytest.raises(PromotionRefused, match="INV-009"):
        inv.promote(same, actor="leigh", at=AT)
    assert inv.state is InvestigationState.OPEN
    nothing = confirmatory().freeze()
    with pytest.raises(PromotionRefused):
        inv.promote(nothing, actor="leigh", at=AT)


@pytest.mark.req("INV-009")
@pytest.mark.parametrize(
    "evidence",
    [
        {"kind": "release", "value": "gaia:DR4"},
        {"kind": "archive", "value": "dasch"},
        {"kind": "epoch", "value": "2026.9"},
        {"kind": "region", "value": "icrs;circle(130.0,-10.0,0.5)"},
    ],
)
def test_promotion_with_unused_evidence_links_the_predeclaration(evidence: dict[str, str]) -> None:
    frozen = confirmatory(evidence).freeze()
    inv = investigation()
    inv.promote(frozen, actor="leigh", at=AT, experiment_id="exp-9")
    assert inv.state is InvestigationState.PROMOTED
    assert inv.links["predeclaration_hash"] == frozen.hash and inv.links["experiment_id"] == "exp-9"
    assert inv.read_only
    with pytest.raises(InvalidTransition):
        inv.annotate("edit after promotion", "leigh", AT)
    with pytest.raises(InvalidTransition):
        inv.park("no", "leigh", AT)
    inv.close("failed confirmation", "leigh", AT)
    assert inv.to_document()["state"] == "closed"


def test_held_out_region_inside_the_field_is_not_new_evidence() -> None:
    frozen = confirmatory({"kind": "region", "value": "icrs;circle(120.1,-10.0,0.1)"}).freeze()
    with pytest.raises(PromotionRefused):
        investigation().promote(frozen, actor="leigh", at=AT)


def test_set_stages_complete_in_order_and_failure_keeps_earlier_stages() -> None:
    s = InvestigationSet("invset-1", Field(Cone(1.0, 1.0, 0.1)), synthetic_profile(), 0, AT)
    with pytest.raises(InvalidTransition):
        s.complete_stage(Stage.RETRIEVE, A, AT)
    s.complete_stage(Stage.IDENTIFY, A, AT, objects=3)
    s.fail_stage(Stage.RETRIEVE, "archive down", AT)
    assert s.stage_done(Stage.IDENTIFY) and not s.stage_done(Stage.RETRIEVE)
    restored = InvestigationSet.from_document(s.to_document())
    assert restored.to_document() == s.to_document()
    assert [e.name for e in s.pull_events()] == ["InvestigationStageCompleted", "InvestigationStageFailed"]
