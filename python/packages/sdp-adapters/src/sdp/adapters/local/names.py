"""A local source's names file: where each OBJECT name its frames use points on the sky (#71).

It is kept as ``<data>/index/<source>/names.yaml``. SDP fills it from SIMBAD the first time it meets a name,
and the person can add or correct a name (``sdp local names --centre``), so a later run needs no network.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
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


@dataclass
class NameBook:
    source: LocalSource
    data: Path
    _entries: dict[str, dict[str, Any]] | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def path(self) -> Path:
        return self.source.output_dir(self.data, "index") / "names.yaml"

    def entries(self) -> dict[str, dict[str, Any]]:
        if self._entries is None:
            doc = yaml.safe_load(self.path.read_text("utf-8")) if self.path.exists() else None
            self._entries = dict((doc or {}).get("names") or {})
        return self._entries

    def get(self, name: str) -> tuple[float, float] | None:
        entry = self.entries().get(name)
        return None if entry is None else (float(entry["ra_deg"]), float(entry["dec_deg"]))

    def set(self, name: str, ra_deg: float, dec_deg: float, given_by: str) -> None:
        with self._lock:
            entries = self.entries()
            if given_by == FROM_SIMBAD and entries.get(name, {}).get("from") == FROM_YOU:
                return
            entries[name] = {
                "ra_deg": round(float(ra_deg), 7),
                "dec_deg": round(float(dec_deg), 7),
                "from": given_by,
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(yaml.safe_dump({"names": dict(sorted(entries.items()))}, sort_keys=False), "utf-8")
            tmp.replace(self.path)
