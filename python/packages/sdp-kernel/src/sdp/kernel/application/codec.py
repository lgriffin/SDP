"""A JSON codec for checkpoints and outputs. No pickle anywhere (§15).

Supported: JSON scalars, lists, tuples, string-keyed dicts, numpy arrays and scalars, astropy
Quantities and pyarrow Tables. Arrays travel as base64 .npy with allow_pickle=False; tables as
Arrow IPC streams; non-finite and integral floats as tagged strings, because canonical JSON
writes 2.0 as 2 and a resumed checkpoint must hand back a float, not an int.
"""

from __future__ import annotations

import base64
import io
import json
import math
from typing import Any

import astropy.units as u
import numpy as np
import pyarrow as pa

from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.domain.errors import InvariantViolation

_TAG = "__sdp__"
MEDIA_TYPE = "application/vnd.sdp.value+json"


def _array_payload(array: np.ndarray) -> str:
    if array.dtype.kind == "O":
        raise InvariantViolation("object arrays cannot be checkpointed; use lists")
    buf = io.BytesIO()
    np.save(buf, np.ascontiguousarray(array), allow_pickle=False)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def encode(value: Any) -> Any:
    """Turn a supported value into a JSON-compatible tree."""
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and not value.is_integer():
            return value
        return {_TAG: "float", "repr": repr(value)}
    if isinstance(value, u.Quantity):
        return {_TAG: "quantity", "unit": value.unit.to_string(), "value": encode(np.asarray(value.value))}
    if isinstance(value, np.ndarray):
        return {_TAG: "ndarray", "npy": _array_payload(value)}
    if isinstance(value, np.generic):
        return encode(value.item())
    if isinstance(value, pa.Table):
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, value.schema) as writer:
            writer.write_table(value)
        return {_TAG: "arrow", "ipc": base64.b64encode(sink.getvalue().to_pybytes()).decode("ascii")}
    if isinstance(value, tuple):
        return {_TAG: "tuple", "items": [encode(v) for v in value]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise InvariantViolation(f"checkpoint dict keys must be strings, got {type(key).__name__}")
            if key == _TAG:
                raise InvariantViolation(f"{_TAG} is a reserved key")
        return {k: encode(v) for k, v in value.items()}
    raise InvariantViolation(f"{type(value).__name__} cannot be checkpointed")


def decode(tree: Any) -> Any:
    if isinstance(tree, list):
        return [decode(v) for v in tree]
    if not isinstance(tree, dict):
        return tree
    tag = tree.get(_TAG)
    if tag is None:
        return {k: decode(v) for k, v in tree.items()}
    if tag == "float":
        return float(tree["repr"])
    if tag == "ndarray":
        return np.load(io.BytesIO(base64.b64decode(tree["npy"])), allow_pickle=False)
    if tag == "quantity":
        return u.Quantity(decode(tree["value"]), u.Unit(tree["unit"]))
    if tag == "arrow":
        with pa.ipc.open_stream(pa.py_buffer(base64.b64decode(tree["ipc"]))) as reader:
            return reader.read_all()
    if tag == "tuple":
        return tuple(decode(v) for v in tree["items"])
    raise InvariantViolation(f"unknown codec tag {tag!r}")


def dumps(value: Any) -> bytes:
    """Canonical bytes, so identical results checkpoint to identical artifact ids."""
    return canonical_json(encode(value)).encode("utf-8")


def loads(data: bytes) -> Any:
    return decode(json.loads(data))
