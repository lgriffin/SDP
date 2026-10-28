"""Validate contracts/fixtures against contracts/schemas (JSON Schema 2020-12).

Every fixture under fixtures/valid/<schema>/ must be accepted. Every fixture under
fixtures/invalid/<schema>/ must be rejected, and the error must sit at the JSON
pointer named by its sibling ``.expect.json`` file. The canonical-JSON vectors are
re-hashed so the published hashes stay honest, and the draft predeclarations under
experiments/ are validated too.

Run with:  uv run --with jsonschema --with referencing python contracts/tools/check_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
FIXTURES = ROOT / "fixtures"


def load_registry() -> tuple[Registry, dict[str, dict[str, Any]]]:
    schemas: dict[str, dict[str, Any]] = {}
    resources = []
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        doc = json.loads(path.read_text())
        schemas[path.name.removesuffix(".schema.json")] = doc
        resource = Resource.from_contents(doc)
        resources.append((path.name, resource))
        resources.append((doc["$id"], resource))
    registry = Registry().with_resources(resources)
    return registry, schemas


def pointer(error: ValidationError) -> str:
    parts = [str(p) for p in error.absolute_path]
    if error.validator == "required":
        missing = error.message.split("'")[1]
        parts.append(missing)
    return "/" + "/".join(parts) if parts else ""


def error_pointers(validator: Draft202012Validator, instance: Any) -> set[str]:
    found: set[str] = set()

    def visit(error: ValidationError) -> None:
        found.add(pointer(error))
        for sub in error.context or []:
            visit(sub)

    for error in validator.iter_errors(instance):
        visit(error)
    return found


def es_number(x: float) -> str:
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    text = repr(abs(x))
    mant, exp = (text.split("e") + ["0"])[:2]
    ip, _, fp = mant.partition(".")
    raw = ip + fp
    digits = raw.lstrip("0")
    n = len(ip) + int(exp) - (len(raw) - len(digits))
    digits = digits.rstrip("0") or "0"
    k = len(digits)
    if k <= n <= 21:
        out = digits + "0" * (n - k)
    elif 0 < n <= 21:
        out = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        out = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + out


def canonical(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        return es_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical(value[k]) for k in keys) + "}"
    raise TypeError(type(value))


def main() -> int:
    registry, schemas = load_registry()
    failures: list[str] = []
    checked = 0
    for name, schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, registry=registry)
        for path in sorted((FIXTURES / "valid" / name).glob("*.json")):
            checked += 1
            errors = list(validator.iter_errors(json.loads(path.read_text())))
            if errors:
                failures.append(f"{path.relative_to(ROOT)} should be valid: {errors[0].message}")
        for path in sorted((FIXTURES / "invalid" / name).glob("*.json")):
            if path.name.endswith(".expect.json"):
                continue
            checked += 1
            expected = json.loads(path.with_name(path.name.removesuffix(".json") + ".expect.json").read_text())["path"]
            pointers = error_pointers(validator, json.loads(path.read_text()))
            if not pointers:
                failures.append(f"{path.relative_to(ROOT)} should be invalid")
            elif expected not in pointers:
                failures.append(f"{path.relative_to(ROOT)} rejected at {sorted(pointers)}, expected {expected}")
    predeclarations = Draft202012Validator(schemas["predeclaration"], registry=registry)
    for path in sorted((ROOT.parent / "experiments").glob("*/predeclaration.json")):
        checked += 1
        errors = list(predeclarations.iter_errors(json.loads(path.read_text())))
        if errors:
            failures.append(f"{path.relative_to(ROOT.parent)} is not a valid predeclaration: {errors[0].message}")
    for vector in json.loads((FIXTURES / "canonical" / "vectors.json").read_text()):
        checked += 1
        text = canonical(vector["input"])
        digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
        if text != vector["canonical"] or digest != vector["sha256"]:
            failures.append(f"canonical vector {vector['name']} does not reproduce")
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"{checked} fixtures checked, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
