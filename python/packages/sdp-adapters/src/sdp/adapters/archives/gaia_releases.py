"""The Gaia data releases SDP reads, from ``gaia_releases.yaml`` (DR4 readiness).

Code asks for a release by its short name (``dr3``, ``dr4``) and gets its TAP tables, its bulk-file folder,
its column names and its citation. A release whose names are not yet checked against the live archive
(``confirmed: false``, DR4 before 2 December 2026) is refused by :func:`confirmed_release`, so nothing is
fetched under guessed names; :func:`check_release` asks the archive whether the names are right.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from sdp.adapters.archives.tap import TapSource, load_sources
from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.adapters.archives.vo import run_sync, sync_request

RELEASES_FILE = "gaia_releases.yaml"
_TABLE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_COLUMN = re.compile(r"^[a-z][a-z0-9_]*$")


class UnconfirmedRelease(RuntimeError):
    """A release's table names are guesses until checked against the live archive."""


@dataclass(frozen=True)
class GaiaRelease:
    name: str
    label: str
    confirmed: bool
    tap: str
    tables: Mapping[str, str]
    citation: str
    bulk: str | None = None
    doi: str | None = None
    orbits_citation: str = ""
    confirmed_on: str | None = None
    #: SDP's column name -> the release's own, only where the release renamed one
    columns: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for role, table in self.tables.items():
            if not _TABLE.match(table):
                raise ValueError(f"{self.name}: {role} {table!r} is not a schema.table name")
        for ours, theirs in self.columns.items():
            if not (_COLUMN.match(ours) and _COLUMN.match(theirs)):
                raise ValueError(f"{self.name}: column mapping {ours!r} -> {theirs!r} is not two plain names")
        if self.confirmed and not self.confirmed_on:
            raise ValueError(f"{self.name}: a confirmed release says when (confirmed_on)")
        if self.bulk is not None and not self.bulk.endswith("/"):
            raise ValueError(f"{self.name}: the bulk folder must end in /")

    def table(self, role: str) -> str:
        try:
            return self.tables[role]
        except KeyError:
            raise KeyError(f"Gaia {self.name} has no {role} table in {RELEASES_FILE}") from None

    def column(self, ours: str) -> str:
        """The release's own name for one of SDP's columns."""
        return self.columns.get(ours, ours)

    def select(self, alias: str, ours: str) -> str:
        """An ADQL select item that reads the release's column under SDP's name."""
        theirs = self.column(ours)
        return f"{alias}.{theirs}" if theirs == ours else f"{alias}.{theirs} AS {ours}"

    @classmethod
    def from_document(cls, name: str, doc: Mapping[str, Any]) -> GaiaRelease:
        return cls(
            name=name,
            label=str(doc["label"]),
            confirmed=bool(doc["confirmed"]),
            confirmed_on=str(doc["confirmed_on"]) if doc.get("confirmed_on") else None,
            tap=str(doc["tap"]).rstrip("/"),
            tables={str(k): str(v) for k, v in dict(doc["tables"]).items()},
            citation=str(doc["citation"]),
            bulk=str(doc["bulk"]) if doc.get("bulk") else None,
            doi=str(doc["doi"]) if doc.get("doi") else None,
            orbits_citation=str(doc.get("orbits_citation") or ""),
            columns={str(k): str(v) for k, v in dict(doc.get("columns") or {}).items()},
        )


def load_releases(path: Path | None = None) -> dict[str, GaiaRelease]:
    text = (
        path.read_text("utf-8")
        if path is not None
        else files("sdp.adapters.archives").joinpath(RELEASES_FILE).read_text("utf-8")
    )
    doc = yaml.safe_load(text)
    return {str(k): GaiaRelease.from_document(str(k), v) for k, v in dict(doc["releases"]).items()}


def release(name: str, releases: Mapping[str, GaiaRelease] | None = None) -> GaiaRelease:
    known = releases if releases is not None else load_releases()
    key = name.lower().removeprefix("gaia:")
    if key not in known:
        raise KeyError(f"unknown Gaia release {name!r}; known: {', '.join(sorted(known))}")
    return known[key]


