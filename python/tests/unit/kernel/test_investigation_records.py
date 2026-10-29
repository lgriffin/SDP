"""T1: the Investigate records as stored: every field survives its document, every default is the
documented one, every refusal says why, and every triage step emits the event it names.

The broader behaviour lives in test_investigation.py; these pin the details that mutation testing
showed nothing else checked (docs/testing.md, "Killing a survivor")."""

from __future__ import annotations

import math
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from sdp.kernel.domain.archive import Box, Cone, Filter
from sdp.kernel.domain.errors import InvalidTransition, InvariantViolation
from sdp.kernel.domain.gates import Comparator, Gate
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.investigation import (
    EXPLORATORY,
    ArchiveRole,
    ArchiveSelection,
    EvidenceUsed,
    FetchedProduct,
    Field,
    FieldImage,
    IdentificationMethod,
    IdentifiedObject,
    Investigation,
    InvestigationSet,
    InvestigationState,
    NullFieldResult,
    ObjectSet,
    ObservationCriteria,
    ObservationIndex,
    ObservationRecord,
    Polygon,
    ProductEvidence,
    ProductRef,
    RankedCandidate,
    RankingPolicy,
    ResolverRecord,
    Stage,
    StageRecord,
    StageState,
    Subject,
    TestResult,
    confirmatory_evidence,
    parse_region,
)
from sdp.kernel.domain.predeclaration import Predeclaration
from sdp.testing.investigate import synthetic_profile

pytestmark = pytest.mark.tier("T1")

AT = datetime(2026, 10, 28, 12, tzinfo=UTC)
LATER = datetime(2026, 10, 29, 9, 30, tzinfo=UTC)
A = ArtifactId("sha256:" + "a" * 64)
B = ArtifactId("sha256:" + "b" * 64)
C = ArtifactId("sha256:" + "c" * 64)


def violation(message: str) -> Any:
    """The exact refusal, so a changed or missing message is a failure."""
    return pytest.raises(InvariantViolation, match="^" + re.escape(message) + "$")


def refused(message: str) -> Any:
    return pytest.raises(InvalidTransition, match="^" + re.escape(message) + "$")


# -- Regions --------------------------------------------------------------------------------------


def _unit(ra: float, dec: float) -> tuple[float, float, float]:
    r, d = math.radians(ra), math.radians(dec)
    return (math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d))


def _side(a: tuple[float, ...], b: tuple[float, ...], p: tuple[float, ...]) -> float:
    """Which side of the great circle through a and b the point p lies on (a triple product)."""
    return a[0] * (b[1] * p[2] - b[2] * p[1]) - a[1] * (b[0] * p[2] - b[2] * p[0]) + a[2] * (b[0] * p[1] - b[1] * p[0])


@pytest.mark.parametrize(
    "vertices",
    [
        ((10.0, 40.0), (13.0, 41.0), (11.0, 43.5)),
        ((358.0, -30.0), (2.5, -31.0), (1.0, -27.0)),
        ((200.0, 5.0), (203.0, 5.5), (204.0, 8.0), (201.0, 9.0)),
    ],
)
def test_polygon_containment_matches_the_great_circle_edges(vertices: tuple[tuple[float, float], ...]) -> None:
    """A gnomonic projection keeps great circles straight, so containment in the tangent plane must
    agree with the sign of each edge's triple product on the sphere."""
    poly = Polygon(vertices)
    corners = [_unit(ra, dec) for ra, dec in vertices]
    edges = list(zip(corners, corners[1:] + corners[:1], strict=True))
    turn = math.copysign(1.0, _side(corners[0], corners[1], corners[2]))
    ras = [ra if ra > 180 or max(v[0] for v in vertices) < 300 else ra + 360 for ra, _ in vertices]
    ra_lo, ra_hi = min(ras) - 1, max(ras) + 1
    dec_lo, dec_hi = min(d for _, d in vertices) - 1, max(d for _, d in vertices) + 1
    checked = inside_count = 0
    for i in range(41):
        for j in range(41):
            ra = (ra_lo + (ra_hi - ra_lo) * i / 40) % 360.0
            dec = dec_lo + (dec_hi - dec_lo) * j / 40
            sides = [_side(a, b, _unit(ra, dec)) * turn for a, b in edges]
            if min(abs(s) for s in sides) < 1e-6:
                continue  # too close to an edge to call
            expected = all(s > 0 for s in sides)
            assert poly.contains(ra, dec) is expected, (ra, dec)
            checked += 1
            inside_count += expected
    assert checked > 1000 and inside_count > 50


