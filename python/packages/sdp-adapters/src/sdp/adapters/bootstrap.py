"""The production composition root: the kernel wired to real adapters from Settings (§4).

This is the only module that imports more than one adapter; the CLI and the API both start here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa

from sdp.adapters.compute.cpu import CpuExecutor
from sdp.adapters.ledger.git import GitSignedLedger, load_signing_key
from sdp.adapters.logs import configure_logging
from sdp.adapters.postgres import PostgresDatabase
from sdp.adapters.settings import Settings
from sdp.adapters.storage.nas import NasArtifactStore
from sdp.adapters.system import SystemClock, SystemEnvironmentProbe, UlidGenerator
from sdp.adapters.telemetry.prometheus import PrometheusTextfileTracker
from sdp.kernel.application.kernel import Kernel, KernelPorts
from sdp.kernel.application.plugins import EntryPointProtocolRegistry
from sdp.kernel.domain.dataset import DialectRef
from sdp.kernel.ports.archives import ArchiveGateway, DialectCurator
from sdp.kernel.ports.evidence import ExperimentTracker
from sdp.kernel.ports.plugins import ProtocolRegistry
from sdp.kernel.ports.publication import BundlePublisher, DoiRecord


class NotConfigured(RuntimeError):
    pass


class UnconfiguredPublisher:
    """Stands in until a DOI publisher is configured; refuses rather than pretending to publish."""

    def publish(self, bundle: bytes, filename: str, metadata: Mapping[str, Any]) -> DoiRecord:
        raise NotConfigured("no bundle publisher is configured for this deployment")


class NoDialectsCurator:
    """Stands in until archive dialects are installed; refuses rather than guessing columns."""

    def dialect_for(self, source_id: str, release_label: str) -> DialectRef:
        raise NotConfigured(f"no dialect is installed for {source_id} {release_label}")

    def curate(self, native: pa.Table, dialect: DialectRef) -> pa.Table:
        raise NotConfigured(f"no dialect is installed for {dialect}")


class _NullTracker:
    def log_params(self, experiment_id: str, params: Mapping[str, Any]) -> None:
        return None

    def log_metrics(self, experiment_id: str, metrics: Mapping[str, float]) -> None:
        return None

    def set_tags(self, experiment_id: str, tags: Mapping[str, str]) -> None:
        return None


@dataclass
class Overrides:
    """Ports a deployment supplies beyond the defaults (archive gateways, curators, publishers)."""

    gateways: Mapping[str, ArchiveGateway] = field(default_factory=dict)
    curator: DialectCurator | None = None
    publisher: BundlePublisher | None = None
    protocols: ProtocolRegistry | None = None
    services: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Application:
    settings: Settings
    kernel: Kernel
    database: PostgresDatabase
    store: NasArtifactStore
    ledger: GitSignedLedger


def build_ledger(settings: Settings) -> GitSignedLedger:
    return GitSignedLedger(
        root=settings.ledger_dir,
        clock=SystemClock(),
        signing_key=load_signing_key(settings.signing_key) if settings.signing_key else None,
        allowed_signers=settings.allowed_signers,
        commit=settings.ledger_commit,
        require_signatures=settings.require_signatures,
    )


def build_application(settings: Settings, overrides: Overrides | None = None, *, logging: bool = True) -> Application:
    overrides = overrides or Overrides()
    if logging:
        configure_logging(settings.log_level)
    database = PostgresDatabase(settings.database_url)
    store = NasArtifactStore(settings.artifact_root)
    ledger = build_ledger(settings)
    tracker: ExperimentTracker = (
        PrometheusTextfileTracker(settings.metrics_dir) if settings.metrics_dir else _NullTracker()
    )
    ports = KernelPorts(
        uow=database.unit_of_work,
        store=store,
        ledger=ledger,
        protocols=overrides.protocols or EntryPointProtocolRegistry(),
        executor=CpuExecutor(threads=settings.threads),
        clock=SystemClock(),
        ids=UlidGenerator(),
        environment=SystemEnvironmentProbe(repo=settings.repo_root),
        tracker=tracker,
        publisher=overrides.publisher or UnconfiguredPublisher(),
        curator=overrides.curator or NoDialectsCurator(),
        gateways=dict(overrides.gateways),
        services=dict(overrides.services),
        min_free_bytes=settings.min_free_bytes,
    )
    return Application(settings, Kernel.build(ports), database, store, ledger)
