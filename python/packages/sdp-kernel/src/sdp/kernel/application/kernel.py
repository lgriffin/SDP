"""The Kernel facade: the nine use cases, built from driven ports by a composition root."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sdp.kernel.application.acquisition import AcquireDatasetUseCase, RetryPolicy
from sdp.kernel.application.dossiers import AssembleDossierUseCase
from sdp.kernel.application.experiments import (
    DefineExperimentUseCase,
    ManifestWriter,
    RunExperimentUseCase,
    ValidateExperimentUseCase,
)
from sdp.kernel.application.hypotheses import RegisterHypothesisUseCase
from sdp.kernel.application.investigate import (
    InvestigateFieldUseCase,
    InvestigatePorts,
    QueryInvestigationsUseCase,
    TriageInvestigationUseCase,
)
from sdp.kernel.application.predeclarations import FreezePredeclarationUseCase
from sdp.kernel.application.publication import PublishBundleUseCase
from sdp.kernel.application.queries import QueryEvidenceUseCase
from sdp.kernel.ports.archives import ArchiveGateway, DialectCurator
from sdp.kernel.ports.compute import ComputeExecutor, EnvironmentProbe
from sdp.kernel.ports.determinism import Clock, IdGenerator
from sdp.kernel.ports.evidence import ExperimentTracker
from sdp.kernel.ports.ledger import Ledger
from sdp.kernel.ports.plugins import ProtocolRegistry
from sdp.kernel.ports.publication import BundlePublisher
from sdp.kernel.ports.repositories import UnitOfWork
from sdp.kernel.ports.storage import ArtifactStore


@dataclass
class KernelPorts:
    """Every driven port the kernel needs, as wired by a composition root."""

    uow: Callable[[], UnitOfWork]
    store: ArtifactStore
    ledger: Ledger
    protocols: ProtocolRegistry
    executor: ComputeExecutor
    clock: Clock
    ids: IdGenerator
    environment: EnvironmentProbe
    tracker: ExperimentTracker
    publisher: BundlePublisher
    curator: DialectCurator
    gateways: Mapping[str, ArchiveGateway] = field(default_factory=dict)
    services: Mapping[str, Any] = field(default_factory=dict)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    min_free_bytes: int = 50 * 1024**3
    investigate: InvestigatePorts | None = None


@dataclass
class Kernel:
    ports: KernelPorts
    register_hypothesis: RegisterHypothesisUseCase
    freeze_predeclaration: FreezePredeclarationUseCase
    acquire_dataset: AcquireDatasetUseCase
    define_experiment: DefineExperimentUseCase
    run_experiment: RunExperimentUseCase
    validate_experiment: ValidateExperimentUseCase
    assemble_dossier: AssembleDossierUseCase
    publish_bundle: PublishBundleUseCase
    query_evidence: QueryEvidenceUseCase
    triage_investigation: TriageInvestigationUseCase | None = None
    query_investigations: QueryInvestigationsUseCase | None = None
    investigate_field: InvestigateFieldUseCase | None = None

    @classmethod
    def build(cls, ports: KernelPorts) -> Kernel:
        manifests = ManifestWriter(ports.store)
        kernel = cls(
            ports=ports,
            register_hypothesis=RegisterHypothesisUseCase(ports.uow, ports.clock),
            freeze_predeclaration=FreezePredeclarationUseCase(ports.uow, ports.ledger, ports.clock),
            acquire_dataset=AcquireDatasetUseCase(
                ports.uow,
                ports.gateways,
                ports.curator,
                ports.store,
                ports.clock,
                ports.retry,
                ports.min_free_bytes,
            ),
            define_experiment=DefineExperimentUseCase(
                ports.uow, ports.protocols, ports.store, ports.clock, ports.ids, ports.environment, manifests
            ),
            run_experiment=RunExperimentUseCase(
                ports.uow,
                ports.ledger,
                ports.protocols,
                ports.store,
                ports.executor,
                ports.clock,
                manifests,
                ports.tracker,
                ports.services,
            ),
            validate_experiment=ValidateExperimentUseCase(
                ports.uow, ports.protocols, ports.clock, manifests, ports.tracker
            ),
            assemble_dossier=AssembleDossierUseCase(ports.uow, ports.clock, ports.ids, manifests),
            publish_bundle=PublishBundleUseCase(
                ports.uow, ports.ledger, ports.store, ports.publisher, ports.clock, manifests
            ),
            query_evidence=QueryEvidenceUseCase(ports.uow, ports.ledger),
            triage_investigation=TriageInvestigationUseCase(ports.uow, ports.ledger, ports.clock),
            query_investigations=QueryInvestigationsUseCase(ports.uow, ports.store),
        )
        if ports.investigate is not None:
            kernel.investigate_field = InvestigateFieldUseCase(
                ports.uow,
                ports.store,
                ports.clock,
                ports.ids,
                ports.environment,
                ports.executor,
                kernel.acquire_dataset,
                ports.investigate,
            )
        return kernel
