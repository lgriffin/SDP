"""The local folder sources a person has added, kept in ``<data>/local-sources.yaml`` (#71).

A source is read in place and never written to: everything SDP makes from it (the frame index, plate
solutions) goes under SDP's data folder, which must not lie inside the source, nor the source inside it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REGISTRY = "local-sources.yaml"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class LocalSourceError(ValueError):
    pass


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    """SDP's data folder: ``SDP_DATA`` (set by the SDP Command Prompt), else ``SDP_DESKTOP_DATA``, else
    the folder holding the artifact store (``SDP_ARTIFACT_ROOT``, as in ``D:\\SDP\\artifacts``)."""
    env = os.environ if env is None else env
    for key in ("SDP_DATA", "SDP_DESKTOP_DATA"):
        if env.get(key):
            return Path(env[key])
    if env.get("SDP_ARTIFACT_ROOT"):
        return Path(env["SDP_ARTIFACT_ROOT"]).parent
    raise LocalSourceError("set SDP_DATA (or SDP_ARTIFACT_ROOT) to say where SDP keeps its data")


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class LocalSource:
    name: str
    path: Path
    #: arcseconds per raw pixel, used when a frame's header gives no FOCALLEN and XPIXSZ
    scale_arcsec: float | None = None
    added_at: str = ""

    @property
    def source_id(self) -> str:
        return f"local:{self.name}"

    def output_dir(self, data: Path, kind: str) -> Path:
        """Where SDP writes what it makes from this source: ``<data>/<kind>/<name>`` (index, solutions)."""
        return data / kind / self.name

    def to_document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"path": str(self.path), "read_only": True, "added_at": self.added_at}
        if self.scale_arcsec is not None:
            doc["scale_arcsec"] = self.scale_arcsec
        return doc


@dataclass(frozen=True)
class LocalSources:
    data: Path

    @property
    def registry(self) -> Path:
        return self.data / REGISTRY

    def all(self) -> dict[str, LocalSource]:
        if not self.registry.exists():
            return {}
        doc = yaml.safe_load(self.registry.read_text("utf-8")) or {}
        return {
            name: LocalSource(name, Path(s["path"]), s.get("scale_arcsec"), str(s.get("added_at", "")))
            for name, s in (doc.get("sources") or {}).items()
        }

    def get(self, name: str) -> LocalSource:
        sources = self.all()
        if name not in sources:
            known = ", ".join(sorted(sources)) or "none yet; add one with sdp local add"
            raise LocalSourceError(f"no local source {name!r}; added: {known}")
        return sources[name]

    def add(self, name: str, path: Path, scale_arcsec: float | None = None) -> LocalSource:
        if not _NAME.match(name):
            raise LocalSourceError(f"{name!r}: a source name is lower-case letters, digits, - and _ (e.g. astro)")
        if not path.is_dir():
            raise LocalSourceError(f"{path} is not a folder")
        if _inside(self.data, path):
            raise LocalSourceError(f"SDP's data folder {self.data} is inside {path}; SDP never writes into a source")
        if _inside(path, self.data):
            raise LocalSourceError(f"{path} is inside SDP's data folder {self.data}; keep sources outside it")
        if scale_arcsec is not None and not 0 < scale_arcsec < 3600:
            raise LocalSourceError("--scale is arcseconds per pixel, such as 1.65")
        source = LocalSource(name, path.resolve(), scale_arcsec, datetime.now(UTC).isoformat(timespec="seconds"))
        sources = {**self.all(), name: source}
        self.data.mkdir(parents=True, exist_ok=True)
        document = {"sources": {n: s.to_document() for n, s in sorted(sources.items())}}
        tmp = self.registry.with_name(self.registry.name + ".tmp")
        tmp.write_text(yaml.safe_dump(document, sort_keys=False), "utf-8")
        tmp.replace(self.registry)
        return source
