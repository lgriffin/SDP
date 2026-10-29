"""Investigate steps. The seam is the use-case tier's: InvestigateField and TriageInvestigation on the
kernel wired to the fakes of ``sdp.testing``, as in ``tests/usecase/test_investigate.py``.

A step drives the kernel only through its driving ports and reads back only through
``query_investigations`` and the event log.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sdp.kernel.domain.errors import PredeclarationNotFrozen, PromotionRefused
from sdp.kernel.domain.gates import Comparator, Gate
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.investigation import Investigation
from sdp.kernel.domain.predeclaration import Predeclaration
from sdp.kernel.ports.driving import FreezePredeclarationCommand
from sdp.kernel.ports.investigate import InvestigateFieldCommand, InvestigationRun, TriageCommand
from sdp.testing.investigate import (
    PLANTED,
    gaia_archive,
    investigate_ports,
    observation_archive,
    synthetic_catalogue,
    synthetic_field,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

from .runner import given, then, when

ORDINALS = {"first": 0, "second": 1}


@dataclass
class InvestigateWorld:
    """One investigated synthetic field and what triage has done to its candidates."""

    k: InMemoryKernel
    run: InvestigationRun
    predeclaration_hash: str | None = None
    error: Exception | None = None

    def nth(self, ordinal: str) -> Investigation:
        return self.run.investigations[ORDINALS[ordinal]]

    def stored(self, ordinal: str) -> Investigation:
        assert self.k.kernel.query_investigations is not None
        investigation = self.k.kernel.query_investigations.investigation(self.nth(ordinal).investigation_id)
        assert investigation is not None
        return investigation

    def triage(self, ordinal: str, action: str, actor: str, **kw: Any) -> None:
        assert self.k.kernel.triage_investigation is not None
        try:
            self.k.kernel.triage_investigation.execute(
                TriageCommand(self.nth(ordinal).investigation_id, action, actor=actor, **kw)
            )
        except (PromotionRefused, PredeclarationNotFrozen) as error:
            self.error = error

    def events(self, name: str) -> list[Any]:
        return [e.event for e in self.k.db.state.events if e.event.name == name]


def world(ctx: dict[str, Any]) -> InvestigateWorld:
    investigate: InvestigateWorld = ctx["investigate"]
    return investigate


# Given


@given("the synthetic field has been investigated")
def _investigated(ctx: dict[str, Any]) -> None:
    catalogue = synthetic_catalogue()
    k = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=investigate_ports(catalogue=catalogue, observations=observation_archive(catalogue)),
    )
    assert k.kernel.investigate_field is not None
    run = k.kernel.investigate_field.execute(InvestigateFieldCommand(synthetic_field(), synthetic_profile(), seed=11))
    ctx["investigate"] = InvestigateWorld(k, run)


@given('a frozen confirmatory predeclaration names the release "{release}"')
def _confirmatory(ctx: dict[str, Any], release: str) -> None:
    w = world(ctx)
    predeclaration = Predeclaration(
        predeclaration_id=f"pre-confirm-{release}",
        protocol_id="uc01-stellar",
        protocol_version="1.0.0",
        title="Confirm the co-moving group",
        hypotheses=(Hypothesis("H-1", 1, "the group shares a space motion"),),
        gates=(Gate("STL-G-09", "membership", "membership", Comparator.GE, 0.5),),
        parameters={"confirmatory_evidence": [{"kind": "release", "value": release}]},
    )
    frozen, _ = w.k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(predeclaration, "leigh"))
    w.predeclaration_hash = frozen.hash


# When


@when('"{actor}" promotes the {ordinal} investigation without a predeclaration')
def _promote_bare(ctx: dict[str, Any], actor: str, ordinal: str) -> None:
    world(ctx).triage(ordinal, "promote", actor)


@when('"{actor}" promotes the {ordinal} investigation under that predeclaration')
def _promote(ctx: dict[str, Any], actor: str, ordinal: str) -> None:
    w = world(ctx)
    w.triage(ordinal, "promote", actor, predeclaration_hash=w.predeclaration_hash, experiment_id="exp-1")


@when('"{actor}" parks the {ordinal} investigation because "{reason}"')
def _park(ctx: dict[str, Any], actor: str, ordinal: str, reason: str) -> None:
    world(ctx).triage(ordinal, "park", actor, reason=reason)


@when('"{actor}" closes the {ordinal} investigation because "{reason}"')
def _close(ctx: dict[str, Any], actor: str, ordinal: str, reason: str) -> None:
    world(ctx).triage(ordinal, "close", actor, reason=reason)


# Then


@then("one open investigation exists for each planted star")
def _one_per_star(ctx: dict[str, Any]) -> None:
    run = world(ctx).run
    assert sorted(i.candidate.object_key or "" for i in run.investigations) == sorted(PLANTED)
    assert all(i.state.value == "open" for i in run.investigations)
    assert len(world(ctx).events("InvestigationOpened")) == len(PLANTED)


@then("the set records {objects:d} objects examined, {tests:d} test applied and {candidates:d} candidates opened")
def _multiplicity(ctx: dict[str, Any], objects: int, tests: int, candidates: int) -> None:
    multiplicity = world(ctx).run.manifest["multiplicity"]
    assert (multiplicity["objects_examined"], multiplicity["tests_applied"]) == (objects, tests)
    assert multiplicity["candidates_opened"] == candidates


@then('every investigation and every test result is labelled "{label}"')
def _labelled(ctx: dict[str, Any], label: str) -> None:
    for investigation in world(ctx).run.investigations:
        assert investigation.label == label and "verdict" not in investigation.to_document()
        assert investigation.test_results and all(r.label == label for r in investigation.test_results)


@then('every investigation dossier is labelled "{label}"')
def _dossiers_labelled(ctx: dict[str, Any], label: str) -> None:
    w = world(ctx)
    assert w.k.kernel.query_investigations is not None
    for investigation in w.run.investigations:
        dossier = w.k.kernel.query_investigations.dossier(investigation.investigation_id)
        assert dossier is not None and dossier["label"] == label


@then("no verdict was issued")
def _no_verdict(ctx: dict[str, Any]) -> None:
    assert not [e for e in world(ctx).k.db.state.events if e.event.name.startswith("Verdict")]


@then("the promotion is refused")
def _refused(ctx: dict[str, Any]) -> None:
    assert world(ctx).error is not None


@then('the promotion is refused because "{reason}"')
def _refused_because(ctx: dict[str, Any], reason: str) -> None:
    error = world(ctx).error
    assert isinstance(error, PromotionRefused) and reason in str(error), error


@then("{count:d} promotion refusal is recorded against the {ordinal} investigation")
def _refusals(ctx: dict[str, Any], count: int, ordinal: str) -> None:
    w = world(ctx)
    refusals = w.events("InvestigationPromotionRefused")
    assert [e.aggregate_id for e in refusals] == [w.nth(ordinal).investigation_id] * count


@then('the {ordinal} investigation\'s state is "{state}"')
def _state(ctx: dict[str, Any], ordinal: str, state: str) -> None:
    assert world(ctx).stored(ordinal).state.value == state


@then('the {ordinal} investigation is "{state}" because "{reason}"')
def _state_because(ctx: dict[str, Any], ordinal: str, state: str, reason: str) -> None:
    stored = world(ctx).stored(ordinal)
    assert stored.state.value == state and stored.transitions[-1].reason == reason


@then("the set still holds {count:d} investigations")
def _held(ctx: dict[str, Any], count: int) -> None:
    w = world(ctx)
    assert w.k.kernel.query_investigations is not None
    assert len(w.k.kernel.query_investigations.investigations(w.run.investigation_set.set_id)) == count


@then("the first and second investigations keep their dossiers")
def _kept(ctx: dict[str, Any]) -> None:
    w = world(ctx)
    assert w.k.kernel.query_investigations is not None
    for ordinal in ("first", "second"):
        assert w.k.kernel.query_investigations.dossier(w.nth(ordinal).investigation_id) is not None
