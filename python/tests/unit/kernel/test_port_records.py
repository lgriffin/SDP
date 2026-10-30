"""T1: the records the kernel ports define: a ledger entry's documents and hash, read back from its
document, and the message a failed task carries (§6, ADR-004).

The ledger behaviour is in the contract suites (tests/contract); these pin the details mutation testing showed
nothing else checked (docs/testing.md, "Killing a survivor")."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sdp.kernel.domain.canonical import hash_document
from sdp.kernel.ports.compute import TaskFailed
from sdp.kernel.ports.ledger import LedgerEntry

RECORDED = datetime(2026, 10, 30, 8, 45, 12, 250000, tzinfo=UTC)


def _entry(**changes: Any) -> LedgerEntry:
    values: dict[str, Any] = {
        "entry_id": "led-7",
        "sequence": 7,
        "kind": "approval",
        "subject_id": "plan-3",
        "subject_hash": "sha256:" + "ab" * 32,
        "recorded_at": RECORDED,
        "signer": "leigh",
        "signature_method": "ed25519",
        "signature": "c2lnbmF0dXJl",
        "previous_entry_hash": "sha256:" + "cd" * 32,
        "payload": {"budget_gb": 4.5, "items": ["a", "b"]},
    }
    values.update(changes)
    return LedgerEntry(**values)


UNSIGNED = {
    "schema_version": "1.0.0",
    "entry_id": "led-7",
    "sequence": 7,
    "kind": "approval",
    "subject_id": "plan-3",
    "subject_hash": "sha256:" + "ab" * 32,
    "recorded_at": "2026-10-30T08:45:12.250000Z",
    "signer": "leigh",
    "previous_entry_hash": "sha256:" + "cd" * 32,
    "payload": {"budget_gb": 4.5, "items": ["a", "b"]},
}
SIGNED = {**UNSIGNED, "signature": {"method": "ed25519", "value": "c2lnbmF0dXJl"}}


def test_the_unsigned_document_is_everything_but_the_signature() -> None:
    assert _entry().unsigned_document() == UNSIGNED


def test_the_document_adds_the_signature_method_and_value() -> None:
    assert _entry().to_document() == SIGNED


def test_the_entry_hash_is_the_hash_of_the_signed_document() -> None:
    assert _entry().entry_hash() == hash_document(SIGNED)
    assert _entry().entry_hash() != _entry(signature="b3RoZXI=").entry_hash()


def test_an_entry_reads_back_from_its_document() -> None:
    entry = LedgerEntry.from_document(SIGNED)
    assert entry == _entry()
    assert entry.recorded_at.utcoffset() == timedelta(0)
    assert type(entry.payload) is dict


def test_the_first_entry_has_no_previous_hash_and_reads_back_so() -> None:
    first = _entry(sequence=1, previous_entry_hash=None, kind="predeclaration")
    assert LedgerEntry.from_document(first.to_document()) == first


def test_a_document_without_a_payload_reads_back_with_an_empty_one() -> None:
    doc = {k: v for k, v in SIGNED.items() if k != "payload"}
    assert LedgerEntry.from_document(doc).payload == {}


def test_a_sequence_written_as_text_reads_back_as_a_number() -> None:
    assert LedgerEntry.from_document({**SIGNED, "sequence": "7"}).sequence == 7


def test_a_failed_task_names_the_task_and_its_cause() -> None:
    cause = ValueError("grid has no points")
    failure = TaskFailed("fit:H3", cause, {"attempt": 2})
    assert str(failure) == "task fit:H3 failed: ValueError: grid has no points"
    assert failure.task_id == "fit:H3"
    assert failure.cause is cause
    assert failure.diagnostics == {"attempt": 2}


def test_a_failed_task_keeps_its_own_copy_of_the_diagnostics() -> None:
    diagnostics = {"attempt": 2}
    failure = TaskFailed("fit:H3", RuntimeError("x"), diagnostics)
    diagnostics["attempt"] = 3
    assert failure.diagnostics == {"attempt": 2}
    with pytest.raises(TaskFailed, match=r"^task t failed: KeyError: 'k'$"):
        raise TaskFailed("t", KeyError("k"), {})
