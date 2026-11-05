"""Publication linkage (Discovery Roadmap C3): which public archive programs have no refereed paper?

A program's data are "used" when a refereed paper names the program. NASA ADS indexes the full text of
refereed papers and arXiv preprints, so each program is asked for once, as a full-text query that
names the mission and the program number the ways papers write it (``GO 1324``, ``PID 1324``,
``program 1324``, ``jw01324``...), from the year the mission's data began. ADS's ``property`` facet
splits the answer into refereed and not refereed in the same call.

What a zero means, and does not:

* the count is of papers that *name* the program in their text. A paper that uses the data without
  naming the program (it cites only a data DOI, or a table) is missed, so a zero is a candidate for
  the underused-data register, not proof that nobody used the data; the register says so;
* a non-zero count can include a paper that names an unrelated program with the same number, so it is
  an upper bound on use, and every bibcode is kept so it can be read;
* ADS's full text lags publication by weeks, so programs whose data became public less than
  ``min_public_days`` ago are reported as too recent, not as unpublished.

Every result here is EXPLORATORY: the register is the input to a predeclared note, not a finding.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

#: The settings every register is made under; their hash goes into every summary.
SETTINGS: dict[str, Any] = {
    "missions": {
        "JWST": {
            "names": ["JWST", "James Webb Space Telescope"],
            "first_year": 2022,
            "file_prefix": "jw",
            "science_categories": ["DD", "ERS", "GO", "GTO", "SURVEY"],
        }
    },
    #: how papers write a program number; ``{n}`` is the number as MAST gives it, ``{n5}`` zero-padded to 5
    "program_phrases": [
        "program {n}",
        "programme {n}",
        "program ID {n}",
        "programme ID {n}",
        "proposal {n}",
        "proposal ID {n}",
        "PID {n}",
        "GO {n}",
        "GTO {n}",
        "ERS {n}",
        "DD {n}",
        "DDT {n}",
        "ERO {n}",
        "{prefix}{n5}",
    ],
    "min_public_days": 365,
}

PROGRAM_ID = re.compile(r"^\d{1,6}$")

UNPUBLISHED = "unpublished"
ONLY_PREPRINTS = "only-preprints"
PUBLISHED = "published"
TOO_RECENT = "too-recent"
NOT_PUBLIC = "not-public"
NOT_SCIENCE = "not-science"
#: a program ADS has not been asked about yet (the daily allowance ran out); the next run asks
NOT_ASKED = "not-asked-yet"
STATUSES = (UNPUBLISHED, ONLY_PREPRINTS, PUBLISHED, TOO_RECENT, NOT_PUBLIC, NOT_SCIENCE, NOT_ASKED)


def settings_sha256(settings: Mapping[str, Any] = SETTINGS) -> str:
    return hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Program:
    """One archive program as the archive describes it."""

    mission: str
    program_id: str
    category: str
    pi: str
    title: str
    #: level-3 (combined) products the archive holds for it
    products: int
    #: when its first and last level-3 product became (or will become) public
    public_from: datetime | None
    public_to: datetime | None


@dataclass(frozen=True)
class Mentions:
    """What ADS answered for one program's query."""

    query: str
    total: int
    refereed: int
    #: the earliest refereed bibcodes, oldest first (enough to read, not every one)
    bibcodes: tuple[str, ...]


def _mission(settings: Mapping[str, Any], mission: str) -> Mapping[str, Any]:
    missions = settings["missions"]
    if mission not in missions:
        raise ValueError(f"no linkage settings for mission {mission!r}; known: {', '.join(sorted(missions))}")
    found: Mapping[str, Any] = missions[mission]
    return found


def _quoted(phrase: str) -> str:
    if '"' in phrase or "\\" in phrase:
        raise ValueError(f"a phrase may not hold quotes or backslashes: {phrase!r}")
    return f'"{phrase}"'


