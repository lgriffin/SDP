"""Fakes for the v0.4.0 integration ports (Integration Architecture v0.4.0 §9)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date

from sdp.kernel.domain.association import (
    Association,
    AssociationGraph,
    AssociationPolicy,
    AssociationRecord,
    ObjectHypothesis,
)
from sdp.kernel.domain.integration import (
    AdapterCompatibilityRecord,
    CapabilityMatch,
    EvidenceRequirement,
    SourceCapabilityDescriptor,
    match_requirement,
    order_matches,
)
from sdp.kernel.domain.investigation import Field


class StaticCompatibilityRecords:
    """AdapterCompatibilitySource over a fixed list, as a release's conformance run would produce."""

    def __init__(self, records: Sequence[AdapterCompatibilityRecord] = ()) -> None:
        self._records = tuple(records)

    def records(self) -> Sequence[AdapterCompatibilityRecord]:
        return self._records


class InMemoryCapabilityRegistry:
    """Descriptors keyed by source id; ``put`` replaces, nothing is edited in place."""

    def __init__(self, descriptors: Sequence[SourceCapabilityDescriptor] = ()) -> None:
        self._by_id: dict[str, SourceCapabilityDescriptor] = {}
        for d in descriptors:
            self.put(d)

    def put(self, descriptor: SourceCapabilityDescriptor) -> None:
        self._by_id[descriptor.source_id] = descriptor

    def get(self, source_id: str) -> SourceCapabilityDescriptor | None:
        return self._by_id.get(source_id)

    def all(self) -> Sequence[SourceCapabilityDescriptor]:
        return tuple(self._by_id[k] for k in sorted(self._by_id))

    def stale(self, older_than: date) -> Sequence[SourceCapabilityDescriptor]:
        return tuple(d for d in self.all() if d.verified_on < older_than)


class FixtureDiscovery:
    """Discovery over descriptors already in a registry: no VO calls, the same matching rules."""

    def __init__(self, registry: InMemoryCapabilityRegistry) -> None:
        self._registry = registry

    def describe(self, source_id: str) -> SourceCapabilityDescriptor:
        found = self._registry.get(source_id)
        if found is None:
            raise KeyError(f"no descriptor for {source_id}")
        return found

    def match(self, field: Field, need: EvidenceRequirement) -> tuple[CapabilityMatch, ...]:
        region = field.query_region()
        return order_matches(match_requirement(d, region, need) for d in self._registry.all())


class PlantedAssociations:
    """Returns planted posteriors: ``truth`` maps a record id to (hypothesis id, posterior) pairs.

    Records without planted candidates become their own singleton hypothesis with posterior 1.
    """

    method = "planted"
    method_version = "1.0.0"

    def __init__(self, truth: Mapping[str, Sequence[tuple[str, float]]]) -> None:
        self._truth = {k: tuple(v) for k, v in truth.items()}

    def associate(self, records: Sequence[AssociationRecord], policy: AssociationPolicy) -> AssociationGraph:
        present = sorted(records, key=lambda r: r.record_id)
        ids = {r.record_id for r in present}
        members: dict[str, list[str]] = {}
        assocs: list[Association] = []
        for r in present:
            planted = self._truth.get(r.record_id) or ((f"obj:{r.record_id}", 1.0),)
            kept = [(h, p) for h, p in planted if p >= policy.min_posterior]
            for h, p in kept:
                members.setdefault(h, []).append(r.record_id)
                assocs.append(
                    Association(
                        record_id=r.record_id,
                        hypothesis_id=h,
                        method=self.method,
                        method_version=self.method_version,
                        prior=0.5,
                        likelihood=p,
                        posterior=p,
                        bayes_factor=None,
                        competing=tuple(o for o, _ in kept if o != h),
                    )
                )
        hyps = tuple(ObjectHypothesis(h, tuple(sorted(m))) for h, m in sorted(members.items()) if set(m) <= ids)
        return AssociationGraph(
            policy=policy,
            method=self.method,
            method_version=self.method_version,
            records=tuple(present),
            hypotheses=hyps,
            associations=tuple(assocs),
            source_density_per_arcsec2=0.0,
        )


def descriptor_with_conformance(
    descriptor: SourceCapabilityDescriptor, passed: Mapping[str, bool]
) -> SourceCapabilityDescriptor:
    """Test helper: the same descriptor with its adapter's conformance replaced."""
    assert descriptor.adapter is not None
    return replace(descriptor, adapter=replace(descriptor.adapter, conformance=dict(passed)))
