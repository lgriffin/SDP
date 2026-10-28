"""NFR-008: the per-TaskKind nondeterminism notes are published in docs/determinism.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdp.testing.synthetic import SyntheticLinearProtocol
from sdp_relativity.protocol import Einstein1919Protocol
from sdp_stellar.protocol import StellarClusterProtocol

DOC = Path(__file__).resolve().parents[3] / "docs" / "determinism.md"
PROTOCOLS = (SyntheticLinearProtocol(), Einstein1919Protocol(), StellarClusterProtocol())


@pytest.mark.req("NFR-008")
@pytest.mark.tier("T0")
@pytest.mark.parametrize("protocol", PROTOCOLS, ids=lambda p: p.protocol_id.name)
def test_every_registered_task_kind_has_a_note_and_is_in_the_determinism_doc(protocol: object) -> None:
    text = DOC.read_text("utf-8")
    registry = protocol.task_kinds()  # type: ignore[attr-defined]
    for kind in registry.kinds():
        note = registry.nondeterminism(kind)
        assert note, f"{kind} has no nondeterminism note"
        assert f"| `{kind}` | {note} |" in text, f"docs/determinism.md is out of date for {kind}"
