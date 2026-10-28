"""AssembleDossier: only from a validated experiment (§5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sdp.kernel.application.experiments import ManifestWriter, _persist
from sdp.kernel.domain import events as ev
from sdp.kernel.domain.dossier import Dossier
from sdp.kernel.ports.determinism import Clock, IdGenerator
from sdp.kernel.ports.driving import AssembleDossierCommand
from sdp.kernel.ports.repositories import UnitOfWork


@dataclass
class AssembleDossierUseCase:
    uow: Callable[[], UnitOfWork]
    clock: Clock
    ids: IdGenerator
    manifests: ManifestWriter

    def execute(self, command: AssembleDossierCommand) -> Dossier:
        with self.uow() as uow:
            experiment = uow.experiments.get(command.experiment_id)
            if experiment is None:
                raise KeyError(command.experiment_id)
            experiment.limitations = list(command.limitations)
            _persist(uow, experiment, self.manifests)
            dossier = Dossier.assemble(
                dossier_id=self.ids.new("dos"),
                experiment=experiment,
                limitations=command.limitations,
                figures=command.figures,
                sections=command.sections,
            )
            uow.dossiers.save(dossier)
            uow.events.append(
                [ev.DossierAssembled(dossier.dossier_id, self.clock.now(), {"experiment_id": experiment.experiment_id})]
            )
            uow.commit()
        return dossier
