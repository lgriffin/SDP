from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sdp.kernel.domain.canonical import canonical_json, es_number, hash_document
from sdp.kernel.domain.errors import InvariantViolation

pytestmark = pytest.mark.tier("T1")
VECTORS = Path(__file__).resolve().parents[4] / "contracts" / "fixtures" / "canonical" / "vectors.json"


@pytest.mark.req("CTR-003")
@pytest.mark.parametrize("vector", json.loads(VECTORS.read_text()), ids=lambda v: v["name"])
def test_canonical_vectors_reproduce_byte_for_byte(vector: dict[str, Any]) -> None:
    assert canonical_json(vector["input"]) == vector["canonical"]
    assert hash_document(vector["input"]) == vector["sha256"]


@pytest.mark.parametrize(
    ("value", "text"),
    [(1.0, "1"), (0.1, "0.1"), (1e21, "1e+21"), (1e-7, "1e-7"), (1e-6, "0.000001"), (-0.0, "0"), (123.456, "123.456")],
)
def test_numbers_follow_ecmascript(value: float, text: str) -> None:
    assert es_number(value) == text


def test_keys_sort_by_utf16_code_units() -> None:
    assert canonical_json({"\U0001f600": 1, "￿": 2}) == '{"\U0001f600":1,"￿":2}'


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 2**60, {1: "x"}, object()])
def test_non_json_values_are_refused(bad: object) -> None:
    with pytest.raises(InvariantViolation):
        canonical_json(bad)


@pytest.mark.req("CTR-003")
def test_numpy_floats_are_written_as_the_numbers_they_hold() -> None:
    # numpy 2 decorates repr() as np.float64(...); canonical text must not depend on it
    assert es_number(np.float64(2.5)) == "2.5"
    assert canonical_json({"slope": np.float64(0.1), "rms": [np.float64(1e-7)]}) == '{"rms":[1e-7],"slope":0.1}'
