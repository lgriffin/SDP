"""T1: the association records in full: every document a record, association, policy and graph
writes, the refusals each gives, the review transitions' bookkeeping, and the exact geometry the
association science uses (v0.4.0 §4, INT-009 to INT-011).

The behaviour is in test_association.py and tests/association; these pin the details mutation
testing showed nothing else checked (docs/testing.md, "Killing a survivor")."""

from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Any

import pytest

from sdp.kernel.domain.association import (
    Association,
    AssociationGraph,
    AssociationPolicy,
    AssociationRecord,
    CatalogueSource,
    Detection,
    Motion,
    ObjectHypothesis,
    Photometry,
    PositionCovariance,
    ReviewState,
)
from sdp.kernel.domain.errors import InvalidTransition, InvariantViolation
from sdp.kernel.science.association import (
    candidate_options,
    field_density_per_arcsec2,
    offset_arcsec,
    propagate,
    separation_arcsec,
)
from sdp.testing.integration import association_policy

pytestmark = pytest.mark.tier("T1")

ARCSEC = 1 / 3600
COV = PositionCovariance(0.03, 0.04)


def exactly(error: type[Exception], message: str) -> Any:
    return pytest.raises(error, match="^" + re.escape(message) + "$")


def anchor(record_id: str, ra: float, dec: float, **changes: Any) -> CatalogueSource:
    return replace(CatalogueSource(record_id, "gaia", "DR3", record_id, ra, dec, COV, 2016.0), **changes)


def detection(record_id: str, ra: float, dec: float, **changes: Any) -> Detection:
    return replace(Detection(record_id, "p", "SCI", ra, dec, COV, 2016.0, 0.1, "NIRCAM"), **changes)


def association(**changes: Any) -> Association:
    return replace(Association("r", "h", "m", "1.0.0", 0.5, 2.0, 0.8, 4.0, ("h2",)), **changes)


# --- records ------------------------------------------------------------------------------------


def test_record_documents_carry_every_field() -> None:
    src = anchor(
        "g",
        10.0,
        -5.0,
        motion=Motion(1.0, 2.0, 3.0, 0.1, 0.2, 0.3),
        resolution_arcsec=0.5,
        photometry=(Photometry("G", 12.0, 0.01),),
    )
    assert src.kind == "catalogue_source"
    assert src.to_document() == {
        "kind": "catalogue_source",
        "record_id": "g",
        "catalogue": "gaia",
        "release_label": "DR3",
        "source_key": "g",
        "ra_deg": 10.0,
        "dec_deg": -5.0,
        "covariance": {"sigma_ra_arcsec": 0.03, "sigma_dec_arcsec": 0.04, "rho": 0.0},
        "epoch_jyear": 2016.0,
        "motion": {
            "pmra_mas_yr": 1.0,
            "pmdec_mas_yr": 2.0,
            "parallax_mas": 3.0,
            "sigma_pmra_mas_yr": 0.1,
            "sigma_pmdec_mas_yr": 0.2,
            "sigma_parallax_mas": 0.3,
        },
        "resolution_arcsec": 0.5,
        "photometry": [{"band": "G", "magnitude": 12.0, "sigma": 0.01}],
    }
    assert anchor("g", 10.0, -5.0).to_document()["motion"] is None
    det = detection("d", 10.0, -5.0, photometry=(Photometry("F200W", 20.0, 0.1),))
    assert det.kind == "detection"
    assert det.to_document() == {
        "kind": "detection",
        "record_id": "d",
        "product_id": "p",
        "extension": "SCI",
        "ra_deg": 10.0,
        "dec_deg": -5.0,
        "covariance": {"sigma_ra_arcsec": 0.03, "sigma_dec_arcsec": 0.04, "rho": 0.0},
        "epoch_jyear": 2016.0,
        "resolution_arcsec": 0.1,
        "instrument": "NIRCAM",
        "photometry": [{"band": "F200W", "magnitude": 20.0, "sigma": 0.1}],
    }


