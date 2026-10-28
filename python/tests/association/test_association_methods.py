"""The two association implementations (v0.4.0 §4, §12): T3 contract suites, T8 agreement and
recovery on planted truth, and the propagation they share (INT-009, INT-010, INT-012)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from sdp.adapters.association import BayesFactorAssociation, LikelihoodRatioAssociation
from sdp.kernel.domain.association import AssociationGraph, CatalogueSource, Detection, ReviewState
from sdp.kernel.ports.integration import AssociationService
from sdp.testing.contracts.integration import AssociationCase, AssociationServiceContract
from sdp.testing.integration import association_policy, planted_field, synthetic_association_field

AGREEMENT_TOLERANCE = 1e-6
"""Declared tolerance on every posterior between the two methods (INT-012)."""

SERVICES: list[AssociationService] = [BayesFactorAssociation(), LikelihoodRatioAssociation()]


def planted_case(service: AssociationService) -> AssociationCase:
    gaia, dets = planted_field()
    return AssociationCase(
        service=service,
        records=(*gaia, *dets),
        policy=association_policy(),
        ambiguous=("wise-b", "hst-d"),
        same_object=(("gaia-a", "jwst-a"), ("gaia-c", "jwst-c")),
    )


@pytest.mark.tier("T3")
class TestBayesFactorAssociation(AssociationServiceContract):
    @pytest.fixture
    def association_case(self) -> AssociationCase:
        return planted_case(BayesFactorAssociation())


@pytest.mark.tier("T3")
class TestLikelihoodRatioAssociation(AssociationServiceContract):
    @pytest.fixture
    def association_case(self) -> AssociationCase:
        return planted_case(LikelihoodRatioAssociation())


def _posteriors(graph: AssociationGraph) -> dict[tuple[str, str], float]:
    return {(a.record_id, a.hypothesis_id): a.posterior for a in graph.associations}


def _agree(records: list[CatalogueSource | Detection]) -> None:
    policy = association_policy()
    a = _posteriors(BayesFactorAssociation().associate(records, policy))
    b = _posteriors(LikelihoodRatioAssociation().associate(records, policy))
    assert a.keys() == b.keys()
    worst = max(abs(a[k] - b[k]) for k in a)
    assert worst <= AGREEMENT_TOLERANCE, f"methods disagree by {worst}"


@pytest.mark.tier("T8")
@pytest.mark.scientific
@pytest.mark.req("INT-012")
def test_the_two_methods_agree_on_the_reference_fixture() -> None:
    gaia, dets = planted_field()
    _agree([*gaia, *dets])


@pytest.mark.tier("T8")
@pytest.mark.scientific
@pytest.mark.req("INT-012")
@pytest.mark.parametrize("seed", range(5))
def test_the_two_methods_agree_on_synthetic_fields(seed: int) -> None:
    records, _ = synthetic_association_field(seed)
    _agree(records)


@pytest.mark.tier("T8")
@pytest.mark.scientific
@pytest.mark.req("INT-009")
@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("service", SERVICES, ids=lambda s: s.method)
def test_planted_identities_and_blends_are_recovered(seed: int, service: AssociationService) -> None:
    records, truth = synthetic_association_field(seed)
    graph = service.associate(records, association_policy())
    for rid, anchors in truth.items():
        ranked = graph.for_record(rid)
        assert ranked, rid
        if len(anchors) == 1:
            assert ranked[0].hypothesis_id == f"obj:{next(iter(anchors))}", rid
            assert ranked[0].posterior > 0.99
        else:
            blend = {a.hypothesis_id for a in ranked if a.evidence.get("role") == "blend"}
            assert blend == {f"obj:{x}" for x in anchors}, f"{rid} lost a blend member"
            assert all(a.evidence.get("role") != "counterpart" for a in ranked), "a blend became one-to-one"


@pytest.mark.tier("T1")
@pytest.mark.req("INT-009")
@pytest.mark.parametrize("service", SERVICES, ids=lambda s: s.method)
def test_a_genuine_ambiguity_keeps_both_hypotheses_with_the_prior_recorded(service: AssociationService) -> None:
    gaia, dets = planted_field()
    graph = service.associate([*gaia, *dets], association_policy())
    ranked = graph.for_record("hst-d")
    top = [a for a in ranked if a.evidence["role"] == "counterpart"]
    assert {a.hypothesis_id for a in top} == {"obj:gaia-d1", "obj:gaia-d2"}
    assert abs(top[0].posterior - top[1].posterior) < 0.05
    for a in ranked:
        assert a.evidence["density_per_arcsec2"] == graph.source_density_per_arcsec2 > 0
        assert set(a.competing) == {x.hypothesis_id for x in ranked} - {a.hypothesis_id}
        assert a.state is ReviewState.PROPOSED


@pytest.mark.tier("T1")
@pytest.mark.req("INT-010")
@pytest.mark.parametrize("service", SERVICES, ids=lambda s: s.method)
def test_a_moving_star_matches_only_after_propagation(service: AssociationService) -> None:
    gaia, dets = planted_field()
    moved = service.associate([*gaia, *dets], association_policy())
    assert moved.for_record("jwst-c")[0].hypothesis_id == "obj:gaia-c"
    frozen = [
        replace(g, motion=replace(g.motion, pmra_mas_yr=0.0)) if g.record_id == "gaia-c" and g.motion else g
        for g in gaia
    ]
    static = service.associate([*frozen, *dets], association_policy())
    assert static.for_record("jwst-c")[0].hypothesis_id == "obj:jwst-c", "matched without propagating"