def test_polygon_rejects_points_on_the_far_side_of_the_sky() -> None:
    poly = Polygon(((359.0, -1.0), (1.0, -1.0), (0.0, 1.0)))
    assert poly.contains(0.0, 0.0) is True
    assert poly.contains(180.0, 0.0) is False  # projects onto the centre from behind
    assert poly.contains(5.0, 0.0) is False


def test_polygon_bounding_cone_is_centred_on_the_vertices_and_just_covers_them() -> None:
    poly = Polygon(((350.0, 10.0), (352.0, 10.0), (351.0, 16.0)))
    cone = poly.bounding_cone()
    assert cone.ra_deg == pytest.approx(351.0)
    assert cone.dec_deg == pytest.approx(12.0)
    far = max(
        math.degrees(math.acos(sum(a * b for a, b in zip(_unit(351.0, 12.0), _unit(*v), strict=True))))
        for v in poly.vertices
    )
    assert cone.radius_deg == pytest.approx(far * 1.0001, rel=1e-9)
    tiny = Polygon(((20.0, 0.0), (20.000001, 0.0), (20.0, 0.000001))).bounding_cone()
    assert tiny.radius_deg < 1e-5


def test_polygon_vertices_and_width_are_bounded() -> None:
    Polygon(((0.0, 0.0), (1.0, 0.0), (0.5, 1.0)))  # ra 0 is on the sky
    Polygon(((0.0, 89.0), (120.0, 89.0), (240.0, 90.0)))  # the pole is on the sky
    Polygon(((0.0, -89.0), (120.0, -89.0), (240.0, -90.0)))
    with violation("a polygon needs at least three vertices"):
        Polygon(((0.0, 0.0), (1.0, 0.0)))
    for ra, dec in ((360.0, 0.0), (360.5, 0.0), (-0.5, 0.0), (1.0, 90.5), (1.0, -90.5), (1.0, 95.0)):
        with violation(f"polygon vertex out of range: ({ra}, {dec})"):
            Polygon(((ra, dec), (1.5, 0.0), (1.0, 1.0)))
    Polygon(((0.0, 0.0), (19.9, 0.0), (10.0, 0.5)))  # a radius just under 10 degrees
    with violation("polygon wider than a 10 degree cone; fields must stay bounded"):
        Polygon(((0.0, 0.0), (20.5, 0.0), (10.0, 0.5)))


def test_region_text_in_every_form_and_every_refusal() -> None:
    assert parse_region("circle(370.5d, -10d, 0.5d)") == Cone(10.5, -10.0, 0.5)
    assert parse_region("FK5; CIRCLE(1,2,0.25)") == Cone(1.0, 2.0, 0.25)
    box = parse_region("box(100, 60, 2, 1)")
    assert isinstance(box, Box)
    half = 1.0 / math.cos(math.radians(60))
    assert (box.ra_min_deg, box.ra_max_deg, box.dec_min_deg, box.dec_max_deg) == pytest.approx(
        (100 - half, 100 + half, 59.5, 60.5)
    )
    polar = parse_region("box(100, 90, 0.000002, 1)")
    assert isinstance(polar, Box) and polar.ra_max_deg - polar.ra_min_deg == pytest.approx(2.0, rel=1e-6)
    poly = parse_region("polygon(361,0,362,0,361.5,1)")
    assert isinstance(poly, Polygon) and poly.vertices == ((1.0, 0.0), (2.0, 0.0), (1.5, 1.0))
    with violation("not a region: 'ellipse(1,2,3)'; expected e.g. icrs;circle(120.0,-10.0,0.5)"):
        parse_region("ellipse(1,2,3)")
    with violation("region values must be numbers in degrees: 'circle(1,two,3)'"):
        parse_region("circle(1,two,3)")
    with violation("circle takes ra, dec, radius"):
        parse_region("circle(1,2)")
    with violation("box takes ra, dec, width, height"):
        parse_region("box(1,2,3)")
    for text in ("polygon(1,0,2,0)", "polygon(1,0,2,0,1.5,1,3)"):
        with violation("polygon takes at least three ra, dec pairs"):
            parse_region(text)


