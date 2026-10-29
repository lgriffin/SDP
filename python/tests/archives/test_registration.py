"""T3: the descriptors a live deployment registers before it can plan (Integration v0.4.0 §2)."""

from __future__ import annotations

from datetime import date

import pytest

from sdp.adapters.archives.registration import (
    RegisteredArchives,
    catalogue_descriptor,
    describe,
    gaia_datalink_descriptor,
    register_defaults,
)
from sdp.adapters.archives.tap import load_sources
from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.integration import EvidenceRequirement, MatchStatus, Operation, ProductType, match_requirement
from sdp.testing.fakes.integration import InMemoryCapabilityRegistry

pytestmark = pytest.mark.tier("T3")
TODAY = date(2026, 10, 29)


@pytest.mark.req("INT-003")
def test_a_packaged_catalogue_is_described_by_hand_and_feeds_exploration_until_admitted() -> None:
    gaia = catalogue_descriptor(load_sources()["gaia"], TODAY)
    assert (gaia.source_id, gaia.origin, gaia.verified_on) == ("gaia", "hand-written", TODAY)
    assert gaia.product_types == {ProductType.CATALOGUE} and Operation.CONE in gaia.operations
    assert gaia.adapter is not None and not gaia.adapter.conformance, "admission comes only from records"
    need = EvidenceRequirement(
        "identify:catalogue:gaia",
        "identify",
        frozenset({ProductType.CATALOGUE}),
        operations=frozenset({Operation.CONE}),
        needs_bytes=False,
    )
    match = match_requirement(gaia, Cone(259.28, 43.14, 0.01), need)
    assert match.status is MatchStatus.PARTIAL


def test_register_defaults_registers_every_packaged_catalogue() -> None:
    registry = InMemoryCapabilityRegistry()
    registered = register_defaults(
        registry, lambda name: pytest.fail(f"no transport for {name}"), mast=False, today=lambda: TODAY
    )
    expected = {*load_sources(), "gaia-datalink"}
    assert {d.source_id for d in registered} == expected == {d.source_id for d in registry.all()}


@pytest.mark.req("INT-022")
def test_gaia_datalink_products_are_described_and_matched_as_light_curves_and_spectra() -> None:
    gaia = gaia_datalink_descriptor(TODAY)
    assert (gaia.source_id, gaia.origin, gaia.verified_on) == ("gaia-datalink", "hand-written", TODAY)
    assert gaia.product_types == {ProductType.TIMESERIES, ProductType.SPECTRUM}
    assert [p.standard for p in gaia.protocols] == ["TAP", "DataLink"]
    assert gaia.operations == {Operation.CONE, Operation.DOWNLOAD}
    assert gaia.calibration_levels == {2, 3} and gaia.coverage.all_sky is True
    assert gaia.adapter is not None and (gaia.adapter.name, gaia.adapter.version) == ("gaia-datalink", "1.0.0")
    assert not gaia.adapter.conformance, "admission comes only from records"
    need = EvidenceRequirement("variability:product:lightcurve", "variability", frozenset({ProductType.TIMESERIES}))
    assert match_requirement(gaia, Cone(56.53, 23.35, 0.01), need).status is MatchStatus.PARTIAL


@pytest.mark.req("INV-019")
def test_the_registered_archives_are_listed_and_registered_again_for_the_api() -> None:
    archives = RegisteredArchives(
        InMemoryCapabilityRegistry(), lambda name: pytest.fail(f"no transport for {name}"), mast=False
    )
    assert archives.registered() == []
    registered = archives.register()
    assert [d["source_id"] for d in archives.registered()] == sorted({*load_sources(), "gaia-datalink"})
    gaia = next(d for d in registered if d["source_id"] == "gaia")
    assert gaia == {
        "source_id": "gaia",
        "origin": "hand-written",
        "verified_on": date.today().isoformat(),
        "product_types": ["catalogue"],
        "operations": sorted(o.value for o in catalogue_descriptor(load_sources()["gaia"], TODAY).operations),
        "adapter": "tap-archive",
        "admitted_for": [],
    }
    assert describe(catalogue_descriptor(load_sources()["gaia"], TODAY))["verified_on"] == "2026-10-29"
