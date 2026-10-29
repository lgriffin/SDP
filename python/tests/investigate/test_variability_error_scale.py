"""The variability test's error scale (INT-023): NEOWISE quotes single-exposure errors 1.2 to 1.4
times smaller than the scatter of every star in a field, constant or not, so a profile can scale them
by a fixed factor or by what the field's own stars show."""

from __future__ import annotations

import math

import numpy as np
import pytest

from sdp_investigate.base import Features, Scored
from sdp_investigate.photometry import MIN_FIELD_CURVES, Variability, error_scale

pytestmark = [pytest.mark.tier("T1"), pytest.mark.req("INT-023")]


def curve(rng: np.random.Generator, n: int, quoted: float, true: float, amplitude: float = 0.0):  # type: ignore[no-untyped-def]
    t = np.sort(rng.uniform(56700.0, 60300.0, n))
    mag = 10.0 + amplitude * np.sin(2 * np.pi * t / 200.0) + rng.normal(0.0, true, n)
    return t, mag, np.full(n, quoted)


def pv(score: Scored) -> float:
    assert score.p_value is not None
    return score.p_value


def field(seed: int = 1) -> Features:
    """Eight constant stars whose scatter is 1.3 times their quoted errors, and one that varies."""
    rng = np.random.default_rng(seed)
    curves = [curve(rng, 200, 0.02, 0.026) for _ in range(8)]
    curves.append(curve(rng, 200, 0.02, 0.026, amplitude=0.05))
    return Features(tuple(f"s{i}" for i in range(9)), {"curves": curves})


def test_quoted_errors_flag_every_constant_star_and_field_calibration_flags_only_the_variable() -> None:
    features = field()
    raw = Variability().evaluate(features, {}, np.random.default_rng(0))
    assert sum(pv(s) < 0.01 for s in raw[:8]) >= 7, "errors 1.3 times too small make everything variable"
    assert all("error_scale" not in s.details for s in raw)
    calibrated = Variability().evaluate(features, {"error_scale": "field"}, np.random.default_rng(0))
    scale = calibrated[0].details["error_scale"]
    assert 1.2 < scale < 1.45 and {s.details["error_scale"] for s in calibrated} == {scale}
    assert sum(pv(s) < 0.01 for s in calibrated[:8]) <= 1
    assert pv(calibrated[8]) < 1e-6, "a real 0.05-mag variation still stands out"
    assert calibrated[8].statistic == pytest.approx(raw[8].statistic / scale**2)


def test_a_fixed_scale_divides_chi_squared_by_its_square() -> None:
    features = field()
    raw = Variability().evaluate(features, {}, np.random.default_rng(0))
    scaled = Variability().evaluate(features, {"error_scale": 2.0}, np.random.default_rng(0))
    for r, s in zip(raw, scaled, strict=True):
        assert s.statistic == pytest.approx(r.statistic / 4.0) and s.details["error_scale"] == 2.0
    assert Variability().evaluate(features, {"error_scale": 1.0}, np.random.default_rng(0)) == raw


def test_the_field_scale_needs_enough_curves_and_never_shrinks_errors() -> None:
    rng = np.random.default_rng(2)
    noisy = [curve(rng, 50, 0.02, 0.04) for _ in range(MIN_FIELD_CURVES)]
    assert error_scale(noisy[: MIN_FIELD_CURVES - 1], "field") == 1.0
    assert error_scale(noisy, "field") == pytest.approx(2.0, rel=0.15)
    quiet = [curve(rng, 50, 0.02, 0.01) for _ in range(MIN_FIELD_CURVES)]
    assert error_scale(quiet, "field") == 1.0, "a field quieter than its errors leaves them alone"
    one_point = [(np.array([1.0]), np.array([10.0]), np.array([0.1]))] * 5
    assert error_scale(one_point, "field") == 1.0
    reduced = sorted(
        float(np.sum(((m - np.average(m, weights=1 / e**2)) / e) ** 2)) / (len(m) - 1) for _, m, e in noisy
    )
    assert error_scale(noisy, "field") == pytest.approx(math.sqrt(reduced[1]))


@pytest.mark.parametrize("setting", [0.0, -1.0, float("nan"), float("inf"), "auto"])
def test_a_scale_that_is_not_positive_or_field_is_refused(setting: object) -> None:
    with pytest.raises(ValueError, match=r"^error_scale must be positive or 'field', got "):
        error_scale([], setting)


def test_null_fields_carry_the_noise_the_field_shows() -> None:
    features = field()
    test = Variability()
    null = test.null_features(features, {"error_scale": "field"}, np.random.default_rng(5))
    scores = test.evaluate(null, {"error_scale": "field"}, np.random.default_rng(0))
    assert sum(pv(s) < 0.01 for s in scores) <= 1, "a null star is constant against the same scale"
    _, mag, err = null.arrays["curves"][0]
    assert np.array_equal(err, features.arrays["curves"][0][2]), "the quoted errors are kept"
    assert float(np.std(mag)) > 1.15 * 0.02