def test_a_polygon_field_is_centred_on_its_bounding_cone() -> None:
    poly = Polygon(((10.0, 0.0), (12.0, 0.0), (11.0, 2.0)))
    f = Field(poly)
    query = f.query_region(margin_deg=0.5)
    cone = poly.bounding_cone()
    assert query == Cone(cone.ra_deg, cone.dec_deg, cone.radius_deg + 0.5)
    used = EvidenceUsed(("gaia",), ("gaia:DR3",), poly.to_document())
    assert used.covers("region", "polygon(10.9,0.9,11.1,0.9,11.0,1.1)")


# -- Field and profile documents ------------------------------------------------------------------


def test_field_document_carries_its_images_and_defaults() -> None:
    images = (FieldImage(A, wcs_id=B, band="J"), FieldImage(C))
    assert images[0].to_document() == {"image_id": A.value, "wcs_id": B.value, "band": "J"}
    assert images[1].to_document() == {"image_id": C.value, "wcs_id": None, "band": None}
    f = Field(Cone(10.0, 20.0, 0.5), epoch_jyear=1950.5, images=images, label="plates")
    assert Field.from_document(f.to_document()) == f
    assert Field.from_document({"region": Cone(10.0, 20.0, 0.5).to_document()}) == Field(Cone(10.0, 20.0, 0.5))
    assert Field.from_document({"region": Cone(1.0, 2.0, 0.1).to_document()}).label == ""


def test_profile_archive_lookup() -> None:
    profile = synthetic_profile()
    assert profile.archive("fake-plates").release_label == "dasch:DR7"
    assert profile.archive("synthetic-2mass").role is ArchiveRole.CATALOGUE
    with pytest.raises(KeyError, match="'nowhere'"):
        profile.archive("nowhere")


# -- Identify and retrieve documents --------------------------------------------------------------


def test_resolver_and_object_set_documents() -> None:
    bare = ResolverRecord.from_document({"main_id": "* alf Cen", "object_type": "Star"})
    assert bare == ResolverRecord("* alf Cen", "Star")
    obj = IdentifiedObject("gaia:DR3:1", IdentificationMethod.CATALOGUE, 1.0, 2.0, (A,), gaia_source_key="gaia:DR3:1")
    objects = ObjectSet("field-1", (obj,))
    assert ObjectSet.from_document(objects.to_document()) == objects


def test_observation_criteria_bounds_and_document() -> None:
    criteria = ObservationCriteria(("HST",), ("image",), 3600.0, 50000.0, 51000.0)
    assert criteria.to_document() == {
        "missions": ["HST"],
        "product_types": ["image"],
        "radius_arcsec": 3600.0,
        "epoch_min_mjd": 50000.0,
        "epoch_max_mjd": 51000.0,
    }
    ObservationCriteria(radius_arcsec=0.5)
    for radius in (0.0, 3600.5):
        with violation("an observation search radius stays within a degree"):
            ObservationCriteria(radius_arcsec=radius)


def _record(epoch: float | None = 50000.0) -> ObservationRecord:
    return ObservationRecord(
        ProductRef("mast", "p1", "https://mast/p1", "s3://mast/p1"),
        "HST",
        "WFC3",
        "image",
        epoch,
        1024,
        "public",
        "cite HST",
        epoch_end_mjd=50001.0,
    )


