"""T2: the set dossier's README says, for each candidate, which survey comparisons disagreed with Gaia
and by how much (INV-029)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from sdp.kernel.application.set_bundle import _difference, contradiction_lines

pytestmark = [pytest.mark.tier("T2"), pytest.mark.req("INV-029")]

KS = {"channel": "2mass:Ks", "kind": "band", "residual_mag": -0.52, "z": -8.13}
POSITION = {
    "channel": "2mass:position",
    "kind": "position",
    "offset_mas": [740.0, -20.0],
    "field_offset_mas": [40.0, -25.0],
    "z": 6.2,
}
J = {"channel": "2mass:J", "kind": "band", "residual_mag": 0.01, "z": 0.2}


def dossier(key: str, *results: dict[str, Any]) -> tuple[bytes, str, str]:
    return json.dumps({"test_results": list(results)}).encode(), "application/json", "dossier"


def result(
    key: str, p: float | None, flagged: list[str], comparisons: list[dict[str, Any]], pattern: str
) -> dict[str, Any]:
    return {
        "test_name": "cross-survey-contradiction",
        "subject": {"object_key": key},
        "p_value": p,
        "details": {"flagged": flagged, "comparisons": comparisons, "pattern": pattern, "pattern_reading": "reading"},
    }


def candidate(n: int, key: str) -> Any:
    return SimpleNamespace(investigation_id=f"inv-{n}", candidate=SimpleNamespace(key=key))


def test_each_flagged_comparison_is_listed_under_its_candidate() -> None:
    files = {
        "dossiers/inv-1.json": dossier("a", result("a", 1e-9, ["2mass:Ks"], [KS, J], "one-band")),
        "dossiers/inv-2.json": dossier("b", result("b", None, [], [], "not-assessable")),
        "dossiers/inv-3.json": dossier("c", {"test_name": "parallax-consistency", "subject": {"object_key": "c"}}),
    }
    lines = contradiction_lines([candidate(1, "a"), candidate(2, "b"), candidate(3, "c"), candidate(4, "d")], files)
    assert lines == [
        "- `a`: one-band, p = 1e-09; reading",
        "  - 2mass:Ks: 0.52 mag brighter than Gaia predicts, z = -8.1",
        "- `b`: not assessable; reading",
    ]


def test_another_star_s_result_in_the_dossier_is_not_this_candidate_s() -> None:
    files = {"dossiers/inv-1.json": dossier("a", result("other", 1e-9, ["2mass:Ks"], [KS], "one-band"))}
    assert contradiction_lines([candidate(1, "a")], files) == []


def test_a_difference_reads_in_magnitudes_or_mas_after_the_field_s_offset() -> None:
    assert _difference(KS) == "0.52 mag brighter than Gaia predicts"
    assert _difference({**KS, "residual_mag": 0.4}) == "0.40 mag fainter than Gaia predicts"
    assert _difference(POSITION) == "700 mas from where Gaia's motion puts it (+700 east, +5 north)"
    assert _difference({"kind": "position", "offset_mas": [None, None]}) == "no measurement"
    assert _difference({"kind": "position", "offset_mas": [3.0, 4.0]}).startswith("5 mas")
