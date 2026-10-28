"""T3/T5 with real adapters: the same contract suites the fakes pass (TST-003).

Each class here mirrors one in ``tests/contract/test_kernel_fakes.py``; a fake that passes what
its real adapter fails (or the reverse) shows up as a failure in exactly one of the two.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from sdp.adapters.compute.cpu import CpuExecutor
from sdp.adapters.historical.filesystem import FilesystemHistoricalSources
from sdp.adapters.ledger.git import GitSignedLedger, load_signing_key
from sdp.adapters.postgres import PostgresDatabase, PostgresDatasetRepository
from sdp.adapters.storage.nas import NasArtifactStore
from sdp.adapters.system import Sha256Hasher, SystemClock, UlidGenerator
from sdp.adapters.telemetry.prometheus import PrometheusTextfileTracker
from sdp.testing.contracts.artifact_store import ArtifactStoreContract
from sdp.testing.contracts.compute import ComputeExecutorContract
from sdp.testing.contracts.dataset_repository import DatasetRepositoryContract
from sdp.testing.contracts.historical_sources import HistoricalSourcesContract
from sdp.testing.contracts.investigate import InvestigationRepositoryContract, sample_investigation, sample_set
from sdp.testing.contracts.ledger import LedgerContract
from sdp.testing.contracts.metadata import UnitOfWorkContract
from sdp.testing.contracts.misc import DeterminismContract, TrackerContract

from .keys import write_signing_key

pytestmark = [pytest.mark.tier("T5"), pytest.mark.integration, pytest.mark.req("TST-003")]


class TestNasArtifactStore(ArtifactStoreContract):
    @pytest.fixture
    def artifact_store(self, tmp_path: Path) -> NasArtifactStore:
        return NasArtifactStore(tmp_path / "nas")


class TestPostgresDatasetRepository(DatasetRepositoryContract):
    @pytest.fixture
    def dataset_repository(self, database_url: str) -> Iterator[PostgresDatasetRepository]:
        with psycopg.connect(database_url, autocommit=True) as conn:
            yield PostgresDatasetRepository(conn)


class TestPostgresUnitOfWork(UnitOfWorkContract):
    @pytest.fixture
    def uow_factory(self, database_url: str) -> Any:
        return PostgresDatabase(database_url).unit_of_work


class TestGitSignedLedger(LedgerContract):
    @pytest.fixture
    def ledger(self, tmp_path: Path) -> GitSignedLedger:
        key_path, _ = write_signing_key(tmp_path, "leigh")
        for principal in ("rev", "s"):
            write_signing_key(tmp_path, principal)
        # One key signs for every principal the suite uses; allow it under each name.
        line = (tmp_path / "allowed_signers").read_text().splitlines()[0].split(" ", 1)[1]
        (tmp_path / "allowed_signers").write_text("".join(f"{p} {line}\n" for p in ("leigh", "rev", "s")))
        return GitSignedLedger(
            root=tmp_path / "ledger",
            clock=SystemClock(),
            signing_key=load_signing_key(key_path),
            allowed_signers=tmp_path / "allowed_signers",
        )


class TestCpuExecutor(ComputeExecutorContract):
    @pytest.fixture
    def executor(self) -> CpuExecutor:
        return CpuExecutor(threads=1)


class TestSystemDeterminismSeams(DeterminismContract):
    @pytest.fixture
    def clock(self) -> SystemClock:
        return SystemClock()

    @pytest.fixture
    def ids(self) -> UlidGenerator:
        return UlidGenerator()

    @pytest.fixture
    def hasher(self) -> Sha256Hasher:
        return Sha256Hasher()


class TestPrometheusTracker(TrackerContract):
    @pytest.fixture
    def tracker(self, tmp_path: Path) -> PrometheusTextfileTracker:
        return PrometheusTextfileTracker(tmp_path / "textfile")


class TestFilesystemHistoricalSources(HistoricalSourcesContract):
    @pytest.fixture
    def historical_sources(self, tmp_path: Path) -> FilesystemHistoricalSources:
        return FilesystemHistoricalSources(tmp_path / "historical")


class TestPostgresInvestigations(InvestigationRepositoryContract):
    @pytest.fixture
    def uow_factory(self, database_url: str) -> Any:
        return PostgresDatabase(database_url).unit_of_work

    @pytest.mark.req("INV-010")
    def test_the_schema_refuses_to_delete_an_investigation(self, database_url: str) -> None:
        uow_factory = PostgresDatabase(database_url).unit_of_work
        with uow_factory() as uow:
            uow.investigations.save_set(sample_set())
            uow.investigations.save(sample_investigation("inv-kept"))
            uow.commit()
        with psycopg.connect(database_url, autocommit=True) as conn, pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM investigations WHERE investigation_id = 'inv-kept'")