def test_observation_index_document_and_defaults() -> None:
    index = ObservationIndex("mast", "mast:caom", "SELECT 1", "M31", (_record(),), "CC-BY", "cite MAST")
    assert ObservationIndex.from_document(index.to_document()) == index
    bare = ObservationIndex.from_document(
        {"archive": "mast", "release_label": "mast:caom", "query_text": "SELECT 1", "target": "M31"}
    )
    assert bare == ObservationIndex("mast", "mast:caom", "SELECT 1", "M31")
    assert (bare.records, bare.licence, bare.citation) == ((), "", "")


def test_a_product_is_read_from_one_of_its_uris() -> None:
    ref = ProductRef("mast", "p1", "https://mast/p1", "s3://mast/p1")
    for uri in (ref.uri, ref.cloud_uri):
        assert FetchedProduct(ref, b"x", "image/fits", "mast:caom", "public", "cite", str(uri)).read_from == uri
    with violation("a product is read from its archive URI or its cloud URI"):
        FetchedProduct(ref, b"x", "image/fits", "mast:caom", "public", "cite", "https://elsewhere")


def test_product_evidence_keeps_its_raw_and_envelope_artifacts() -> None:
    ref = ProductRef("mast", "p1", "https://mast/p1")
    full = ProductEvidence(ref, "image", A, ref.uri, raw_artifact_id=B, envelope_artifact_id=C)
    doc = full.to_document()
    assert doc["raw_artifact_id"] == B.value and doc["envelope_artifact_id"] == C.value
    assert ProductEvidence.from_document(doc) == full
    plain = ProductEvidence(ref, "image", A, ref.uri)
    assert set(plain.to_document()) == {"product", "product_type", "artifact_id", "read_from"}
    assert ProductEvidence.from_document(plain.to_document()) == plain


# -- Evidence used and promotion ------------------------------------------------------------------


def test_evidence_used_epoch_bounds_are_inclusive_and_need_both_ends() -> None:
    used = EvidenceUsed(("gaia",), ("gaia:DR3",), Cone(1.0, 1.0, 0.1).to_document(), 1890.0, 2016.0)
    assert used.covers("epoch", "1890.0") and used.covers("epoch", "2016.0")
    assert not used.covers("epoch", "1889.9") and not used.covers("epoch", "2016.1")
    for low, high in ((None, 2016.0), (1890.0, None), (None, None)):
        assert not replace(used, epoch_min_jyear=low, epoch_max_jyear=high).covers("epoch", "2000.0")
    with violation("unknown evidence kind 'colour'; expected one of ('archive', 'release', 'epoch', 'region')"):
        used.covers("colour", "red")
    bare = EvidenceUsed.from_document({"region": Cone(1.0, 1.0, 0.1).to_document()})
    assert bare == EvidenceUsed((), (), Cone(1.0, 1.0, 0.1).to_document())


def _frozen(parameters: dict[str, Any]) -> Any:
    return Predeclaration(
        predeclaration_id="pre-confirm",
        protocol_id="uc01-stellar",
        protocol_version="1.0.0",
        title="Confirm the candidate",
        hypotheses=(Hypothesis("H-1", 1, "the group is co-moving"),),
        gates=(Gate("STL-G-01", "membership", "membership", Comparator.GE, 0.5),),
        parameters=parameters,
    ).freeze()


def test_confirmatory_evidence_keeps_only_well_formed_items() -> None:
    assert confirmatory_evidence(_frozen({})) == ()
    assert confirmatory_evidence(_frozen({"confirmatory_evidence": "archive=dasch"})) == ()
    items = [
        {"kind": "archive", "value": "dasch"},
        {"kind": "colour", "value": "red"},
        {"kind": "release"},
        "epoch=2030",
        {"kind": "epoch", "value": 2030.5},
    ]
    assert confirmatory_evidence(_frozen({"confirmatory_evidence": items})) == (
        ("archive", "dasch"),
        ("epoch", "2030.5"),
    )


