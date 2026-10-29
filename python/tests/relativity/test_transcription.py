"""T1: transcribed rows keep their locator and convention into the observation bundle (REL-001)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from sdp.kernel.domain.historical import TranscribedRecord, VerificationRecord, is_verified
from sdp.kernel.science.plan import TaskContext
from sdp_relativity.analysis import transcription_audit
from sdp_relativity.measurement import Convention
from sdp_relativity.observations import parse_bundle
from sdp_relativity.transcription import bundle_from_records

pytestmark = pytest.mark.tier("T1")

AT = datetime(2026, 10, 28, tzinfo=UTC)


def record(record_id: str, locator: str, **fields: Any) -> TranscribedRecord:
    return TranscribedRecord(record_id, "dyson-1920", locator, fields, "leigh", AT)


def table() -> list[TranscribedRecord]:
    star = {"kind": "star", "instrument": "sobral-4in", "plate_id": "I", "x_arcmin": 30.0, "y_arcmin": 40.0}
    return [
        record("r0", "p. 300, footnote", kind="instrument", instrument="sobral-4in", solar_radius_arcmin=15.78),
        record(
            "r1",
            "p. 312, Table IV row 1",
            **star,
            star_id="11",
            dx_arcsec=-0.2,
            dy_arcsec=0.4,
            error_arcsec=0.12,
            convention="probable_error",
        ),
        record(
            "r2",
            "p. 312, Table IV row 2",
            **{**star, "x_arcmin": -50.0},
            star_id="5",
            dx_arcsec=0.3,
            dy_arcsec=None,
            error_arcsec=0.12,
            convention="probable_error",
        ),
        record(
            "r3",
            "p. 310, plate constants",
            kind="plate",
            instrument="sobral-4in",
            plate_id="I",
            check_scale=0.001,
            check_rotation=0.0,
            check_scale_sigma=0.002,
        ),
    ]


@pytest.mark.req("REL-001")
def test_every_star_row_carries_its_locator_and_convention() -> None:
    bundle = parse_bundle(bundle_from_records(table(), [], {"document": "dyson-1920"}))
    stars = bundle.instrument("sobral-4in").stars
    assert [(s.star_id, s.locator) for s in stars] == [
        ("11", "p. 312, Table IV row 1"),
        ("5", "p. 312, Table IV row 2"),
    ]
    assert all(s.convention is Convention.PROBABLE_ERROR for s in stars)
    assert stars[1].axes == (("x", 0.3),)
    assert bundle.records["r3"] == "p. 310, plate constants"
    assert bundle.instrument("sobral-4in").plates[0].check_scale_sigma == 0.002
    assert bundle.provenance == {"kind": "transcription", "document": "dyson-1920"}


@pytest.mark.req("REL-001")
def test_a_star_row_without_a_convention_is_refused() -> None:
    rows = table()
    rows[1] = record("r1", "p. 312, Table IV row 1", **{k: v for k, v in rows[1].fields.items() if k != "convention"})
    with pytest.raises(ValueError, match=r"r1 \(p\. 312, Table IV row 1\) lacks \['convention'\]"):
        bundle_from_records(rows, [])


def test_a_correction_from_a_second_pass_supersedes_the_transcription() -> None:
    corrections = [
        VerificationRecord("r1", "reader-2", AT, False, {"dx_arcsec": -0.25}),
        VerificationRecord("r2", "leigh", AT, False, {"dx_arcsec": 0.31}),  # one user: the transcriber re-checks
    ]
    bundle = parse_bundle(bundle_from_records(table(), corrections))
    stars = {s.star_id: s for s in bundle.instrument("sobral-4in").stars}
    assert stars["11"].dx_arcsec == -0.25
    assert stars["5"].dx_arcsec == 0.31


def test_a_correction_after_a_matching_pass_needs_a_matching_pass_of_its_own() -> None:
    record = next(r for r in table() if r.record_id == "r2")
    matched = VerificationRecord("r2", "leigh", AT, True)
    corrected = VerificationRecord("r2", "leigh", AT + timedelta(hours=1), False, {"dx_arcsec": 0.31})
    rechecked = VerificationRecord("r2", "leigh", AT + timedelta(hours=2), True)
    assert is_verified(record, (matched,))
    assert not is_verified(record, (corrected, matched)), "the corrected value was never re-checked"
    assert is_verified(record, (rechecked, corrected, matched))
    # The bundle carries each pass's time, so the audit reads each record's latest in any order.
    bundle = bundle_from_records(table(), [corrected, matched])
    assert [v["matches"] for v in bundle["verifications"]] == [True, False]
    reordered = {**bundle, "verifications": bundle["verifications"][::-1]}
    assert "sobral-4in/5" in transcription_audit({}, {}, cast(TaskContext, _Inputs(reordered)))["unverified"]


class _Inputs:
    def __init__(self, bundle: dict[str, Any]) -> None:
        self.bundle = bundle

    def service(self, name: str) -> dict[str, Any]:
        return {"observations": self.bundle}


def test_a_set_without_a_solar_radius_is_refused() -> None:
    with pytest.raises(ValueError, match="no solar radius"):
        bundle_from_records(table()[1:], [])
