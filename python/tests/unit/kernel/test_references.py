"""T1: reference pulls as values (INV-024): moving a target along its motion, matching within a cone,
and the invariants and documents of a pull."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.references import ReferencePull, ReferenceTarget, matches_near, separation_arcsec

pytestmark = [pytest.mark.tier("T1"), pytest.mark.req("INV-024")]

AT = datetime(2026, 10, 31, tzinfo=UTC)
TARGET = ReferenceTarget(150.0, 30.0, 2016.0, 1000.0, -2000.0)


def test_a_target_moves_along_its_proper_motion_to_another_epoch() -> None:
    ra, dec = TARGET.at(2006.0)
    # ten years back: 10" west and 20" north, in true arcseconds on the sky
    assert separation_arcsec(150.0, 30.0, ra, 30.0) == pytest.approx(10.0, rel=1e-6)
    assert ra < 150.0
    assert (dec - 30.0) * 3600 == pytest.approx(20.0, rel=1e-9)
    assert TARGET.at(None) == (150.0, 30.0)
    assert ReferenceTarget(150.0, 30.0).at(2000.0) == (150.0, 30.0)


def test_matches_are_those_within_the_cone_nearest_first_after_moving() -> None:
    then = TARGET.at(2000.0)
    rows: list[dict[str, Any]] = [
        {"source_key": "b", "ra_deg": 150.0, "dec_deg": 30.0 + 2 / 3600},
        {"source_key": "a", "ra_deg": then[0], "dec_deg": then[1], "epoch_jyear": 2000.0},
        {"source_key": "far", "ra_deg": 150.0, "dec_deg": 30.01},
        {"source_key": "no position", "ra_deg": None, "dec_deg": 30.0},
    ]
    found = matches_near(TARGET, rows, 5.0)
    assert [m["source_key"] for m in found] == ["a", "b"]
    assert found[0]["separation_arcsec"] == pytest.approx(0.0, abs=1e-6)
    assert found[1]["separation_arcsec"] == pytest.approx(2.0, rel=1e-6)
    assert len(matches_near(TARGET, [rows[1]] * 30, 5.0, limit=10)) == 10


def pull(**changes: object) -> ReferencePull:
    base: dict[str, object] = {
        "source_id": "simbad",
        "title": "SIMBAD",
        "release_label": "simbad:live",
        "citation": "Wenger et al. 2000",
        "radius_arcsec": 5.0,
        "target": TARGET,
        "asked_at": AT,
        "actor": "leigh",
        "status": "found",
        "matches": ({"source_key": "x", "separation_arcsec": 0.4, "parallax_mas": float("nan")},),
        "raw_artifact_id": "sha256:" + "0" * 64,
    }
    return ReferencePull(**{**base, **changes})  # type: ignore[arg-type]


def test_a_pull_round_trips_through_its_document_with_nan_as_null() -> None:
    doc = pull().to_document()
    assert doc["matches"][0]["parallax_mas"] is None
    assert ReferencePull.from_document(doc).to_document() == doc


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"status": "maybe"}, "is one of"),
        ({"actor": " "}, "who asked"),
        ({"radius_arcsec": 0.0}, "radius"),
        ({"radius_arcsec": 601.0}, "radius"),
        ({"status": "none"}, "exactly when"),
        ({"matches": ()}, "exactly when"),
        ({"status": "failed", "matches": (), "error": None}, "says why"),
    ],
)
def test_a_pull_that_breaks_an_invariant_is_refused(changes: dict[str, object], message: str) -> None:
    with pytest.raises(InvariantViolation, match=message):
        pull(**changes)


def test_a_target_needs_a_sky_position() -> None:
    with pytest.raises(InvariantViolation):
        ReferenceTarget(float("nan"), 0.0)
    with pytest.raises(InvariantViolation):
        ReferenceTarget(10.0, 91.0)
