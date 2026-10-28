"""Test profiles offered in the plugin: the versioned YAML documents in a directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_profiles(directory: str | Path) -> dict[str, dict[str, Any]]:
    """``profile_id@version`` → the profile document, for every ``*.yaml``/``*.yml`` in the directory.

    The kernel validates and hashes the document; here it is only read and passed on.
    """
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(directory).glob("*.y*ml")):
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict) or "profile_id" not in doc or "version" not in doc:
            continue
        key = f"{doc['profile_id']}@{doc['version']}"
        if key in found:
            raise ValueError(f"{key} is defined twice in {directory}")
        found[key] = doc
    return found
