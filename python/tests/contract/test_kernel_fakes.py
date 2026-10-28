"""T3 with fakes: every fake passes its port's contract suite (TST-003).

Real adapters join these parametrisations in their own test modules as they land, so the same
suite runs against the fake and every available adapter.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest

from sdp.kernel.domain.archive import CatalogueQuery, Cone
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.science.io import ResolvedInput, ResolvedInputs
from sdp.testing.catalogues import FAKE_GAIA, fixture_catalogue
from sdp.testing.contracts.archive_gateway import ArchiveGatewayContract, GatewayCase
from sdp.testing.contracts.artifact_store import ArtifactStoreContract
from sdp.testing.contracts.compute import ClusterSearchContract, ComputeExecutorContract
from sdp.testing.contracts.cross_match import CrossMatchContract
from sdp.testing.contracts.dataset_repository import DatasetRepositoryContract
from sdp.testing.contracts.historical_sources import HistoricalSourcesContract
from sdp.testing.contracts.ledger import LedgerContract
from sdp.testing.contracts.metadata import UnitOfWorkContract
from sdp.testing.contracts.misc import (
    DeterminismContract,
    DialectCuratorContract,
    PublisherContract,
    TrackerContract,
)
from sdp.testing.contracts.scientific_protocol import ScientificProtocolContract
from sdp.testing.fakes import (
    AstropyCrossMatch,
    FakeArchive,
    FakeClusterer,
    FakeDoiMinter,
    FixedClock,
    IdentityCurator,
    InMemoryArtifactStore,
    InMemoryDatabase,
    InMemoryDatasetRepository,
    InMemoryHistoricalSources,
    InMemoryLedger,
    NullTracker,
    SequentialIds,
    Sha256Hasher,
    SynchronousExecutor,
)
from sdp.testing.synthetic import SyntheticLinearProtocol, make_series

pytestmark = [pytest.mark.tier("T3"), pytest.mark.req("TST-003")]


class TestArtifactStore(ArtifactStoreContract):
    @pytest.fixture
    def artifact_store(self) -> InMemoryArtifactStore:
        return InMemoryArtifactStore()


class TestDatasetRepository(DatasetRepositoryContract):
    @pytest.fixture
    def dataset_repository(self) -> InMemoryDatasetRepository:
        return InMemoryDatasetRepository()


class TestUnitOfWork(UnitOfWorkContract):
    @pytest.fixture
    def uow_factory(self) -> Any:
        return InMemoryDatabase().unit_of_work


class TestLedger(LedgerContract):
    @pytest.fixture
    def ledger(self) -> InMemoryLedger:
        return InMemoryLedger()


class TestArchiveGateway(ArchiveGatewayContract):
    @pytest.fixture
    def archive_case(self) -> GatewayCase:
        query = CatalogueQuery(
            "fake-gaia",
            "gaia:DR3",
            Cone(56.5, 24.25, 1.0),
            ("source_id", "ra", "dec", "phot_g_mean_mag"),
            row_cap=100,
            chunk_rows=40,
        )
        return GatewayCase(FakeArchive(FAKE_GAIA, fixture_catalogue()), query)


class TestHistoricalSources(HistoricalSourcesContract):
    @pytest.fixture
    def historical_sources(self) -> InMemoryHistoricalSources:
        return InMemoryHistoricalSources()


class TestCrossMatch(CrossMatchContract):
    @pytest.fixture
    def cross_match(self) -> AstropyCrossMatch:
        return AstropyCrossMatch()


class TestExecutor(ComputeExecutorContract):
    @pytest.fixture
    def executor(self) -> SynchronousExecutor:
        return SynchronousExecutor()


class TestFakeClusterer(ClusterSearchContract):
    @pytest.fixture
    def clusterer(self) -> FakeClusterer:
        return FakeClusterer()

    @pytest.fixture
    def cluster_params(self) -> dict[str, Any]:
        return {"eps": 1.0, "min_cluster_size": 10}


class TestDeterminism(DeterminismContract):
    @pytest.fixture
    def clock(self) -> FixedClock:
        return FixedClock()

    @pytest.fixture
    def ids(self) -> SequentialIds:
        return SequentialIds()

    @pytest.fixture
    def hasher(self) -> Sha256Hasher:
        return Sha256Hasher()


class TestTracker(TrackerContract):
    @pytest.fixture
    def tracker(self) -> NullTracker:
        return NullTracker()


class TestPublisher(PublisherContract):
    @pytest.fixture
    def publisher(self) -> FakeDoiMinter:
        return FakeDoiMinter()


class TestIdentityCurator(DialectCuratorContract):
    @pytest.fixture
    def curator_case(self) -> tuple[Any, ...]:
        table = pa.table({"source_key": ["x:1"], "ra_deg": [1.0]})
        return (IdentityCurator(), "fake", "fake:v1", table, ["source_key", "ra_deg"])


class TestSyntheticProtocol(ScientificProtocolContract):
    @pytest.fixture
    def protocol_case(self) -> tuple[Any, ...]:
        import json

        data = make_series()
        source = SourceRef(SourceKind.SYNTHETIC, "sdp.synthetic.linear", "Apache-2.0", "SDP synthetic generator")
        inputs = ResolvedInputs(
            {"series": ResolvedInput("series", ArtifactId.of_bytes(data), "application/json", source, json.loads(data))}
        )
        return (SyntheticLinearProtocol(), inputs, {}, 42)
