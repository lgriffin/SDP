"""Ledger contract: append-only, hash-chained, signed, tamper-evident (ADR-004)."""

from __future__ import annotations

import pytest

from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.ports.ledger import Ledger


class LedgerContract:
    @pytest.mark.req("KRN-002")
    def test_append_returns_a_signed_chained_entry(self, ledger: Ledger) -> None:
        first = ledger.append(
            kind="predeclaration", subject_id="p1", subject_hash=sha256_id(b"p1"), signer="leigh", payload={}
        )
        second = ledger.append(
            kind="approval", subject_id="e1", subject_hash=sha256_id(b"e1"), signer="rev", payload={"v": 1}
        )
        assert first.previous_entry_hash is None
        assert second.previous_entry_hash == first.entry_hash()
        assert second.sequence == first.sequence + 1
        assert first.signature and second.signature
        assert ledger.verify() == []

    @pytest.mark.req("KRN-003")
    def test_find_by_subject_hash_and_kind(self, ledger: Ledger) -> None:
        h = sha256_id(b"predeclaration")
        entry = ledger.append(kind="predeclaration", subject_id="p", subject_hash=h, signer="leigh", payload={})
        assert ledger.find(h, "predeclaration") == entry
        assert ledger.find(h, "approval") is None
        assert ledger.find(sha256_id(b"other"), "predeclaration") is None

    def test_entries_are_returned_in_order(self, ledger: Ledger) -> None:
        for i in range(3):
            ledger.append(
                kind="predeclaration", subject_id=f"p{i}", subject_hash=sha256_id(bytes([i])), signer="s", payload={}
            )
        assert [e.sequence for e in ledger.entries()] == [0, 1, 2]

    def test_entry_documents_are_schema_shaped(self, ledger: Ledger) -> None:
        entry = ledger.append(
            kind="predeclaration", subject_id="p", subject_hash=sha256_id(b"x"), signer="s", payload={"a": 1}
        )
        doc = entry.to_document()
        assert doc["recorded_at"].endswith("Z")
        assert set(doc) == {
            "schema_version",
            "entry_id",
            "sequence",
            "kind",
            "subject_id",
            "subject_hash",
            "recorded_at",
            "signer",
            "signature",
            "previous_entry_hash",
            "payload",
        }