def mention_query(mission: str, program_id: str, settings: Mapping[str, Any] = SETTINGS) -> str:
    """The ADS query for papers whose full text names the mission and the program, from its first year."""
    if not PROGRAM_ID.match(program_id):
        raise ValueError(f"a program id is digits only, not {program_id!r}")
    m = _mission(settings, mission)
    n = str(int(program_id))
    phrases = [p.format(n=n, n5=n.zfill(5), prefix=m.get("file_prefix", "")) for p in settings["program_phrases"]]
    names = " OR ".join(_quoted(x) for x in m["names"])
    named = " OR ".join(_quoted(p) for p in dict.fromkeys(phrases))
    return f"full:({names}) AND full:({named}) AND year:{int(m['first_year'])}-"


def refereed_count(facets: Mapping[str, Mapping[str, int]]) -> int:
    """ADS's ``property`` facet gives refereed papers as ``refereed`` (lower case in facets)."""
    return int(facets.get("property", {}).get("refereed", 0))


def status_without_ads(program: Program, today: date, settings: Mapping[str, Any] = SETTINGS) -> str | None:
    """A program's status when ADS need not be asked (it costs one call of a daily allowance), else None.
    Only a science program whose data have been public for ``min_public_days`` is asked about."""
    m = _mission(settings, program.mission)
    if program.category not in m["science_categories"]:
        return NOT_SCIENCE
    if program.public_from is None or program.public_from.date() > today:
        return NOT_PUBLIC
    if (today - program.public_from.date()).days < int(settings["min_public_days"]):
        return TOO_RECENT
    return None


def classify(
    program: Program,
    mentions: Mentions | None,
    today: date,
    settings: Mapping[str, Any] = SETTINGS,
) -> str:
    """Where a program stands: unpublished when no refereed paper and no preprint names it."""
    known = status_without_ads(program, today, settings)
    if known is not None:
        return known
    if mentions is None:
        return NOT_ASKED
    if mentions.refereed > 0:
        return PUBLISHED
    return ONLY_PREPRINTS if mentions.total > 0 else UNPUBLISHED


def row(program: Program, mentions: Mentions | None, status: str, today: date) -> dict[str, Any]:
    """One line of programs.csv."""
    public_days = (today - program.public_from.date()).days if program.public_from else None
    return {
        "mission": program.mission,
        "program_id": program.program_id,
        "category": program.category,
        "pi": program.pi,
        "title": program.title,
        "level3_products": program.products,
        "public_from": program.public_from.date().isoformat() if program.public_from else "",
        "public_to": program.public_to.date().isoformat() if program.public_to else "",
        "public_days": "" if public_days is None else public_days,
        "status": status,
        "refereed_mentions": "" if mentions is None else mentions.refereed,
        "all_mentions": "" if mentions is None else mentions.total,
        "first_refereed_bibcodes": "" if mentions is None else " ".join(mentions.bibcodes),
        "ads_query": "" if mentions is None else mentions.query,
    }


def summary(rows: Sequence[Mapping[str, Any]], mission: str, today: date) -> dict[str, Any]:
    """Counts by status, and by category for the science programs asked about."""
    counts = {s: sum(r["status"] == s for r in rows) for s in STATUSES}
    asked = [r for r in rows if r["status"] in (UNPUBLISHED, ONLY_PREPRINTS, PUBLISHED)]
    by_category: dict[str, dict[str, int]] = {}
    for r in asked:
        c = by_category.setdefault(str(r["category"]), {UNPUBLISHED: 0, ONLY_PREPRINTS: 0, PUBLISHED: 0})
        c[str(r["status"])] += 1
    return {
        "status": "EXPLORATORY",
        "mission": mission,
        "as_of": today.isoformat(),
        "programs": len(rows),
        "counts": counts,
        "asked_by_category": dict(sorted(by_category.items())),
        "unpublished_share": round(counts[UNPUBLISHED] / len(asked), 4) if asked else None,
        "complete": counts[NOT_ASKED] == 0,
        "settings_sha256": settings_sha256(),
        "caveat": (
            "A program is unpublished when no refereed paper or preprint in ADS names it in its full text. "
            "Papers that use the data without naming the program are missed, so each unpublished program is a "
            "candidate to check by hand, not a finding."
        ),
    }
