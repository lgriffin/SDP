"""T3 with fakes: every Investigate fake passes its port's contract suite (Investigate §8, TST-003)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.investigation import FetchedProduct, Field, ObservationCriteria, ProductRef
from sdp.kernel.ports.investigate import (
    Detection,
    FrameDescription,
    ImageFrame,
    ObservationTarget,
    ServiceRecord,
    SkyPoint,
)
from sdp.testing.contracts.integration import PlanRepositoryContract
from sdp.testing.contracts.investigate import (
    DetectorCase,
    DialectCase,
    ForcedPhotometerContract,
    InvestigationRepositoryContract,
    ObjectResolverContract,
    ObservationArchiveGatewayContract,
    ObservationCase,
    PhotometerCase,
    ProductDialectContract,
    RefinerCase,
    RegistryCase,
    ResolverCase,
    SourceDetectorContract,
    SourceRegistryContract,
    WcsRefinerContract,
)
from sdp.testing.fakes import InMemoryDatabase
from sdp.testing.fakes.investigate import (
    NearestWcsRefiner,
    PlantedDetector,
    PlantedImage,
    PlantedPhotometer,
    StaticSourceRegistry,
    ThinningDialect,
)
from sdp.testing.investigate import (
    fixture_resolver,
    observation_archive,
    planted_lightcurve,
    synthetic_catalogue,
    synthetic_field,
)

pytestmark = pytest.mark.tier("T3")

FIELD = synthetic_field()
FRAME = ImageFrame("sha256:" + "1" * 64, b"SIMPLE  =                    T", "application/fits")
REF_RA = np.array([120.0, 120.01, 120.02])
REF_DEC = np.array([-10.0, -10.01, -10.02])
OFFSET = 0.5 / 3600


def planted() -> PlantedDetector:
    dets = [
        Detection(10.0 * i, 5.0 * i, ra + OFFSET, dec, 100.0)
        for i, (ra, dec) in enumerate(zip(REF_RA, REF_DEC, strict=True))
    ]
    return PlantedDetector({FRAME.artifact_id: dets})


class TestFakeObservationArchive(ObservationArchiveGatewayContract):
    @pytest.fixture
    def observation_case(self) -> ObservationCase:
        first = synthetic_catalogue().to_pylist()[0]
        return ObservationCase(
            gateway=observation_archive(),
            target=ObservationTarget(first["source_key"], first["ra_deg"], first["dec_deg"]),
            criteria=ObservationCriteria(radius_arcsec=2.0),
            field=FIELD,
        )


class TestFakeObservationArchiveFiltered(ObservationArchiveGatewayContract):
    @pytest.fixture
    def observation_case(self) -> ObservationCase:
        first = synthetic_catalogue().to_pylist()[0]
        return ObservationCase(
            gateway=observation_archive(),
            target=ObservationTarget(first["source_key"], first["ra_deg"], first["dec_deg"]),
            criteria=ObservationCriteria(missions=("DASCH",), product_types=("lightcurve",), radius_arcsec=2.0),
            field=FIELD,
        )


class TestPlantedDetector(SourceDetectorContract):
    @pytest.fixture
    def detector_case(self) -> DetectorCase:
        return DetectorCase(planted(), FRAME, expected=3)


class TestNearestWcsRefiner(WcsRefinerContract):
    @pytest.fixture
    def refiner_case(self) -> RefinerCase:
        return RefinerCase(NearestWcsRefiner(), planted(), FRAME, REF_RA, REF_DEC, max_rms_arcsec=0.01)


class TestPlantedPhotometer(ForcedPhotometerContract):
    @pytest.fixture
    def photometer_case(self) -> PhotometerCase:
        image = PlantedImage(FrameDescription("F656N", 50000.0, "WFC3", 0.04, 0.1), {"s": (120.0, -10.0, 5000.0)})
        return PhotometerCase(
            PlantedPhotometer({FRAME.artifact_id: image}),
            FRAME,
            SkyPoint("s", 120.0, -10.0),
            5000.0,
            0.1,
            "F656N",
            SkyPoint("far", 121.0, -10.0),
        )


class TestFixtureResolver(ObjectResolverContract):
    @pytest.fixture
    def resolver_case(self) -> ResolverCase:
        resolver = fixture_resolver()
        rec = resolver.table[0]
        assert rec.ra_deg is not None and rec.dec_deg is not None
        return ResolverCase(resolver, "HD  999999", rec.ra_deg, rec.dec_deg, 200.0, 40.0)


class TestStaticSourceRegistry(SourceRegistryContract):
    @pytest.fixture
    def registry_case(self) -> RegistryCase:
        registry = StaticSourceRegistry(
            (
                ServiceRecord("ivo://fake/tap", "Fake TAP", "TAP", "https://fake.invalid/tap"),
                ServiceRecord("ivo://fake/sia", "Fake SIA", "SIA", "https://fake.invalid/sia"),
            )
        )
        return RegistryCase(registry, Field(Cone(1.0, 1.0, 0.1)), "tap")


class TestInMemoryInvestigationRepository(InvestigationRepositoryContract):
    @pytest.fixture
    def uow_factory(self) -> Any:
        return InMemoryDatabase().unit_of_work


class TestInMemoryPlanRepository(PlanRepositoryContract):
    @pytest.fixture
    def uow_factory(self) -> Any:
        return InMemoryDatabase().unit_of_work


class TestThinningDialect(ProductDialectContract):
    @pytest.fixture
    def dialect_case(self) -> DialectCase:
        ref = ProductRef("fake-plates", "lc-0", "https://fake.invalid/lc-0.csv")
        raw = FetchedProduct(
            ref, planted_lightcurve(0), "text/csv", "fake:1", "fixture licence", "fixture citation", ref.uri
        )
        return DialectCase(ThinningDialect(), raw, "lightcurve", min_epochs=10)
