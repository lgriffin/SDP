"""Canonical JSON and content hashing.

Canonical JSON follows RFC 8785 (JCS): object keys sorted by UTF-16 code units, no
insignificant whitespace, strings escaped as ECMAScript's JSON.stringify does, and numbers
formatted with the ECMAScript Number-to-String algorithm. The TypeScript harness implements
the same function, and contracts/fixtures/canonical/vectors.json pins both to identical bytes.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from sdp.kernel.domain.errors import InvariantViolation

SHA256_PREFIX = "sha256:"
_MAX_SAFE_INTEGER = 2**53


def es_number(value: float) -> str:
    """Format a finite float exactly as ECMAScript's Number.prototype.toString does."""
    if not math.isfinite(value):
        raise InvariantViolation("canonical JSON cannot carry NaN or infinity")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    text = repr(abs(value))
    mantissa, _, exponent = text.partition("e")
    integer_part, _, fraction_part = mantissa.partition(".")
    raw = integer_part + fraction_part
    digits = raw.lstrip("0")
    point = len(integer_part) + int(exponent or "0") - (len(raw) - len(digits))
    digits = digits.rstrip("0") or "0"
    k = len(digits)
    if k <= point <= 21:
        out = digits + "0" * (point - k)
    elif 0 < point <= 21:
        out = digits[:point] + "." + digits[point:]
    elif -6 < point <= 0:
        out = "0." + "0" * (-point) + digits
    else:
        e = point - 1
        out = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + out


def canonical_json(value: Any) -> str:
    """Serialise a JSON-compatible value to its canonical text."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise InvariantViolation("integers beyond 2**53 do not survive a JavaScript reader")
        return str(value)
    if isinstance(value, float):
        return es_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Mapping):
        keys = sorted(value, key=lambda k: str(k).encode("utf-16-be"))
        for key in keys:
            if not isinstance(key, str):
                raise InvariantViolation(f"canonical JSON keys must be strings, got {type(key).__name__}")
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical_json(value[k]) for k in keys) + "}"
    if isinstance(value, Sequence):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    raise InvariantViolation(f"{type(value).__name__} is not JSON-compatible")


def sha256_id(data: bytes) -> str:
    """The content identity used everywhere in SDP: ``sha256:<hex>``."""
    return SHA256_PREFIX + hashlib.sha256(data).hexdigest()


def hash_document(value: Any) -> str:
    """Hash of a JSON-compatible document over its canonical bytes."""
    return sha256_id(canonical_json(value).encode("utf-8"))


def is_sha256_id(text: str) -> bool:
    if not text.startswith(SHA256_PREFIX) or len(text) != len(SHA256_PREFIX) + 64:
        return False
    return all(c in "0123456789abcdef" for c in text[len(SHA256_PREFIX) :])
