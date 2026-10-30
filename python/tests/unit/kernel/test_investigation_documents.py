"""T1: more of the Investigate records as stored. Every field survives its document, every default
is the documented one, every bound is checked on both sides, and every refusal says exactly why.
These pin what the mutation run on sdp.kernel.domain.investigation showed nothing else checked
(docs/testing.md, "Killing a survivor")."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np
import pytest
from tests.unit.kernel.test_investigation_records import AT, A, B, C, _investigation, violation

from sdp.kernel.domain.archive import Box, Cone, Filter
from sdp.kernel.domain.investigation import (
    EXPLORATORY,
    SCHEMA_VERSION,
    ArchiveRole,
    ArchiveSelection,
    EvidenceBundle,
    EvidenceUsed,
    Field,
    IdentificationMethod,
    IdentifiedObject,
    IdentifyRoute,
    IdentifySpec,
    Investigation,
    NullFieldPlan,
    NullFieldResult,
    ObjectSet,
    ObservationCriteria,
    ObservationIndex,
    Polygon,
    ProductEvidence,
    ProductRef,
    RankedCandidate,
    RankingPolicy,
    ResolverRecord,
    Subject,
    TestProfile,
    TestResult,
    TestSelection,
    _jsonable,
    _tangent,
    check_calibration_level,
    confirmatory_evidence,
    parse_region,
    region_contains,
)
from sdp.testing.investigate import synthetic_profile

pytestmark = pytest.mark.tier("T1")


# -- Small helpers ----------------------------------------------------------------------------------


def test_jsonable_turns_numpy_scalars_and_non_finite_floats_into_plain_json() -> None:
    assert _jsonable({"a": np.float64(1.5), "b": [np.int64(2), (math.inf, -math.inf, math.nan)], 3: 0.25}) == {
        "a": 1.5,
        "b": [2, [None, None, None]],
        "3": 0.25,
    }
    assert type(_jsonable(np.int64(2))) is int
    assert _jsonable("text") == "text"

    class Item:
        item = "not callable"

    thing = Item()
    assert _jsonable(thing) is thing, "only a callable item() is a numpy scalar"


def test_slugs_and_finite_values_say_what_they_check() -> None:
    with violation("test name must be a lowercase slug: 'Bad Name'"):
        TestSelection("Bad Name", "1.0.0")
    with violation("ranking policy id must be a lowercase slug: 'Sig'"):
        RankingPolicy("Sig", "1.0.0", 0.5)
    with violation("profile id must be a lowercase slug: 'P'"):
        replace(synthetic_profile(), profile_id="P")
    subject = Subject.of_object("o")
    for name, kwargs in (
        ("statistic", {}),
        ("uncertainty", {"uncertainty": math.nan}),
        ("effect size", {"effect_size": math.inf}),
    ):
        statistic = math.nan if name == "statistic" else 1.0
        with violation(f"{name} must be finite"):
            TestResult("t", "1.0.0", subject, statistic, 1, **kwargs)  # type: ignore[arg-type]


# -- Regions ----------------------------------------------------------------------------------------


def test_the_tangent_plane_projection() -> None:
    assert _tangent(10.0, 20.0, 10.0, 20.0) == pytest.approx((0.0, 0.0), abs=1e-12)
    x, y = _tangent(11.0, 21.0, 10.0, 20.0)
    r, d, r0, d0 = map(math.radians, (11.0, 21.0, 10.0, 20.0))
    cos_c = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(r - r0)
    assert x == pytest.approx(math.degrees(math.cos(d) * math.sin(r - r0) / cos_c), rel=1e-12)
    assert y == pytest.approx(
        math.degrees((math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(r - r0)) / cos_c), rel=1e-12
    )
    assert x == pytest.approx(0.9338, abs=1e-4) and y == pytest.approx(1.0030, abs=1e-4)  # about cos(21) and 1 degree
    # well away from ra0 = 0 the sign and size of ra - ra0 both matter
    assert _tangent(201.0, 40.0, 200.0, 40.0)[0] == pytest.approx(0.766, abs=1e-3)
    assert _tangent(199.0, 40.0, 200.0, 40.0)[0] == pytest.approx(-0.766, abs=1e-3)


def test_cone_and_box_edges_are_inside() -> None:
    cone = Cone(0.0, 0.0, 1.0)
    assert region_contains(cone, 1.0, 0.0) and not region_contains(cone, 1.0001, 0.0)
    box = Box(10.0, 12.0, -1.0, 1.0)
    for ra, dec in ((10.0, 0.0), (12.0, 0.0), (11.0, -1.0), (11.0, 1.0)):
        assert region_contains(box, ra, dec), (ra, dec)
    assert not region_contains(box, 13.0, 0.0) and not region_contains(box, 11.0, 2.0)


def test_polygon_edges_and_the_far_hemisphere() -> None:
    square = Polygon(((10.0, -1.0), (12.0, -1.0), (12.0, 1.0), (10.0, 1.0)))
    assert square.contains(11.0, 0.0)
    assert not square.contains(11.0, 1.5) and not square.contains(9.5, 0.0)
    # a point exactly at a vertex's height counts once, so the tip of a triangle is outside
    triangle = Polygon(((10.0, 0.0), (12.0, 0.0), (11.0, 1.0)))
    assert not triangle.contains(9.0, 0.0) and not triangle.contains(13.0, 0.0)
    assert triangle.contains(11.0, 0.3)
    centre = square.bounding_cone()
    antipode = ((centre.ra_deg + 180.0) % 360.0, -centre.dec_deg)
    assert not square.contains(*antipode)
    assert not square.contains((centre.ra_deg + 90.0) % 360.0, 0.0), "90 degrees away is already outside"


def test_a_polygon_just_under_ten_degrees_is_allowed() -> None:
    wide = Polygon(((0.0, -6.5), (13.0, -6.5), (13.0, 6.5), (0.0, 6.5)))
    assert 9.0 < wide.bounding_cone().radius_deg < 10.0


def test_region_text_accepts_a_degree_suffix() -> None:
    assert parse_region("icrs;circle(120d,-10d,0.5d)") == Cone(120.0, -10.0, 0.5)


def test_a_field_epoch_is_a_plausible_julian_year_inclusive() -> None:
    for year in (1800.0, 2200.0):
        assert Field(Cone(1.0, 1.0, 0.1), epoch_jyear=year).epoch_jyear == year
    for year in (1799.9, 2200.1):
        with violation(f"field epoch {year} is not a plausible Julian year"):
            Field(Cone(1.0, 1.0, 0.1), epoch_jyear=year)


def test_a_box_field_is_queried_with_its_margin_on_every_side() -> None:
    box = Box(10.0, 12.0, -1.0, 1.0)
    field = Field(box)
    assert field.query_region() is box
    assert field.query_region(0.5) == Box(9.5, 12.5, -1.5, 1.5)
    assert Field(Cone(1.0, 1.0, 9.8)).query_region(0.5) == Cone(1.0, 1.0, 10.0), "a query cone stops at 10 degrees"
    assert Field(Cone(1.0, 1.0, 0.1)).query_region(0.5) == Cone(1.0, 1.0, 0.6)


# -- The profile ----------------------------------------------------------------------------------


def _identify(**kwargs: Any) -> IdentifySpec:
    return IdentifySpec(IdentifyRoute.IMAGE, "gaia", "gaia:DR3", ("ra", "dec"), **kwargs)


def test_identify_refusals_and_its_radius_bounds() -> None:
    with violation("Identify names the catalogue columns it reads"):
        IdentifySpec(IdentifyRoute.CATALOGUE, "gaia", "gaia:DR3", ())
    assert _identify(match_radius_arcsec=60.0).match_radius_arcsec == 60.0
    for radius in (0.0, 60.5):
        with violation("match radius must be in (0, 60] arcsec"):
            _identify(match_radius_arcsec=radius)


def test_identify_document_round_trips_and_has_documented_defaults() -> None:
    full = _identify(
        quality_cuts=(Filter("ruwe", "<", 1.4),),
        match_radius_arcsec=2.5,
        row_cap=500,
        resolve=False,
        resolver_radius_arcsec=4.0,
    )
    doc = full.to_document()
    assert doc == {
        "route": "image",
        "source_id": "gaia",
        "release_label": "gaia:DR3",
        "columns": ["ra", "dec"],
        "quality_cuts": [Filter("ruwe", "<", 1.4).to_document()],
        "match_radius_arcsec": 2.5,
        "row_cap": 500,
        "resolve": False,
        "resolver_radius_arcsec": 4.0,
    }
    assert IdentifySpec.from_document(doc) == full
    bare = IdentifySpec.from_document({"source_id": "gaia", "release_label": "gaia:DR3", "columns": ["ra"]})
    assert bare == IdentifySpec(IdentifyRoute.CATALOGUE, "gaia", "gaia:DR3", ("ra",))
    assert (bare.match_radius_arcsec, bare.row_cap, bare.resolve, bare.resolver_radius_arcsec) == (
        1.0,
        100_000,
        True,
        2.0,
    )
    assert bare.quality_cuts == ()


def test_calibration_levels_are_zero_to_four() -> None:
    for level in (None, 0, 4):
        check_calibration_level(level)
    for bad in (-1, 5, True, 2.0):
        with violation(f"an ObsCore calibration level is a whole number 0 to 4, not {bad!r}"):
            check_calibration_level(bad)
    with violation("an ObsCore calibration level is a whole number 0 to 4, not 9"):
        ObservationCriteria(min_calibration_level=9)


def test_archive_selection_refusals_and_cutout_bounds() -> None:
    with violation("catalogue archive gaia must name its columns"):
        ArchiveSelection("gaia", ArchiveRole.CATALOGUE, "gaia:DR3")
    mast = ArchiveSelection("mast", ArchiveRole.OBSERVATIONS, "mast:caom", cutout_arcsec=0.5)
    assert mast.cutout_arcsec == 0.5
    assert replace(mast, cutout_arcsec=3600.0).cutout_arcsec == 3600.0
    for side in (0.0, 3600.5):
        with violation("a cutout side is more than 0 and at most 3600 arcsec"):
            replace(mast, cutout_arcsec=side)


def test_observation_criteria_keep_their_calibration_level() -> None:
    assert ObservationCriteria(min_calibration_level=3).to_document()["min_calibration_level"] == 3
    assert "min_calibration_level" not in ObservationCriteria().to_document()


def test_a_test_selection_without_thresholds() -> None:
    bare = TestSelection.from_document({"name": "pm-excess", "version": "1.0.0"})
    assert bare == TestSelection("pm-excess", "1.0.0") and dict(bare.thresholds) == {} and bare.needs_products == ()


def test_ranking_alpha_lies_strictly_between_zero_and_one() -> None:
    assert RankingPolicy("sig", "1.0.0", 0.5, alpha=0.999).alpha == 0.999
    for alpha in (0.0, 1.0):
        with violation("alpha must be in (0, 1)"):
            RankingPolicy("sig", "1.0.0", 0.5, alpha=alpha)
    bare = RankingPolicy.from_document({"policy_id": "sig", "version": "1.0.0", "threshold": 0.5})
    assert bare == RankingPolicy("sig", "1.0.0", 0.5) and bare.alpha == 0.01


def test_a_profile_runs_at_least_one_null_field() -> None:
    assert NullFieldPlan(1).trials == 1
    with violation("every profile runs at least one null field (Investigate §2)"):
        NullFieldPlan(0)


def test_profile_refusals_say_why() -> None:
    profile = synthetic_profile()
    with violation("a profile runs at least one test"):
        replace(profile, tests=())
    with violation("a profile selects each test once"):
        replace(profile, tests=(profile.tests[0], profile.tests[0]))
    archive = ArchiveSelection("mast", ArchiveRole.OBSERVATIONS, "mast:caom")
    with violation("a profile lists each archive once"):
        replace(profile, archives=(archive, archive))


def test_profile_document_schema_and_defaults() -> None:
    profile = replace(synthetic_profile(), description="described", null_fields=NullFieldPlan(7))
    doc = profile.to_document()
    assert doc["schema_version"] == SCHEMA_VERSION == "1.0.0"
    assert TestProfile.from_document(doc) == profile
    for key in ("description", "archives", "null_fields"):
        del doc[key]
    bare = TestProfile.from_document(doc)
    assert (bare.description, bare.archives, bare.null_fields) == ("", (), NullFieldPlan(20))
    doc["null_fields"] = {}
    assert TestProfile.from_document(doc).null_fields == NullFieldPlan(20)


# -- Identify ---------------------------------------------------------------------------------------


def _detection(**kwargs: Any) -> IdentifiedObject:
    base: dict[str, Any] = {
        "object_key": "det-1",
        "method": IdentificationMethod.DETECTION,
        "ra_deg": 10.0,
        "dec_deg": 20.0,
        "source_artifacts": (A,),
        "gaia_source_key": "gaia:DR3:1",
        "match_radius_arcsec": 1.0,
        "match_distance_arcsec": 0.5,
    }
    return IdentifiedObject(**{**base, **kwargs})


def test_identified_object_refusals_and_the_match_radius_is_inclusive() -> None:
    with violation("an identified object needs a key"):
        _detection(object_key="")
    with violation("det-1: a detection records its declared match radius"):
        _detection(match_radius_arcsec=None)
    assert _detection(match_distance_arcsec=1.0).match_distance_arcsec == 1.0
    with violation("det-1: a match lies within its declared radius"):
        _detection(match_distance_arcsec=1.01)
    with violation("det-1: a match lies within its declared radius"):
        _detection(match_distance_arcsec=None)


def test_identified_object_document_round_trips_every_field_and_defaults() -> None:
    full = _detection(
        pixel_xy=(12.5, 7.25),
        wcs_artifact=B,
        image_artifact=C,
        resolver_record=ResolverRecord("M 92", "GlC", ("2020A&A...1A",), 259.28, 43.14, 0.3),
        flags=("bright", "edge"),
    )
    doc = full.to_document()
    assert doc["pixel_xy"] == [12.5, 7.25] and doc["image_artifact"] == C.value
    assert IdentifiedObject.from_document(doc) == full
    bare = _detection()
    doc = bare.to_document()
    assert doc["pixel_xy"] is None and doc["image_artifact"] is None
    del doc["flags"]
    assert IdentifiedObject.from_document(doc) == bare


def test_object_keys_are_unique_within_a_set() -> None:
    with violation("object keys are unique within a set"):
        ObjectSet("field-1", (_detection(), _detection()))


# -- Retrieve ---------------------------------------------------------------------------------------


def test_evidence_bundle_refusals_and_document_defaults() -> None:
    obj = _detection()
    index = ObservationIndex("mast", "mast:caom", "q", "det-1")
    with violation("every observation index is registered as a DatasetArtifact (INV-005)"):
        EvidenceBundle(obj, {}, {}, observation_indices=(index,))
    product = ProductEvidence(ProductRef("mast", "p1", "https://x/p1"), "image", C, "https://x/p1")
    full = EvidenceBundle(
        obj,
        {"gaia": ({"ra": 10.0},)},
        {"gaia": A},
        observation_indices=(index,),
        observation_artifacts=(B,),
        products=(product,),
        releases=("gaia:DR3", "mast:caom"),
    )
    assert EvidenceBundle.from_document(full.to_document()) == full
    bare = EvidenceBundle.from_document({"object": obj.to_document()})
    assert bare == EvidenceBundle(obj, {}, {})
    assert (bare.observation_indices, bare.observation_artifacts, bare.products, bare.releases) == ((), (), (), ())


# -- Test -------------------------------------------------------------------------------------------


def test_subject_refusals_and_bounds() -> None:
    with violation("a subject is either one object or one structure"):
        Subject()
    with violation("a structure lists at least two members"):
        Subject(structure_id="s", members=("a",))
    with violation("one membership probability per member"):
        Subject(structure_id="s", members=("a", "b"), membership=(0.5,))
    assert Subject(structure_id="s", members=("a", "b"), membership=(0.0, 1.0)).membership == (0.0, 1.0)
    for p in (-0.1, 1.1):
        with violation("membership probabilities lie in [0, 1]"):
            Subject(structure_id="s", members=("a", "b"), membership=(0.5, p))
    with violation("an object subject has no members"):
        Subject(object_key="o", members=("a", "b"))


def test_subject_document_round_trips_and_defaults() -> None:
    structure = Subject(structure_id="s", members=("a", "b"), membership=(0.25, 0.75))
    assert structure.to_document()["membership"] == [0.25, 0.75]
    assert Subject.from_document(structure.to_document()) == structure
    assert Subject.from_document({"object_key": "o"}) == Subject.of_object("o")


def test_result_refusals_and_p_value_bounds() -> None:
    subject = Subject.of_object("o")
    with violation("investigation results are EXPLORATORY and never carry a verdict (INV-008)"):
        TestResult("t", "1.0.0", subject, 1.0, 1, label="CONFIRMED")
    for p in (0.0, 1.0):
        assert TestResult("t", "1.0.0", subject, 1.0, 1, p_value=p).p_value == p
    for p in (-0.01, 1.01):
        with violation("a p-value lies in [0, 1]"):
            TestResult("t", "1.0.0", subject, 1.0, 1, p_value=p)


def test_result_document_round_trips_every_field_and_defaults() -> None:
    full = TestResult(
        "t",
        "1.0.0",
        Subject.of_object("o"),
        1.5,
        9,
        uncertainty=0.25,
        p_value=0.01,
        effect_size=2.0,
        posterior={"mean": 0.5},
        details={"n": 3},
    )
    doc = full.to_document()
    assert (doc["uncertainty"], doc["effect_size"], doc["posterior"], doc["label"]) == (
        0.25,
        2.0,
        {"mean": 0.5},
        EXPLORATORY,
    )
    assert TestResult.from_document(doc) == full
    bare = TestResult.from_document(
        {"test_name": "t", "test_version": "1.0.0", "subject": {"object_key": "o"}, "statistic": 1.5, "seed": 9}
    )
    assert bare == TestResult("t", "1.0.0", Subject.of_object("o"), 1.5, 9)
    assert bare.to_document()["posterior"] is None and bare.label == EXPLORATORY and dict(bare.details) == {}


def test_null_field_counts_and_their_refusals() -> None:
    assert NullFieldResult("t", "1.0.0", 1, 0, 0, 0.01, "shuffle").false_positive_rate == 0.0
    for trials, examined, false in ((0, 5, 0), (1, -1, 0), (1, 5, -1)):
        with violation("null-field counts are non-negative and cover at least one trial"):
            NullFieldResult("t", "1.0.0", trials, examined, false, 0.01, "shuffle")
    with violation("false positives cannot exceed what was examined"):
        NullFieldResult("t", "1.0.0", 1, 2, 3, 0.01, "shuffle")
    assert NullFieldResult("t", "1.0.0", 2, 8, 2, 0.01, "shuffle").to_document()["false_positive_rate"] == 0.25


# -- The investigation ------------------------------------------------------------------------------


def test_opening_records_the_rank_and_the_event() -> None:
    r = TestResult("pm-excess", "1.0.0", Subject.of_object("gaia:DR3:1"), 7.0, seed=3, p_value=0.001)
    inv = Investigation.open(
        investigation_id="inv-1",
        set_id="invset-1",
        field_id="field-1",
        candidate=RankedCandidate(r.subject, 0.75, (r,)),
        evidence_bundle_ids=("bundle-1",),
        policy=RankingPolicy("significance", "2.1.0", 0.5),
        evidence_used=EvidenceUsed(("gaia",), ("gaia:DR3",), Cone(120.0, -10.0, 0.5).to_document(), 1890.0, 2016.0),
        at=AT,
    )
    assert inv.rank.to_document() == {"score": 0.75, "policy_id": "significance", "policy_version": "2.1.0"}
    assert inv.transitions[0].actor == "kernel"
    assert [e.to_document() for e in inv.pull_events()] == [
        {
            "name": "InvestigationOpened",
            "aggregate_id": "inv-1",
            "occurred_at": AT.isoformat(),
            "payload": {"set_id": "invset-1", "candidate": "gaia:DR3:1", "score": 0.75},
        }
    ]


def test_investigation_refusals_and_schema() -> None:
    inv = _investigation()
    with violation("an investigation is EXPLORATORY (INV-008)"):
        replace(inv, label="CONFIRMED")
    with violation("an investigation names the evidence bundles its tests read"):
        replace(inv, evidence_bundle_ids=())
    assert inv.to_document()["schema_version"] == SCHEMA_VERSION


def test_confirmatory_evidence_tolerates_a_missing_or_malformed_list() -> None:
    from tests.unit.kernel.test_investigation_records import _frozen

    assert confirmatory_evidence(_frozen({})) == ()
    assert confirmatory_evidence(_frozen({"confirmatory_evidence": "archive:dasch"})) == ()
