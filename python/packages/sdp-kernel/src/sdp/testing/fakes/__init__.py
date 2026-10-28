"""In-memory fakes for every driven port."""

from sdp.testing.fakes.archives import (
    AstropyCrossMatch,
    FakeArchive,
    IdentityCurator,
    InMemoryHistoricalSources,
)
from sdp.testing.fakes.compute import FakeClusterer, FakeEnvironmentProbe, SynchronousExecutor
from sdp.testing.fakes.determinism import FixedClock, SequentialIds, Sha256Hasher
from sdp.testing.fakes.ledger import InMemoryLedger
from sdp.testing.fakes.metadata import (
    InMemoryDatabase,
    InMemoryEventLog,
    InMemoryEvidenceGraph,
    InMemoryUnitOfWork,
)
from sdp.testing.fakes.misc import FakeDoiMinter, InMemoryProtocolRegistry, NullTracker
from sdp.testing.fakes.storage import InMemoryArtifactStore, InMemoryDatasetRepository

__all__ = [
    "AstropyCrossMatch",
    "FakeArchive",
    "FakeClusterer",
    "FakeDoiMinter",
    "FakeEnvironmentProbe",
    "FixedClock",
    "IdentityCurator",
    "InMemoryArtifactStore",
    "InMemoryDatabase",
    "InMemoryDatasetRepository",
    "InMemoryEventLog",
    "InMemoryEvidenceGraph",
    "InMemoryHistoricalSources",
    "InMemoryLedger",
    "InMemoryProtocolRegistry",
    "InMemoryUnitOfWork",
    "NullTracker",
    "SequentialIds",
    "Sha256Hasher",
    "SynchronousExecutor",
]
