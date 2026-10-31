"""T4 records for cross-survey-contradiction (INV-026): the exact documents a run and a null run make
on the synthetic field, pinned by digest, and the shape every result and comparison has.

A digest over rounded values (9 significant figures) catches any change to what the test computes,
while staying put across machines whose last floating-point bits differ. A deliberate change to the
method changes the digests: update them in the same commit and say why.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np
import pytest
from scipy import stats
from tests.investigate.test_cross_survey_contradiction import FADED, PAIR, THRESHOLDS, context, field

from sdp_investigate.contradiction import PATTERNS, CrossSurveyContradiction

pytestmark = [pytest.mark.tier("T4"), pytest.mark.req("INV-026")]

RESULT_KEYS = {
    "worst_channel",
    "comparisons_counted",
    "comparisons_set_aside",
    "flagged",
    "pattern",
    "pattern_reading",
    "comparisons",
}
COMMON = {
    "channel",
    "survey",
    "kind",
    "counted",
    "set_aside",
    "epoch_gap_years",
    "reported_sigma",
    "calibration_sigma",
    "total_sigma",
    "z",
    "unit",
}
POSITION_KEYS = COMMON | {"offset_mas", "field_offset_mas", "residual_mas"}
BAND_KEYS = COMMON | {"observed_mag", "predicted_mag", "field_offset_mag", "residual_mag"}


def rounded(value: Any) -> Any:
    if isinstance(value, float):
        return float(f"{value:.9g}") if math.isfinite(value) else repr(value)
    if isinstance(value, dict):
        return {k: rounded(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [rounded(v) for v in value]
    return value


def digest(doc: Any) -> str:
    return hashlib.sha256(json.dumps(rounded(doc), sort_keys=True).encode()).hexdigest()


def run() -> list[Any]:
    return list(CrossSurveyContradiction().run(field(), THRESHOLDS, context()))


def test_every_result_and_comparison_has_its_documented_shape() -> None:
    for r in run():
        assert set(r.details) == RESULT_KEYS
        assert r.details["pattern_reading"] == PATTERNS[r.details["pattern"]]
        counted = [c for c in r.details["comparisons"] if c["counted"]]
        assert r.details["comparisons_counted"] == len(counted)
        assert r.details["comparisons_set_aside"] == len(r.details["comparisons"]) - len(counted)
        for c in r.details["comparisons"]:
            assert set(c) == (POSITION_KEYS if c["kind"] == "position" else BAND_KEYS)
            assert c["unit"] == ("mas" if c["kind"] == "position" else "mag")
            assert c["counted"] == (c["set_aside"] is None)
        if counted:
            worst = max(counted, key=lambda c: abs(c["z"]))
            assert r.details["worst_channel"] == worst["channel"]
            assert r.statistic == pytest.approx(abs(worst["z"]))
            assert r.uncertainty == pytest.approx(worst["total_sigma"])
            p_one = 2 * stats.norm.sf(r.statistic)
            assert r.p_value == pytest.approx(1 - (1 - p_one) ** len(counted), rel=1e-9)
            assert {c["channel"] for c in counted if abs(c["z"]) >= 3.0} == set(r.details["flagged"])


def test_a_star_with_nothing_counted_reports_why_and_no_p_value() -> None:
    r = next(r for r in run() if r.subject.object_key == f"gaia:DR3:{PAIR[0]}")
    assert (r.statistic, r.p_value, r.effect_size, r.uncertainty) == (0.0, None, None, None)
    assert r.details["worst_channel"] is None and r.details["flagged"] == []
    assert r.details["comparisons_counted"] == 0
    assert r.details["comparisons_set_aside"] == len(r.details["comparisons"]) > 0


def test_the_faded_star_names_every_band_it_disagrees_in() -> None:
    r = next(r for r in run() if r.subject.object_key == f"gaia:DR3:{FADED}")
    assert r.details["flagged"] == sorted(r.details["flagged"])
    assert {"2mass:J", "2mass:H", "2mass:Ks"} <= set(r.details["flagged"])


def test_the_run_on_the_synthetic_field_is_pinned() -> None:
    docs = [(str(r.subject.object_key), r.statistic, r.p_value, r.effect_size, r.uncertainty, r.details) for r in run()]
    assert digest(docs) == RUN_DIGEST


def test_the_null_features_are_pinned() -> None:
    test = CrossSurveyContradiction()
    features = test.features(field(), THRESHOLDS, None)  # type: ignore[arg-type]
    null = test.null_features(features, THRESHOLDS, np.random.default_rng(4))
    assert null.keys == features.keys
    doc = [(c.name, c.observed.tolist(), c.reasons) for c in null.arrays["channels"]]
    assert digest(doc) == NULL_DIGEST
    # a redrawn field keeps the real field's offset and colour relation
    for real, drawn in zip(features.arrays["channels"], null.arrays["channels"], strict=True):
        assert drawn.name == real.name and drawn.observed.shape == real.observed.shape
        assert np.median(drawn.observed, axis=0) == pytest.approx(
            np.median(real.observed, axis=0), abs=0.5 if real.kind == "band" else 60.0
        )


RUN_DIGEST = "81555728922ceb23470e42f4c5a090f036976f2408fb7a222f92357f94ead55e"
NULL_DIGEST = "6fd194f31642bfa5cf90f62c6a77f86f6db9fb1f72e995cd7bd7070b98e10d71"