def test_records_refuse_positions_and_resolutions_they_cannot_have() -> None:
    with exactly(InvariantViolation, "ra must be finite"):
        detection("d", math.nan, 0.0)
    with exactly(InvariantViolation, "dec must be finite"):
        detection("d", 0.0, math.inf)
    for ra, dec in ((360.0, 0.0), (-0.1, 0.0), (0.0, 90.1), (0.0, -90.1)):
        with exactly(InvariantViolation, "detection position out of range"):
            detection("d", ra, dec)
        with exactly(InvariantViolation, "catalogue position out of range"):
            anchor("a", ra, dec)
    assert detection("d", 0.0, 90.0).dec_deg == 90.0 and detection("d", 359.9, -90.0).ra_deg == 359.9
    assert anchor("a", 0.0, -90.0).dec_deg == -90.0 and anchor("a", 0.0, 90.0).dec_deg == 90.0
    for resolution in (0.0, math.inf):
        with exactly(InvariantViolation, "a detection carries its instrument's resolution"):
            detection("d", 0.0, 0.0, resolution_arcsec=resolution)
    for resolution in (-0.1, math.nan):
        with exactly(InvariantViolation, "a catalogue resolution is finite and not negative"):
            anchor("a", 0.0, 0.0, resolution_arcsec=resolution)


def test_a_covariance_is_positive_and_correlated_strictly_inside_the_unit_interval() -> None:
    c = PositionCovariance(2.0, 3.0, 0.5)
    assert c.matrix() == ((4.0, 3.0), (3.0, 9.0))
    assert c.to_document() == {"sigma_ra_arcsec": 2.0, "sigma_dec_arcsec": 3.0, "rho": 0.5}
    for sigmas in ((0.0, 1.0), (1.0, 0.0), (math.inf, 1.0), (1.0, math.nan)):
        with exactly(InvariantViolation, "positional sigmas are positive; a record without them cannot be associated"):
            PositionCovariance(*sigmas)
    for rho in (-1.0, 1.0):
        with exactly(InvariantViolation, "correlation lies strictly between -1 and 1"):
            PositionCovariance(1.0, 1.0, rho)


# --- associations, hypotheses, policy and graph -----------------------------------------------


def test_an_association_document_and_its_refusals() -> None:
    a = association(evidence={"separation_arcsec": 0.1})
    assert a.to_document() == {
        "record_id": "r",
        "hypothesis_id": "h",
        "method": "m",
        "method_version": "1.0.0",
        "prior": 0.5,
        "likelihood": 2.0,
        "posterior": 0.8,
        "bayes_factor": 4.0,
        "competing": ["h2"],
        "evidence": {"separation_arcsec": 0.1},
        "state": "proposed",
        "ledger_entry_id": None,
        "reviewer": None,
        "reason": "",
    }
    for what, p in (("prior", -0.1), ("prior", 1.1), ("posterior", -0.1), ("posterior", 1.1)):
        with exactly(InvariantViolation, f"{what} is a probability"):
            association(**{what: p})
    assert association(prior=0.0, posterior=1.0).posterior == 1.0
    assert association(prior=1.0, posterior=0.0).prior == 1.0
    with exactly(InvalidTransition, "an association is accepted only with a reviewer's ledger entry (INT-011)"):
        association(state=ReviewState.ACCEPTED)
    with exactly(InvariantViolation, "a rejected association records the reason"):
        association(state=ReviewState.REJECTED)


@pytest.mark.req("INT-011")
def test_a_transition_records_the_reviewer_and_keeps_the_entry_and_reason() -> None:
    accepted = association().transition(ReviewState.ACCEPTED, reviewer="leigh", ledger_entry_id="ledger-1")
    assert (accepted.state, accepted.reviewer, accepted.ledger_entry_id, accepted.reason) == (
        ReviewState.ACCEPTED,
        "leigh",
        "ledger-1",
        "",
    )
    superseded = accepted.transition(ReviewState.SUPERSEDED, reviewer="kernel", reason="re-run")
    assert (superseded.reviewer, superseded.ledger_entry_id, superseded.reason) == ("kernel", "ledger-1", "re-run")
    rejected = association().transition(ReviewState.REJECTED, reviewer="leigh", reason="blend")
    assert rejected.transition(ReviewState.SUPERSEDED, reviewer="kernel").reason == "blend"
    with exactly(InvalidTransition, "association cannot move from superseded to accepted"):
        superseded.transition(ReviewState.ACCEPTED, reviewer="leigh", ledger_entry_id="ledger-2")
    with exactly(InvalidTransition, "an association is accepted only with a reviewer's ledger entry (INT-011)"):
        association().transition(ReviewState.ACCEPTED, reviewer="leigh")


