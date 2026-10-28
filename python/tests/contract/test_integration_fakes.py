"""T3 with fakes: the v0.4.0 integration fakes pass their ports' contract suites."""

from __future__ import annotations

import pytest

from sdp.testing.contracts.integration import (
    AssociationCase,
    AssociationServiceContract,
    CapabilityDiscoveryContract,
    CapabilityRegistryContract,
    DiscoveryCase,
    RegistryCase,
)
from sdp.testing.fakes.integration import FixtureDiscovery, InMemoryCapabilityRegistry, PlantedAssociations
from sdp.testing.integration import (
    SYNTHETIC_FIELD,
    association_policy,
    image_requirement,
    planted_field,
    registry_descriptors,
)

pytestmark = pytest.mark.tier("T3")


class TestInMemoryCapabilityRegistry(CapabilityRegistryContract):
    @pytest.fixture
    def capability_registry_case(self) -> RegistryCase:
        return RegistryCase(InMemoryCapabilityRegistry(), registry_descriptors())


class TestFixtureDiscovery(CapabilityDiscoveryContract):
    @pytest.fixture
    def discovery_case(self) -> DiscoveryCase:
        descriptors = registry_descriptors()
        return DiscoveryCase(
            discovery=FixtureDiscovery(InMemoryCapabilityRegistry(descriptors)),
            field=SYNTHETIC_FIELD,
            need=image_requirement(),
            registered=tuple(d.source_id for d in descriptors),
            expected_satisfied="synthetic-mast",
        )


class TestPlantedAssociations(AssociationServiceContract):
    @pytest.fixture
    def association_case(self) -> AssociationCase:
        gaia, dets = planted_field()
        truth = {
            "gaia-a": (("obj-a", 1.0),),
            "jwst-a": (("obj-a", 0.99),),
            "gaia-b1": (("obj-b1", 1.0),),
            "gaia-b2": (("obj-b2", 1.0),),
            "wise-b": (("obj-b1", 0.55), ("obj-b2", 0.45)),
            "gaia-c": (("obj-c", 1.0),),
            "jwst-c": (("obj-c", 0.98),),
        }
        return AssociationCase(
            service=PlantedAssociations(truth),
            records=(*gaia, *dets),
            policy=association_policy(),
            ambiguous=("wise-b",),
            same_object=(("gaia-a", "jwst-a"), ("gaia-c", "jwst-c")),
        )
