from __future__ import annotations

import pytest

from sdp.kernel.domain.canonical import canonical_json, sha256_id
from sdp.kernel.ports.driving import FreezePredeclarationCommand, RegisterHypothesisCommand

from .conftest import Harness

pytestmark = pytest.mark.tier("T4")


@pytest.mark.req("KRN-002")
def test_freeze_appends_signed_ledger_entry(harness: Harness) -> None:
    predeclaration = harness.protocol.predeclare()
    frozen, entry = harness.k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(predeclaration, "leigh"))

    assert frozen.hash == sha256_id(canonical_json(predeclaration.to_document()).encode())
    assert entry.subject_hash == frozen.hash and entry.kind == "predeclaration" and entry.signer == "leigh"
    assert entry.signature and harness.k.ledger.find(frozen.hash, "predeclaration") == entry
    harness.k.ledger.verify()
    assert "PredeclarationFrozen" in harness.event_names()


@pytest.mark.req("KRN-002")
def test_freezing_twice_records_one_entry(harness: Harness) -> None:
    first = harness.freeze()
    second = harness.freeze()
    assert first.hash == second.hash
    assert len(harness.k.ledger.entries()) == 1
    assert harness.event_names().count("PredeclarationFrozen") == 1


@pytest.mark.req("KRN-001")
def test_editing_a_hypothesis_creates_a_new_version(harness: Harness) -> None:
    register = harness.k.kernel.register_hypothesis
    first = register.execute(RegisterHypothesisCommand("H-1", "light bends"))
    second = register.execute(RegisterHypothesisCommand("H-1", "light bends by 1.75 arcsec at the limb"))

    assert (first.version, second.version) == (1, 2)
    versions = harness.k.kernel.query_evidence.hypothesis_versions("H-1")
    assert [(h.version, h.statement) for h in versions] == [(1, first.statement), (2, second.statement)]
    assert [h.version for h in harness.k.kernel.query_evidence.hypotheses()] == [2]