def test_a_hypothesis_groups_distinct_records() -> None:
    assert ObjectHypothesis("h", ("a", "b")).to_document() == {"hypothesis_id": "h", "members": ["a", "b"]}
    with exactly(InvariantViolation, "an object hypothesis groups at least one record"):
        ObjectHypothesis("h", ())
    with exactly(InvariantViolation, "hypothesis members are distinct"):
        ObjectHypothesis("h", ("a", "a"))


def test_a_policy_document_and_its_bounds() -> None:
    policy = AssociationPolicy("p", "1.0.0", 2023.0, 3.0, 0.01, True, 0.8, "2mass")
    assert policy.to_document() == {
        "policy_id": "p",
        "version": "1.0.0",
        "common_epoch_jyear": 2023.0,
        "search_radius_arcsec": 3.0,
        "min_posterior": 0.01,
        "photometric_terms": True,
        "counterpart_prior": 0.8,
        "anchor_catalogue": "2mass",
    }
    with exactly(InvariantViolation, "search radius is positive"):
        replace(policy, search_radius_arcsec=0.0)
    for floor in (-0.1, 0.5):
        with exactly(InvariantViolation, "the negligible-posterior floor lies in [0, 0.5)"):
            replace(policy, min_posterior=floor)
    assert replace(policy, min_posterior=0.0).min_posterior == 0.0
    for prior in (0.0, 1.0):
        with exactly(InvariantViolation, "the counterpart prior lies strictly between 0 and 1"):
            replace(policy, counterpart_prior=prior)


def small_graph() -> AssociationGraph:
    return AssociationGraph(
        association_policy(),
        "m",
        "1.0.0",
        (anchor("g", 10.0, 0.0), detection("d", 10.0, 0.0)),
        (ObjectHypothesis("h", ("g",)), ObjectHypothesis("h2", ("g",))),
        (association(record_id="d"), association(record_id="d", hypothesis_id="h2", posterior=0.2)),
        0.5,
    )


def test_a_graph_document_its_identity_and_its_refusals() -> None:
    g = small_graph()
    doc = g.to_document()
    assert doc == {
        "schema_version": "1.0.0",
        "method": "m",
        "method_version": "1.0.0",
        "policy": association_policy().to_document(),
        "source_density_per_arcsec2": 0.5,
        "records": [r.to_document() for r in g.records],
        "hypotheses": [h.to_document() for h in g.hypotheses],
        "associations": [a.to_document() for a in g.associations],
        "graph_id": g.graph_id,
    }
    assert "graph_id" not in g.to_document(include_id=False)
    with exactly(InvariantViolation, "record ids are unique within a graph"):
        replace(g, records=(*g.records, detection("d", 1.0, 1.0)))
    with exactly(InvariantViolation, "hypothesis ids are unique within a graph"):
        replace(g, hypotheses=(*g.hypotheses, ObjectHypothesis("h", ("d",))))
    for stray in (association(record_id="x"), association(record_id="d", hypothesis_id="x")):
        with exactly(InvariantViolation, "an association links a record and a hypothesis of the same graph"):
            replace(g, associations=(stray,))
    for density in (-1.0, math.inf):
        with exactly(InvariantViolation, "the chance-alignment density is a finite non-negative number"):
            replace(g, source_density_per_arcsec2=density)
    assert replace(g, source_density_per_arcsec2=0.0).source_density_per_arcsec2 == 0.0


@pytest.mark.req("INT-011")
def test_a_review_changes_only_the_association_it_names() -> None:
    g = small_graph()
    reviewed = g.review("d", "h2", ReviewState.REJECTED, reviewer="leigh", reason="too far")
    assert [(a.hypothesis_id, a.state, a.reviewer, a.reason) for a in reviewed.associations] == [
        ("h", ReviewState.PROPOSED, None, ""),
        ("h2", ReviewState.REJECTED, "leigh", "too far"),
    ]
    accepted = g.review("d", "h", ReviewState.ACCEPTED, reviewer="leigh", ledger_entry_id="ledger-3")
    assert accepted.associations[0].ledger_entry_id == "ledger-3"
    with exactly(InvariantViolation, "no association of d with h3"):
        g.review("d", "h3", ReviewState.REJECTED, reviewer="leigh", reason="x")


