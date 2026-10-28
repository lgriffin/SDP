"""A composition root wiring the kernel to fakes only (tier T4)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sdp.kernel.application.acquisition import RetryPolicy
from sdp.kernel.application.investigate import InvestigatePorts
from sdp.kernel.application.kernel import Kernel, KernelPorts
from sdp.kernel.ports.archives import ArchiveGateway
from sdp.kernel.ports.integration import AdapterCompatibilitySource
from sdp.kernel.science.protocol import ScientificProtocol
from sdp.testing.fakes import (
    FakeDoiMinter,
    FakeEnvironmentProbe,
    FixedClock,
    IdentityCurator,
    InMemoryArtifactStore,
    InMemoryDatabase,
    InMemoryLedger,
    InMemoryProtocolRegistry,
    NullTracker,
    SequentialIds,
    SynchronousExecutor,
)
from sdp.testing.synthetic import SyntheticLinearProtocol


@dataclass
class InMemoryKernel:
    kernel: Kernel
    db: InMemoryDatabase
    store: InMemoryArtifactStore
    ledger: InMemoryLedger
    executor: SynchronousExecutor
    publisher: FakeDoiMinter
    clock: FixedClock


def build_in_memory_kernel(
    *protocols: ScientificProtocol,
    gateways: Mapping[str, ArchiveGateway] | None = None,
    services: Mapping[str, Any] | None = None,
    capacity: int | None = None,
    min_free_bytes: int = 0,
    investigate: InvestigatePorts | None = None,
    compatibility: AdapterCompatibilitySource | None = None,
) -> InMemoryKernel:
    clock = FixedClock()
    db = InMemoryDatabase()
    store = InMemoryArtifactStore(capacity=capacity)
    ledger = InMemoryLedger(clock=clock)
    executor = SynchronousExecutor()
    publisher = FakeDoiMinter()
    ports = KernelPorts(
        uow=db.unit_of_work,
        store=store,
        ledger=ledger,
        protocols=InMemoryProtocolRegistry.of(*(protocols or (SyntheticLinearProtocol(),))),
        executor=executor,
        clock=clock,
        ids=SequentialIds(),
        environment=FakeEnvironmentProbe(),
        tracker=NullTracker(),
        publisher=publisher,
        curator=IdentityCurator(),
        gateways=dict(gateways or {}),
        services=dict(services or {}),
        retry=RetryPolicy(max_attempts=3, base_delay_s=0.0),
        min_free_bytes=min_free_bytes,
        investigate=investigate,
        compatibility=compatibility,
    )
    return InMemoryKernel(Kernel.build(ports), db, store, ledger, executor, publisher, clock)
