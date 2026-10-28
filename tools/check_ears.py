"""Structural check for requirements/*.ears until the TypeScript compiler lands.

Checks: YAML shape, unique ids, known patterns and statuses, and the EARS keyword
that each pattern requires. Run with: uv run --with pyyaml python tools/check_ears.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "requirements"
KEYWORDS = {
    "ubiquitous": re.compile(r"\bshall\b"),
    "event": re.compile(r"^When\b.*\bshall\b"),
    "state": re.compile(r"^While\b.*\bshall\b"),
    "unwanted": re.compile(r"^If\b.*\bthen\b.*\bshall\b"),
    "optional": re.compile(r"^Where\b.*\bshall\b"),
    "complex": re.compile(r"^(While|When|Where|If)\b.*\bshall\b"),
}
STATUSES = {"draft", "approved", "retired"}
VERIFICATIONS = {"test", "analysis", "inspection", "demonstration"}


def main() -> int:
    seen: dict[str, Path] = {}
    errors: list[str] = []
    for path in sorted(ROOT.rglob("*.ears")):
        doc = yaml.safe_load(path.read_text())
        for key in ("capability", "owner", "requirements"):
            if key not in doc:
                errors.append(f"{path}: missing {key}")
        for req in doc.get("requirements", []):
            rid = req.get("id", "?")
            if rid in seen:
                errors.append(f"{path}: duplicate id {rid} (also in {seen[rid]})")
            seen[rid] = path
            pattern = req.get("pattern")
            if pattern not in KEYWORDS:
                errors.append(f"{rid}: unknown pattern {pattern}")
            elif not KEYWORDS[pattern].search(req.get("text", "")):
                errors.append(f"{rid}: text does not follow the {pattern} EARS template")
            if req.get("status") not in STATUSES:
                errors.append(f"{rid}: unknown status {req.get('status')}")
            if req.get("verification") not in VERIFICATIONS:
                errors.append(f"{rid}: unknown verification {req.get('verification')}")
    for error in errors:
        print(f"FAIL {error}")
    print(f"{len(seen)} requirements checked, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
