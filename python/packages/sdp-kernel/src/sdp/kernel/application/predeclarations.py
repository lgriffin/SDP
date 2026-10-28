"""FreezePredeclaration (KRN-002, ADR-004)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sdp.kernel.domain import events as ev
from sdp.kernel.domain.predeclaration import FrozenPredeclaration
from sdp.kernel.domain.provenance import ProvGraph
from sdp.kernel.ports.determinism import Clock
from sdp.kernel.ports.driving import FreezePredeclarationCommand
from sdp.kernel.ports.ledger import Ledger, LedgerEntry
from sdp.kernel.ports.repositories import UnitOfWork


@dataclass
class FreezePredeclarationUseCase:
    uow: Callable[[], UnitOfWork]
    ledger: Ledger
    clock: Clock

    def execute(self, command: FreezePredeclarationCommand) -> tuple[FrozenPredeclaration, LedgerEntry]:
        """Hash over canonical JSON and append a signed ledger entry before returning.

        Freezing the same predeclaration twice returns the existing entry: intent is
        identified by its hash, and the ledger records it once.
        """
        frozen = command.predeclaration.freeze()
        entry = self.ledger.find(frozen.hash, "predeclaration")
        if entry is None:
            entry = self.ledger.append(
                kind="predeclaration",
                subject_id=command.predeclaration.predeclaration_id,
                subject_hash=frozen.hash,
                signer=command.signer,
                payload={
                    "protocol_id": command.predeclaration.protocol_id,
                    "protocol_version": command.predeclaration.protocol_version,
                },
            )
        with self.uow() as uow:
            if uow.predeclarations.get_frozen(frozen.hash) is None:
                uow.predeclarations.save_frozen(frozen, entry.entry_id)
                graph = ProvGraph()
                graph.entity(
                    frozen.hash, type="Predeclaration", predeclaration_id=command.predeclaration.predeclaration_id
                )
                graph.agent(command.signer, **{"prov:type": "prov:Person"})
                graph.relate("wasAttributedTo", frozen.hash, command.signer)
                uow.evidence.record(graph)
                uow.events.append(
                    [
                        ev.PredeclarationFrozen(
                            command.predeclaration.predeclaration_id,
                            self.clock.now(),
                            {"hash": frozen.hash, "ledger_entry_id": entry.entry_id},
                        )
                    ]
                )
            uow.commit()
        return frozen, entry