def _investigation() -> Investigation:
    r = TestResult("pm-excess", "1.0.0", Subject.of_object("gaia:DR3:1"), 7.0, seed=3, p_value=0.001)
    return Investigation.open(
        investigation_id="inv-1",
        set_id="invset-1",
        field_id="field-1",
        candidate=RankedCandidate(r.subject, 1.0, (r,)),
        evidence_bundle_ids=("bundle-1",),
        policy=RankingPolicy("significance", "1.0.0", 0.5),
        evidence_used=EvidenceUsed(("gaia",), ("gaia:DR3",), Cone(120.0, -10.0, 0.5).to_document(), 1890.0, 2016.0),
        at=AT,
        actor="leigh",
    )


def test_every_triage_step_records_its_transition_and_event() -> None:
    inv = _investigation()
    inv.pull_events()
    inv.park("waiting for DR4", "leigh", LATER)
    inv.reopen("DR4 landed", "sam", LATER)
    inv.annotate("looks like a binary", "sam", LATER)
    inv.close("image artefact", "leigh", LATER)
    assert [t.to_document() for t in inv.transitions] == [
        {"from": None, "to": "open", "reason": "opened by the run", "actor": "leigh", "at": AT.isoformat()},
        {"from": "open", "to": "parked", "reason": "waiting for DR4", "actor": "leigh", "at": LATER.isoformat()},
        {"from": "parked", "to": "open", "reason": "DR4 landed", "actor": "sam", "at": LATER.isoformat()},
        {"from": "open", "to": "closed", "reason": "image artefact", "actor": "leigh", "at": LATER.isoformat()},
    ]
    assert [n.to_document() for n in inv.notes] == [
        {"text": "looks like a binary", "author": "sam", "at": LATER.isoformat()}
    ]
    assert [e.to_document() for e in inv.pull_events()] == [
        {
            "name": "InvestigationParked",
            "aggregate_id": "inv-1",
            "occurred_at": LATER.isoformat(),
            "payload": {"reason": "waiting for DR4", "actor": "leigh"},
        },
        {
            "name": "InvestigationReopened",
            "aggregate_id": "inv-1",
            "occurred_at": LATER.isoformat(),
            "payload": {"reason": "DR4 landed", "actor": "sam"},
        },
        {
            "name": "InvestigationAnnotated",
            "aggregate_id": "inv-1",
            "occurred_at": LATER.isoformat(),
            "payload": {"author": "sam"},
        },
        {
            "name": "InvestigationClosed",
            "aggregate_id": "inv-1",
            "occurred_at": LATER.isoformat(),
            "payload": {"reason": "image artefact", "actor": "leigh"},
        },
    ]


def test_triage_refusals_say_why() -> None:
    inv = _investigation()
    with refused("investigation inv-1 cannot move from open to open"):
        inv.reopen("again", "leigh", AT)
    with violation("every triage transition records its reason"):
        inv.park(" ", "leigh", AT)
    with violation("every triage transition records its actor"):
        inv.park("later", " ", AT)
    with violation("an empty note says nothing"):
        inv.annotate("  ", "leigh", AT)
    inv.close("done", "leigh", AT)
    with refused("investigation inv-1 is closed and read-only"):
        inv.annotate("too late", "leigh", AT)
    with refused("investigation inv-1 is closed; it cannot be promoted"):
        inv.promote(_frozen({"confirmatory_evidence": [{"kind": "archive", "value": "dasch"}]}), actor="l", at=AT)
    assert inv.state is InvestigationState.CLOSED and len(inv.transitions) == 2


