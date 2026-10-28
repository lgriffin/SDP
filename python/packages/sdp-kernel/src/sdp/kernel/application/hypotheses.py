"""RegisterHypothesis (KRN-001)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sdp.kernel.domain import events as ev
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.ports.determinism import Clock
from sdp.kernel.ports.driving import RegisterHypothesisCommand
from sdp.kernel.ports.repositories import UnitOfWork


@dataclass
class RegisterHypothesisUseCase:
    uow: Callable[[], UnitOfWork]
    clock: Clock

    def execute(self, command: RegisterHypothesisCommand) -> Hypothesis:
        """Create version 1, or a new version when the hypothesis exists; never overwrite."""
        with self.uow() as uow:
            current = uow.hypotheses.get(command.hypothesis_id)
            if current is None:
                hypothesis = Hypothesis(
                    hypothesis_id=command.hypothesis_id,
                    version=1,
                    statement=command.statement,
                    predictions=command.predictions,
                    evaluation_criteria=command.evaluation_criteria,
                )
            else:
                hypothesis = current.revise(
                    statement=command.statement,
                    predictions=command.predictions,
                    evaluation_criteria=command.evaluation_criteria,
                )
            uow.hypotheses.add(hypothesis)
            uow.events.append(
                [ev.HypothesisRegistered(hypothesis.hypothesis_id, self.clock.now(), {"version": hypothesis.version})]
            )
            uow.commit()
        return hypothesis
