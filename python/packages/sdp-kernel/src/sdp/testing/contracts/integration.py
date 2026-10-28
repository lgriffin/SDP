"""Contract suites for the v0.4.0 integration ports (Integration Architecture v0.4.0 §9, §12 T3)."""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from datetime import date

import pytest

from sdp.kernel.domain.association import AssociationGraph, AssociationPolicy, AssociationRecord, ReviewState
from sdp.kernel.domain.integration import EvidenceRequirement, MatchStatus, SourceCapabilityDescriptor
from sdp.kernel.domain.investigation import Field
from sdp.kernel.ports.integration import AssociationService, CapabilityDiscoveryService, CapabilityRegistry

# ---------------------------------------------------------------------------------------------
# CapabilityRegistry


@dataclass(frozen=True)
class RegistryCase:
    registry: CapabilityRegistry
    descriptors: tuple[SourceCapabilityDescriptor, ...]


class CapabilityRegistryContract:
    @pytest.mark.req("INT-001")
    def test_every_descriptor_round_trips_with_its_verification_date(
        self, capability_registry_case: RegistryCase
    ) -> None:
        reg = capability_registry_case.registry
        for d in capability_registry_case.descriptors:
            reg.put(d)
        for d in capability_registry_case.descriptors:
            got = reg.get(d.source_id)
            assert got is not None
            assert got.to_document() == d.to_document()
            assert got.verified_on == d.verified_on
        assert {d.source_id for d in reg.all()} >= {d.source_id for d in capability_registry_case.descriptors}

    def test_put_replaces_rather_than_merges(self, capability_registry_case: RegistryCase) -> None:
        reg = capability_registry_case.registry
        first = capability_registry_case.descriptors[0]
        reg.put(first)
        newer = replace(first, verified_on=date(2030, 1, 1), missions=("ONLY",))
        reg.put(newer)
        got = reg.get(first.source_id)
        assert got is not None and got.missions == ("ONLY",) and got.verified_on == date(2030, 1, 1)

    def test_stale_lists_descriptors_verified_before_the_cutoff(self, capability_registry_case: RegistryCase) -> None:
        reg = capability_registry_case.registry
        for d in capability_registry_case.descriptors:
            reg.put(d)
        cutoff = max(d.verified_on for d in capability_registry_case.descriptors)
        stale = {d.source_id for d in reg.stale(cutoff)}
        assert stale == {d.source_id for d in capability_registry_case.descriptors if d.verified_on < cutoff}

    def test_an_unknown_source_is_none(self, capability_registry_case: RegistryCase) -> None:
        assert capability_registry_case.registry.get("no-such-source") is None


# ---------------------------------------------------------------------------------------------
# CapabilityDiscoveryService


@dataclass(frozen=True)
class DiscoveryCase:
    discovery: CapabilityDiscoveryService
    field: Field
    need: EvidenceRequirement
    registered: tuple[str, ...]
    expected_satisfied: str


class CapabilityDiscoveryContract:
    @pytest.mark.req("INT-003")
    def test_every_registered_source_is_returned_with_a_reason_per_field(self, discovery_case: DiscoveryCase) -> None:
        matches = discovery_case.discovery.match(discovery_case.field, discovery_case.need)
        assert sorted(m.source_id for m in matches) == sorted(discovery_case.registered)
        for m in matches:
            assert m.requirement_id == discovery_case.need.requirement_id
            assert m.reasons and all(r.reason for r in m.reasons)
            failed = [r for r in m.reasons if r.outcome is MatchStatus.UNSATISFIED]
            assert (m.status is MatchStatus.UNSATISFIED) == bool(failed)

    @pytest.mark.req("INT-003")
    def test_the_expected_source_is_satisfied_and_listed_first(self, discovery_case: DiscoveryCase) -> None:
        matches = discovery_case.discovery.match(discovery_case.field, discovery_case.need)
        assert matches[0].source_id == discovery_case.expected_satisfied
        assert matches[0].status is MatchStatus.SATISFIED

    def test_matching_is_reproducible(self, discovery_case: DiscoveryCase) -> None:
        a = discovery_case.discovery.match(discovery_case.field, discovery_case.need)
        b = discovery_case.discovery.match(discovery_case.field, discovery_case.need)
        assert [m.to_document() for m in a] == [m.to_document() for m in b]

    def test_describe_returns_the_registered_descriptor(self, discovery_case: DiscoveryCase) -> None:
        d = discovery_case.discovery.describe(discovery_case.expected_satisfied)
        assert d.source_id == discovery_case.expected_satisfied


# ---------------------------------------------------------------------------------------------
# AssociationService


@dataclass(frozen=True)
class AssociationCase:
    service: AssociationService
    records: tuple[AssociationRecord, ...]
    policy: AssociationPolicy
    ambiguous: tuple[str, ...]
    """Record ids that must keep more than one hypothesis."""
    same_object: tuple[tuple[str, str], ...]
    """Pairs of record ids whose top hypothesis must be shared."""


class AssociationServiceContract:
    @pytest.mark.req("INT-009")
    def test_every_record_appears_and_candidates_are_retained_not_pruned(
        self, association_case: AssociationCase
    ) -> None:
        graph = association_case.service.associate(association_case.records, association_case.policy)
        assert {r.record_id for r in graph.records} == {r.record_id for r in association_case.records}
        for rid in association_case.ambiguous:
            assert len(graph.for_record(rid)) >= 2, f"{rid} lost a competing hypothesis"
        for a in graph.associations:
            assert 0.0 <= a.posterior <= 1.0 and 0.0 <= a.prior <= 1.0
            assert a.posterior >= association_case.policy.min_posterior
            assert a.method == association_case.service.method

    @pytest.mark.req("INT-009")
    def test_a_records_posteriors_sum_to_at_most_one(self, association_case: AssociationCase) -> None:
        graph = association_case.service.associate(association_case.records, association_case.policy)
        for r in graph.records:
            assert sum(a.posterior for a in graph.for_record(r.record_id)) <= 1.0 + 1e-9

    def test_known_identities_share_their_top_hypothesis(self, association_case: AssociationCase) -> None:
        graph = association_case.service.associate(association_case.records, association_case.policy)
        for left, right in association_case.same_object:
            assert graph.for_record(left)[0].hypothesis_id == graph.for_record(right)[0].hypothesis_id

    @pytest.mark.req("INT-011")
    def test_the_service_never_accepts(self, association_case: AssociationCase) -> None:
        graph = association_case.service.associate(association_case.records, association_case.policy)
        assert all(a.state is ReviewState.PROPOSED for a in graph.associations)
        assert not graph.accepted()

    def test_posteriors_do_not_depend_on_record_order(self, association_case: AssociationCase) -> None:
        records = list(association_case.records)
        first = association_case.service.associate(records, association_case.policy)
        random.Random(7).shuffle(records)
        second = association_case.service.associate(records, association_case.policy)

        def view(g: AssociationGraph) -> list[tuple[str, frozenset[str], float]]:
            members = {h.hypothesis_id: frozenset(h.members) for h in g.hypotheses}
            return sorted((a.record_id, members[a.hypothesis_id], round(a.posterior, 12)) for a in g.associations)

        assert view(first) == view(second)