# --- geometry ---------------------------------------------------------------------------------


def unit(ra: float, dec: float) -> tuple[float, float, float]:
    r, d = math.radians(ra), math.radians(dec)
    return math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d)


@pytest.mark.parametrize(
    ("ra1", "dec1", "ra2", "dec2"), [(0.0, 0.0, 0.0, 1.0), (10.0, 20.0, 11.0, 30.0), (200.0, -60.0, 140.0, -10.0)]
)
def test_separation_agrees_with_the_angle_between_unit_vectors(
    ra1: float, dec1: float, ra2: float, dec2: float
) -> None:
    a, b = unit(ra1, dec1), unit(ra2, dec2)
    cross = math.sqrt(
        sum(x * x for x in (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]))
    )
    want = math.degrees(math.atan2(cross, sum(x * y for x, y in zip(a, b, strict=True)))) * 3600
    assert separation_arcsec(ra1, dec1, ra2, dec2) == pytest.approx(want, rel=1e-12)


@pytest.mark.parametrize(("ra0", "dec0", "ra", "dec"), [(10.0, 30.0, 11.0, 30.5), (200.0, -60.0, 199.0, -61.0)])
def test_the_gnomonic_offset_is_the_tangent_plane_projection(ra0: float, dec0: float, ra: float, dec: float) -> None:
    c, p = unit(ra0, dec0), unit(ra, dec)
    r0, d0 = math.radians(ra0), math.radians(dec0)
    east = (-math.sin(r0), math.cos(r0), 0.0)
    north = (-math.sin(d0) * math.cos(r0), -math.sin(d0) * math.sin(r0), math.cos(d0))
    dot = sum(x * y for x, y in zip(c, p, strict=True))
    want = tuple(math.degrees(sum(x * y for x, y in zip(axis, p, strict=True)) / dot) * 3600 for axis in (east, north))
    assert offset_arcsec(ra0, dec0, ra, dec) == pytest.approx(want, rel=1e-12)
    assert offset_arcsec(ra0, dec0, ra0, dec0) == (0.0, 0.0)


@pytest.mark.req("INT-010")
def test_propagation_lands_at_the_target_epoch_and_carries_the_correlation() -> None:
    still = anchor("a", 10.0, 0.0)
    at_gap = propagate(still, 2017.0)
    assert at_gap is not None and (at_gap.ra_deg, at_gap.dec_deg, at_gap.epoch_jyear) == (10.0, 0.0, 2017.0)
    assert propagate(still, 2017.01) is None
    moving = anchor(
        "m", 10.0, 0.0, covariance=PositionCovariance(0.03, 0.04, 0.5), motion=Motion(0.0, 0.0, None, 10.0, 20.0)
    )
    p = propagate(moving, 2026.0)
    assert p is not None and p.epoch_jyear == 2026.0
    s_ra, s_dec = math.hypot(0.03, 0.1), math.hypot(0.04, 0.2)
    assert (p.covariance.sigma_ra_arcsec, p.covariance.sigma_dec_arcsec) == pytest.approx((s_ra, s_dec))
    assert p.covariance.rho == pytest.approx(0.5 * 0.03 * 0.04 / (s_ra * s_dec))


