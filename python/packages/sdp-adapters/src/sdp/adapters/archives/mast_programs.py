"""Every program of one MAST mission, with when its combined products become public (Discovery Roadmap C3).

Two small questions to MAST's CAOM TAP service, through the archive ``Transport`` so CI replays them:

1. each program's number, PI, title and category (``prpproject``: GO, GTO, ERS, DD, SURVEY, CAL...),
   from ``dbo.caomobservation``;
2. for each program, how many level-3 (combined) products it has and the first and last date one
   of them is public (``datarelease``), from ``dbo.caomplane`` joined to its observation.

Grouping the first query by title times out at MAST (seconds for DISTINCT, a minute's gateway timeout
for GROUP BY on a text column, 2026-11-05), so it asks for distinct rows and folds them here: a
program listed under two spellings of its PI keeps the first in sorted order.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sdp.adapters.archives.mast import MAST_OBSCORE
from sdp.adapters.archives.transport import Transport
from sdp.adapters.archives.untrusted import UntrustedInput
from sdp.adapters.archives.vo import literal, run_sync, sync_request

MAX_PROGRAMS = 50_000
_DIGITS = re.compile(r"^\d{1,6}$")


def programs_query(collection: str) -> str:
    return (
        "SELECT DISTINCT o.prpid, o.prppi, o.prptitle, o.prpproject FROM dbo.caomobservation o "
        f"WHERE o.collection = {literal(collection)}"
    )


def releases_query(collection: str) -> str:
    return (
        "SELECT o.prpid, COUNT(*) AS level3_products, MIN(p.datarelease) AS public_from, "
        "MAX(p.datarelease) AS public_to FROM dbo.caomplane p JOIN dbo.caomobservation o "
        f"ON o.id = p.observationuuid WHERE p.collection = {literal(collection)} "
        f"AND o.collection = {literal(collection)} AND p.calibrationlevel = 3 GROUP BY o.prpid"
    )


def _when(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace("T", " ").rstrip("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise UntrustedInput(f"MAST gave a release date I cannot read: {value!r}")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def fetch_programs(transport: Transport, collection: str = "JWST") -> list[dict[str, Any]]:
    """One dict per program number, sorted by number: ``program_id``, ``pi``, ``title``, ``category``,
    ``level3_products`` and ``public_from``/``public_to`` (UTC datetimes, None when it has no level-3
    product). A program number that is not digits is refused: it is put into an ADS query later."""
    described = run_sync(
        transport, sync_request(MAST_OBSCORE, programs_query(collection), MAX_PROGRAMS), "MAST programs"
    ).to_pylist()
    released = run_sync(
        transport, sync_request(MAST_OBSCORE, releases_query(collection), MAX_PROGRAMS), "MAST program releases"
    ).to_pylist()
    for rows, what in ((described, "programs"), (released, "program releases")):
        if len(rows) >= MAX_PROGRAMS:
            raise UntrustedInput(f"MAST {what}: {len(rows)} rows reached the {MAX_PROGRAMS} cap, so some are missing")
    programs: dict[str, dict[str, Any]] = {}
    for r in sorted(described, key=lambda r: tuple(_text(r.get(k)) for k in ("prpid", "prppi", "prptitle"))):
        pid = _text(r.get("prpid"))
        if not pid:
            continue
        if not _DIGITS.match(pid):
            raise UntrustedInput(f"MAST gave a program id that is not a number: {pid!r}")
        programs.setdefault(
            pid,
            {
                "program_id": pid,
                "pi": _text(r.get("prppi")),
                "title": _text(r.get("prptitle")),
                "category": _text(r.get("prpproject")).upper(),
                "level3_products": 0,
                "public_from": None,
                "public_to": None,
            },
        )
    for r in released:
        pid = _text(r.get("prpid"))
        if pid in programs:
            programs[pid]["level3_products"] = int(r.get("level3_products") or 0)
            programs[pid]["public_from"] = _when(r.get("public_from"))
            programs[pid]["public_to"] = _when(r.get("public_to"))
    return sorted(programs.values(), key=lambda p: int(p["program_id"]))