def test_promotion_records_its_reason_links_and_event() -> None:
    inv = _investigation()
    inv.pull_events()
    evidence = [
        {"kind": "archive", "value": "dasch"},
        {"kind": "archive", "value": "gaia"},
        {"kind": "epoch", "value": "2030"},
    ]
    frozen = _frozen({"confirmatory_evidence": evidence})
    inv.promote(frozen, actor="sam", at=LATER)
    assert inv.transitions[-1].reason == "confirmatory predeclaration frozen: archive=dasch, epoch=2030"
    assert inv.transitions[-1].from_state is InvestigationState.OPEN
    assert inv.links == {"predeclaration_hash": frozen.hash}
    [event] = inv.pull_events()
    assert event.to_document() == {
        "name": "InvestigationPromoted",
        "aggregate_id": "inv-1",
        "occurred_at": LATER.isoformat(),
        "payload": {
            "predeclaration_hash": frozen.hash,
            "experiment_id": None,
            "unused_evidence": [{"kind": "archive", "value": "dasch"}, {"kind": "epoch", "value": "2030"}],
            "actor": "sam",
        },
    }


def test_investigation_document_defaults() -> None:
    inv = _investigation()
    doc = inv.to_document()
    for key in ("transitions", "notes", "links", "label"):
        del doc[key]
    bare = Investigation.from_document(doc)
    assert (bare.transitions, bare.notes, bare.links, bare.label) == ([], [], {}, EXPLORATORY)
    inv.annotate("a note", "leigh", AT)
    inv.link("ticket", "SDP-1")
    assert Investigation.from_document(inv.to_document()) == inv


# -- The set ----------------------------------------------------------------------------------------


def _set() -> InvestigationSet:
    return InvestigationSet("invset-1", Field(Cone(1.0, 1.0, 0.1)), synthetic_profile(), 7, AT)


def test_a_failed_stage_records_its_error_and_event() -> None:
    s = _set()
    s.fail_stage(Stage.IDENTIFY, "archive down", LATER)
    assert s.stages[Stage.IDENTIFY] == StageRecord(Stage.IDENTIFY, StageState.FAILED, None, LATER, "archive down")
    assert [e.to_document() for e in s.pull_events()] == [
        {
            "name": "InvestigationStageFailed",
            "aggregate_id": "invset-1",
            "occurred_at": LATER.isoformat(),
            "payload": {"stage": "identify", "error": "archive down"},
        }
    ]


def test_stage_record_document_defaults() -> None:
    record = StageRecord.from_document({"stage": "test", "state": "pending"})
    assert record == StageRecord(Stage.TEST)
    assert record.summary == {}


def test_set_document_round_trips_every_field() -> None:
    s = _set()
    s.complete_stage(Stage.IDENTIFY, A, LATER, objects=3)
    full = replace(
        s,
        objects_examined=("gaia:DR3:1", "gaia:DR3:2"),
        tests_applied=({"name": "pm-excess", "version": "1.0.0"},),
        archive_releases=("gaia:DR3", "twomass:psc"),
        null_results=(NullFieldResult("pm-excess", "1.0.0", 10, 200, 4, 0.01, "shuffle"),),
        investigation_ids=("inv-1",),
        manifest_id=B,
        plan_artifact_id="plan-1",
    )
    assert InvestigationSet.from_document(full.to_document()) == full


def test_set_document_defaults() -> None:
    s = _set()
    doc = s.to_document()
    for key in (
        "stages",
        "objects_examined",
        "tests_applied",
        "archive_releases",
        "null_results",
        "investigation_ids",
        "manifest_id",
        "label",
        "plan_artifact_id",
    ):
        del doc[key]
    bare = InvestigationSet.from_document(doc)
    assert bare == s
    assert bare.stages == {stage: StageRecord(stage) for stage in Stage}
    assert bare.label == EXPLORATORY and bare.manifest_id is None


def test_quality_cuts_survive_the_profile_document() -> None:
    profile = synthetic_profile()
    cut = replace(profile, identify=replace(profile.identify, quality_cuts=(Filter("ruwe", "<", 1.4),)))
    assert type(cut).from_document(cut.to_document()) == cut


def test_archive_selection_document_defaults() -> None:
    bare = ArchiveSelection.from_document({"source_id": "mast", "role": "observations", "release_label": "mast:caom"})
    assert bare == ArchiveSelection("mast", ArchiveRole.OBSERVATIONS, "mast:caom")