@pytest.mark.req("INT-009", "INT-023")
def test_a_blend_option_sits_at_the_members_mean_widened_by_the_unknown_flux_split() -> None:
    dec0 = 60.0
    east = 2 * ARCSEC / math.cos(math.radians(dec0))
    a = anchor("a", 10.0, dec0)
    b = anchor("b", 10.0 + east, dec0 + 2 * ARCSEC, covariance=PositionCovariance(0.05, 0.06))
    coarse = detection("w", 10.0 + east / 2, dec0 + ARCSEC, resolution_arcsec=6.0)
    by_id = {o.record.record_id: o.options for o in candidate_options([coarse, b, a], association_policy())}
    assert by_id["a"] == () and by_id["b"] == ()
    (blend,) = by_id["w"]
    assert blend.members == ("a", "b") and blend.is_blend and blend.position.epoch_jyear == 2016.0
    assert blend.position.ra_deg == pytest.approx(10.0 + east / 2, abs=1e-4 * ARCSEC)
    assert blend.position.dec_deg == pytest.approx(dec0 + ARCSEC, abs=1e-4 * ARCSEC)
    # the members sit 1 arcsec east and north of their mean either way: a scatter of 1 per axis, over n + 1 = 3
    s_ra, s_dec = math.sqrt((0.03**2 + 0.05**2) / 2 + 1 / 3), math.sqrt((0.04**2 + 0.06**2) / 2 + 1 / 3)
    covariance = blend.position.covariance
    assert covariance.sigma_ra_arcsec == pytest.approx(s_ra, rel=1e-4)
    assert covariance.sigma_dec_arcsec == pytest.approx(s_dec, rel=1e-4)
    assert covariance.rho == pytest.approx((1 / 3) / (s_ra * s_dec), rel=1e-4), (
        "the split runs along the line joining them"
    )


@pytest.mark.req("INT-023")
def test_three_blended_members_spread_the_centroid_by_their_scatter_over_four() -> None:
    a, b, c = anchor("a", 10.0, 0.0), anchor("b", 10.0 + 3 * ARCSEC, 0.0), anchor("c", 10.0, 3 * ARCSEC)
    coarse = detection("w", 10.0 + ARCSEC, ARCSEC, resolution_arcsec=6.0)
    by_id = {o.record.record_id: o.options for o in candidate_options([coarse, a, b, c], association_policy())}
    (blend,) = by_id["w"]
    # offsets from the mean (1, 1): (-1, -1), (2, -1), (-1, 2); scatter sums 6, 6 and -3, over n (n + 1) = 12
    s = math.sqrt(0.03**2 + 0.5)
    assert blend.position.covariance.sigma_ra_arcsec == pytest.approx(s, rel=1e-6)
    assert blend.position.covariance.sigma_dec_arcsec == pytest.approx(math.sqrt(0.04**2 + 0.5), rel=1e-6)
    assert blend.position.covariance.rho == pytest.approx(-0.25 / (s * math.sqrt(0.04**2 + 0.5)), rel=1e-5)


@pytest.mark.req("INT-009")
def test_candidates_include_an_anchor_exactly_at_the_radius_or_resolution() -> None:
    old = anchor("a-old", 10.0, 0.0, epoch_jyear=2000.0)
    edge = anchor("b-edge", 10.0, 2 * ARCSEC)
    det = detection("d", 10.0, 0.0, epoch_jyear=2016.0)
    radius = separation_arcsec(10.0, 0.0, 10.0, 2 * ARCSEC)
    policy = replace(association_policy(), search_radius_arcsec=radius)
    (options,) = (o.options for o in candidate_options([old, edge, det], policy) if o.record.record_id == "d")
    assert [o.members for o in options] == [("b-edge",)], "an anchor too old to compare is skipped, not the end"
    near = anchor("c-near", 10.0, ARCSEC)
    link = math.hypot(*offset_arcsec(10.0, ARCSEC, 10.0, 2 * ARCSEC))
    linked = replace(det, resolution_arcsec=link)
    (options,) = (o.options for o in candidate_options([edge, near, linked], policy) if o.record.record_id == "d")
    assert [o.members for o in options] == [("b-edge",), ("c-near",)], "anchors exactly a resolution apart are resolved"


def test_the_field_density_counts_anchors_over_the_circle_that_holds_every_record() -> None:
    policy = association_policy()
    assert field_density_per_arcsec2([], policy) == 1 / (math.pi * 3600.0)
    assert field_density_per_arcsec2([detection("d", 10.0, 0.0)], policy) == 1 / (math.pi * 3600.0)
    wide: list[AssociationRecord] = [
        anchor("a", 350.0, 100 * ARCSEC),
        detection("d", 350.0, -100 * ARCSEC),
        detection("e", 350.0, 0.0),
    ]
    assert field_density_per_arcsec2(wide, policy) == pytest.approx(1 / (math.pi * 100.0**2), rel=1e-9)
    two = [*wide, anchor("b", 350.0, 0.0)]
    assert field_density_per_arcsec2(two, policy) == pytest.approx(2 / (math.pi * 100.0**2), rel=1e-9)