def by_label(label: str, releases: Mapping[str, GaiaRelease] | None = None) -> GaiaRelease:
    """The release with this label (``gaia:DR4``)."""
    known = releases if releases is not None else load_releases()
    for r in known.values():
        if r.label == label:
            return r
    raise KeyError(f"no Gaia release labelled {label!r} in {RELEASES_FILE}")


def confirmed_release(name: str, releases: Mapping[str, GaiaRelease] | None = None) -> GaiaRelease:
    """The release, refused while its names are unconfirmed guesses."""
    r = release(name, releases)
    if not r.confirmed:
        raise UnconfirmedRelease(
            f"Gaia {r.name.upper()}'s table and column names in {RELEASES_FILE} are not yet checked against "
            f"the archive. Run `sdp archive gaia-check --release {r.name}`, correct any name it reports, and "
            f"set confirmed: true with confirmed_on (docs/dr4-release-day.md)"
        )
    return r


def gaia_source(name: str, releases: Mapping[str, GaiaRelease] | None = None) -> TapSource:
    """The ``gaia`` TAP source for a release: DR3's source file with the release's table, label, dialect and
    citation. Columns keep SDP's names; :attr:`GaiaRelease.columns` says what the release calls them."""
    r = release(name, releases)
    base = load_sources()["gaia"]
    if r.label == base.descriptor.release_label:
        return base
    descriptor = replace(
        base.descriptor,
        release_label=r.label,
        table=r.table("gaia_source"),
        dialect_id=f"gaia-{r.name}",
        citation=r.citation,
        doi=r.doi,
    )
    return replace(base, descriptor=descriptor, service_url=r.tap)


# --- checking a release against the live archive ------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    release: str
    problems: tuple[str, ...]
    tables: Mapping[str, int]
    bulk_files: int | None

    def to_document(self) -> dict[str, Any]:
        return {
            "release": self.release,
            "ok": not self.problems,
            "problems": list(self.problems),
            "columns_found": dict(self.tables),
            "bulk_files": self.bulk_files,
        }


def check_release(
    r: GaiaRelease,
    transport: Transport,
    wanted: Mapping[str, Sequence[str]],
    bulk_listing: Callable[[str], bytes] | None = None,
) -> CheckResult:
    """Ask the archive's TAP_SCHEMA whether every table and column SDP reads exists under the release's
    names, and (with ``bulk_listing``) whether the bulk folder lists its files. ``wanted`` maps a table role
    to SDP's column names in it."""
    problems: list[str] = []
    found: dict[str, int] = {}
    for role, ours in wanted.items():
        if role not in r.tables:
            problems.append(f"no {role} table is named in {RELEASES_FILE}")
            continue
        table = r.table(role)
        query = f"SELECT column_name FROM tap_schema.columns WHERE table_name = '{table}'"
        try:
            answer = run_sync(transport, sync_request(r.tap, query, 5000), f"columns of {table}")
        except TransportError as exc:
            problems.append(f"{table}: the archive did not answer ({exc})")
            continue
        names = {str(n).lower() for n in answer.column("column_name").to_pylist()} if answer.num_rows else set()
        found[table] = len(names)
        if not names:
            problems.append(f"{table}: the archive has no such table")
            continue
        for c in ours:
            if r.column(c).lower() not in names:
                problems.append(f"{table}: no column {r.column(c)!r} (SDP reads it as {c!r})")
    count: int | None = None
    if bulk_listing is not None and r.bulk is not None:
        try:
            text = bulk_listing(r.bulk + "_MD5SUM.txt").decode("utf-8", "replace")
            count = sum(1 for line in text.splitlines() if line.strip().endswith(".csv.gz"))
            if count == 0:
                problems.append(f"{r.bulk}_MD5SUM.txt lists no .csv.gz files")
        except (OSError, TransportError) as exc:
            problems.append(f"{r.bulk}: no _MD5SUM.txt ({exc})")
    return CheckResult(r.name, tuple(problems), found, count)


def http_get(url: str, transport: Transport) -> bytes:
    response = transport.send(HttpRequest("GET", url))
    if response.status != 200:
        raise OSError(f"HTTP {response.status}")
    return response.body
