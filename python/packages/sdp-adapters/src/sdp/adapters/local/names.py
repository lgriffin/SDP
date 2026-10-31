"""A local source's names file: where each OBJECT name its frames use points on the sky (#71).

It is kept as ``<data>/index/<source>/names.yaml``. SDP fills it from SIMBAD the first time it meets a name,
and the person can add or correct a name (``sdp local names --centre``), so a later run needs no network.

Its ``folders`` section gives a folder's centre (``sdp local names --folder raw/IC434=84.0,-2.4``), for
frames whose header has no OBJECT to look up (#143). A folder's centre covers the folders inside it, and
the deepest folder given wins.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from sdp.adapters.local.sources import LocalSource, LocalSourceError

#: who gave a name's position: SIMBAD, or the person (which SIMBAD never overwrites)
FROM_SIMBAD, FROM_YOU = "SIMBAD", "you"


def parse_centre(text: str) -> tuple[str, float, float]:
    """``NAME=RA,DEC`` in degrees, as ``--centre`` takes it: ``M31=10.68,41.27``."""
    name, sep, position = text.rpartition("=")
    try:
        ra, dec = (float(v) for v in position.split(","))
    except ValueError:
        ra = dec = float("nan")
    if not sep or not name.strip() or not (0 <= ra < 360 and -90 <= dec <= 90):
        raise LocalSourceError(f"{text!r}: give a centre as NAME=RA,DEC in degrees, such as M31=10.68,41.27")
    return name.strip(), ra, dec


def folder_key(text: str) -> str:
    """A folder inside the source as the index writes paths: ``/`` separators, no leading ``./`` or
    trailing slash (``raw\\IC434\\`` and ``./raw/IC434`` are both ``raw/IC434``)."""
    parts = [p for p in text.strip().replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts or ":" in parts[0]:
        raise LocalSourceError(f"{text!r}: give a folder inside the source, such as raw/IC434")
    return "/".join(parts)


def parse_folder(text: str) -> tuple[str, float, float]:
    """``FOLDER=RA,DEC`` in degrees, as ``--folder`` takes it: ``raw/IC434=85.25,-2.46``."""
    try:
        folder, ra, dec = parse_centre(text)
    except LocalSourceError:
        raise LocalSourceError(
            f"{text!r}: give a folder's centre as FOLDER=RA,DEC in degrees, such as raw/IC434=85.25,-2.46"
        ) from None
    return folder_key(folder), ra, dec


@dataclass
class NameBook:
    source: LocalSource
    data: Path
    _entries: dict[str, dict[str, Any]] | None = field(default=None, repr=False)
    _folders: dict[str, dict[str, Any]] | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def path(self) -> Path:
        return self.source.output_dir(self.data, "index") / "names.yaml"

    def _load(self) -> None:
        if self._entries is None or self._folders is None:
            doc = yaml.safe_load(self.path.read_text("utf-8")) if self.path.exists() else None
            self._entries = dict((doc or {}).get("names") or {})
            self._folders = dict((doc or {}).get("folders") or {})

    def entries(self) -> dict[str, dict[str, Any]]:
        self._load()
        assert self._entries is not None
        return self._entries

    def folders(self) -> dict[str, dict[str, Any]]:
        self._load()
        assert self._folders is not None
        return self._folders

    def folder_centre(self, relative: str) -> tuple[str, tuple[float, float]] | None:
        """The centre given for the deepest folder holding the frame at ``relative``, with that folder."""
        given = self.folders()
        for parent in map(str, PurePosixPath(relative).parents):  # deepest first, then "."
            if parent in given:
                return parent, (float(given[parent]["ra_deg"]), float(given[parent]["dec_deg"]))
        return None

    def set_folder(self, folder: str, ra_deg: float, dec_deg: float) -> None:
        """A folder's centre, from the person (``--folder``)."""
        with self._lock:
            self.folders()[folder_key(folder)] = self._entry(ra_deg, dec_deg, FROM_YOU)
            self._save()

    @staticmethod
    def _entry(ra_deg: float, dec_deg: float, given_by: str) -> dict[str, Any]:
        return {
            "ra_deg": round(float(ra_deg), 7),
            "dec_deg": round(float(dec_deg), 7),
            "from": given_by,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    def _save(self) -> None:
        doc: dict[str, Any] = {"names": dict(sorted(self.entries().items()))}
        if self.folders():
            doc["folders"] = dict(sorted(self.folders().items()))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
        tmp.replace(self.path)

    def get(self, name: str) -> tuple[float, float] | None:
        entry = self.entries().get(name)
        return None if entry is None else (float(entry["ra_deg"]), float(entry["dec_deg"]))

    def set(self, name: str, ra_deg: float, dec_deg: float, given_by: str) -> None:
        with self._lock:
            entries = self.entries()
            if given_by == FROM_SIMBAD and entries.get(name, {}).get("from") == FROM_YOU:
                return
            entries[name] = self._entry(ra_deg, dec_deg, given_by)
            self._save()
