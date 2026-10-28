"""T1: association records and the review state machine (v0.4.0 §4, INT-009, INT-011)."""

from __future__ import annotations

import pytest

from sdp.kernel.domain.association import (
    Association,
    AssociationGraph,
    ObjectHypothesis,
    PositionCovariance,
    ReviewState,
)
from sdp.kernel.domain.errors import InvalidTransition, InvariantViolation
from sdp.testing.fakes.integration import PlantedAssociations
from sdp.testing.integration import association_policy, planted_field

pytestmark = pytest.mark.tier("T1")


def graph() -> AssociationGraph:
    gaia, dets = planted_field()
    truth = {
        "wise-b": (("obj-b1", 0.55), ("obj-b2", 0.45)),
        "gaia-b1": (("obj-b1", 1.0),),
        "gaia-b2": (("obj-b2", 1.0),),
    }
    return PlantedAssociations(truth).associate([*gaia, *dets], association_policy())


@pytest.mark.req("INT-011")
def test_an_association_is_accepted_only_with_a_ledger_entry() -> None:
    g = graph()
    with pytest.raises(InvalidTransition, match="INT-011"):
        g.review("wise-b", "obj-b1", ReviewState.ACCEPTED, reviewer="leigh")
    accepted = g.review("wise-b", "obj-b1", ReviewState.ACCEPTED, reviewer="leigh", ledger_entry_id="ledger-7")
    assert [a.ledger_entry_id for a in accepted.accepted()] == ["ledger-7"]
    with pytest.raises(InvalidTransition):
        Association("r", "h", "m", "1.0.0", 0.5, 1.0, 0.9, None, (), state=ReviewState.ACCEPTED)


def test_review_states_follow_the_lifecycle() -> None:
    g = graph()
    with pytest.raises(InvariantViolation, match="reason"):
        g.review("wise-b", "obj-b2", ReviewState.REJECTED, reviewer="leigh")
    rejected = g.review("wise-b", "obj-b2", ReviewState.REJECTED, reviewer="leigh", reason="colour mismatch")
    with pytest.raises(InvalidTransition):
        rejected.review("wise-b", "obj-b2", ReviewState.ACCEPTED, reviewer="leigh", ledger_entry_id="x")
    superseded = rejected.review("wise-b", "obj-b2", ReviewState.SUPERSEDED, reviewer="kernel")
    assert superseded.for_record("wise-b")[1].state is ReviewState.SUPERSEDED


@pytest.mark.req("INT-009")
def test_competing_hypotheses_are_kept_and_ranked() -> None:
    g = graph()
    ranked = g.for_record("wise-b")
    assert [a.hypothesis_id for a in ranked] == ["obj-b1", "obj-b2"]
    assert ranked[0].competing == ("obj-b2",)
    assert g.ambiguous_records() == ("wise-b",)


def test_graph_identity_is_content_addressed() -> None:
    assert graph().graph_id == graph().graph_id
    assert graph().to_document()["graph_id"] == graph().graph_id


def test_records_and_hypotheses_need_positions_and_members() -> None:
    with pytest.raises(InvariantViolation):
        PositionCovariance(0.0, 0.1)
    with pytest.raises(InvariantViolation):
        ObjectHypothesis("h", ())
