"""``sdp publication``: which public archive data has no paper (Discovery Roadmap C3, EXPLORATORY).

The science is sdp_investigate.linkage; programs come from sdp.adapters.archives.mast_programs and
papers from NASA ADS (sdp.adapters.archives.ads). The ADS token is read from the environment or from
the gitignored ``.env`` at the top of the checkout; it is never printed or written anywhere.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from sdp.kernel.domain.errors import DomainError

if TYPE_CHECKING:
    from sdp.adapters.archives.transport import Transport

publication_app = typer.Typer(
    help="Publication linkage: join archive programs to NASA ADS to find public data no paper names. "
    "Needs an ADS token (ADS_API_TOKEN in your .env). Every result is EXPLORATORY.",
    no_args_is_help=True,
)

ANSWERS_FILE = "ads-answers.jsonl"
#: how many refereed bibcodes are kept per program (oldest first)
KEEP_BIBCODES = 5


def _say(text: str) -> None:
    typer.echo(text, err=True)


def today() -> date:
    return datetime.now(UTC).date()


def ads_transport() -> Transport:
    """Live ADS, at most one search a second; tests replace this."""
    from sdp.adapters.archives.transport import ARCHIVE_RETRY, PoliteTransport, UrllibTransport

    return PoliteTransport.with_policy(UrllibTransport(timeout_s=120.0), ARCHIVE_RETRY)


def mast_transport() -> Transport:
    """Live MAST TAP; tests replace this."""
    from sdp.adapters.archives.transport import ARCHIVE_RETRY, PoliteTransport, UrllibTransport

    return PoliteTransport.with_policy(UrllibTransport(timeout_s=600.0), ARCHIVE_RETRY)


def ads_token() -> str:
    from sdp.adapters.archives.ads import AdsTokenMissing, token_from_env
    from sdp.adapters.settings import load_dotenv

    load_dotenv()
    try:
        return token_from_env()
    except AdsTokenMissing as exc:
        raise DomainError(str(exc)) from exc


def folder(mission: str) -> Path:
    from sdp.adapters.local.sources import LocalSourceError, data_dir

    try:
        return data_dir() / "publication" / mission.lower()
    except LocalSourceError as exc:
        raise DomainError(f"{exc}, or give --out") from exc


def _read_answers(path: Path) -> dict[str, dict[str, Any]]:
    answers: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                answers[str(record["query"])] = record
    return answers


#: a cell starting with one of these is read as a formula by spreadsheets; MAST titles and PIs are not ours
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> Any:
    """A text cell a spreadsheet will show as text: a leading formula character gets a quote before it."""
    if isinstance(value, str) and value.lstrip(" ").startswith(_FORMULA_START):
        return "'" + value
    return value


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], empty: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        names = list(dict.fromkeys(k for r in rows for k in r)) or list(empty)
        writer = csv.DictWriter(handle, fieldnames=names, restval="")
        writer.writeheader()
        writer.writerows({k: _cell(v) for k, v in r.items()} for r in rows)
    tmp.replace(path)


def _ask(
    transport: Transport,
    token: str,
    query: str,
    search: Callable[..., Any],
    asked_on: date,
    reserve: int = 0,
) -> tuple[dict[str, Any], int | None]:
    from sdp_investigate.linkage import refereed_count

    answer = search(
        transport,
        query,
        token=token,
        fields=("bibcode", "year", "property"),
        rows=KEEP_BIBCODES * 4,
        sort="date asc",
        facet_fields=("property",),
    )
    refereed = [str(d["bibcode"]) for d in answer.docs if "REFEREED" in (d.get("property") or [])]
    counted = refereed_count(answer.facets)
    remaining = answer.remaining
    over_reserve = remaining is None or remaining > reserve
    if counted and not refereed and over_reserve:
        # the first page held only preprints; ask once more for the refereed papers themselves,
        # unless that would spend a search the reserve keeps back
        again = search(
            transport,
            f"({query}) AND property:refereed",
            token=token,
            fields=("bibcode", "year", "property"),
            rows=KEEP_BIBCODES,
            sort="date asc",
        )
        refereed = [str(d["bibcode"]) for d in again.docs]
        remaining = again.remaining if again.remaining is not None else remaining
    record = {
        "query": query,
        "total": answer.num_found,
        "refereed": counted,
        "bibcodes": refereed[:KEEP_BIBCODES],
        "asked_on": asked_on.isoformat(),
    }
    return record, remaining


@publication_app.command("search")
def publication_search(
    query: Annotated[str, typer.Argument(help="An ADS query, e.g. 'full:\"Ruprecht 147\" AND year:2023-'")],
    rows: Annotated[int, typer.Option(help="How many records to list")] = 10,
    refereed: Annotated[bool, typer.Option(help="Only refereed papers")] = False,
) -> None:
    """Ask ADS what has been written: the prior-art check before a note is submitted (a gap that is
    already published is itself a result to record)."""
    from sdp.adapters.archives import ads

    q = f"({query}) AND property:refereed" if refereed else query
    answer = ads.search(
        ads_transport(), q, token=ads_token(), fields=("bibcode", "title", "year", "citation_count"), rows=rows
    )
    _say(
        f"{answer.num_found:,} records match"
        + (f" ({answer.remaining:,} ADS searches left today)" if answer.remaining is not None else "")
    )
    for d in answer.docs:
        title = (d.get("title") or [""])[0]
        _say(f"  {d.get('bibcode', '')}  {title[:100]}  (cited {d.get('citation_count', 0)})")
    typer.echo(
        json.dumps(
            {
                "query": q,
                "num_found": answer.num_found,
                "records": [{k: d.get(k) for k in ("bibcode", "title", "year", "citation_count")} for d in answer.docs],
                "asked_on": today().isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
    )


@publication_app.command("programs")
def publication_programs(
    mission: Annotated[str, typer.Option(help="The MAST mission whose programs are checked")] = "JWST",
    out: Annotated[
        Path | None, typer.Option(help="Folder for the register (default <SDP data>/publication/<mission>)")
    ] = None,
    reserve: Annotated[int, typer.Option(help="Stop asking ADS when fewer searches than this are left today")] = 200,
    limit: Annotated[int | None, typer.Option(help="Ask ADS about at most this many programs this run")] = None,
    max_age_days: Annotated[
        int, typer.Option(help="Ask ADS again about a program whose kept answer is older than this")
    ] = 30,
) -> None:
    """Every public science program of the mission, joined to the ADS papers that name it. Writes
    programs.csv (every program), unpublished.csv (public a year or more, no paper or preprint names it)
    and summary.json. ADS answers are kept in ads-answers.jsonl, so a run stopped by the daily allowance
    resumes the next day without asking again. EXPLORATORY."""
    from sdp.adapters.archives import ads
    from sdp.adapters.archives.mast_programs import fetch_programs
    from sdp_investigate import linkage

    asof = today()
    target = out or folder(mission)
    target.mkdir(parents=True, exist_ok=True)
    answers_path = target / ANSWERS_FILE
    answers = {
        q: a
        for q, a in _read_answers(answers_path).items()
        if (asof - date.fromisoformat(str(a["asked_on"]))).days <= max_age_days
    }
    token = ads_token()

    collection = mission.upper()
    found = fetch_programs(mast_transport(), collection)
    programs = [
        linkage.Program(
            mission=collection,
            program_id=p["program_id"],
            category=p["category"],
            pi=p["pi"],
            title=p["title"],
            products=p["level3_products"],
            public_from=p["public_from"],
            public_to=p["public_to"],
        )
        for p in found
    ]
    to_ask = [p for p in programs if linkage.status_without_ads(p, asof) is None]
    _say(f"{len(programs):,} {collection} programs at MAST; {len(to_ask):,} public science programs to check in ADS")

    transport = ads_transport()
    asked = 0
    stopped = ""
    with answers_path.open("a", encoding="utf-8") as log:
        for p in to_ask:
            query = linkage.mention_query(collection, p.program_id)
            if query in answers:
                continue
            if limit is not None and asked >= limit:
                stopped = f"--limit {limit} reached"
                break
            record, remaining = _ask(transport, token, query, ads.search, asof, reserve)
            answers[query] = record
            log.write(json.dumps(record, sort_keys=True) + "\n")
            log.flush()
            asked += 1
            if asked % 50 == 0:
                _say(
                    f"asked ADS about {asked} programs"
                    + (f" ({remaining:,} searches left today)" if remaining is not None else "")
                )
            if remaining is not None and remaining <= reserve:
                stopped = f"only {remaining} ADS searches left today (reserve {reserve})"
                break

    rows = []
    asked_ids = {p.program_id for p in to_ask}
    for p in programs:
        kept = answers.get(linkage.mention_query(collection, p.program_id)) if p.program_id in asked_ids else None
        mentions = (
            linkage.Mentions(
                query=str(kept["query"]),
                total=int(kept["total"]),
                refereed=int(kept["refereed"]),
                bibcodes=tuple(kept["bibcodes"]),
            )
            if kept
            else None
        )
        rows.append(linkage.row(p, mentions, linkage.classify(p, mentions, asof), asof))
    _write_csv(target / "programs.csv", rows, ["program_id", "status"])
    unpublished = sorted(
        (r for r in rows if r["status"] == linkage.UNPUBLISHED), key=lambda r: -int(r["public_days"] or 0)
    )
    _write_csv(target / "unpublished.csv", unpublished, ["program_id", "status"])
    result = linkage.summary(rows, collection, asof)
    in_register = {row["ads_query"] for row in rows}
    used = sorted(str(r["asked_on"]) for r in answers.values() if r["query"] in in_register)
    result |= {
        "asked_this_run": asked,
        "stopped": stopped or None,
        "folder": str(target),
        # the register is as of these ADS dates, not only the day it was written
        "ads_asked_from": used[0] if used else None,
        "ads_asked_to": used[-1] if used else None,
    }
    (target / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", "utf-8")

    counts = result["counts"]
    _say(
        f"{counts[linkage.PUBLISHED]:,} programs named in refereed papers, {counts[linkage.ONLY_PREPRINTS]:,} "
        f"only in preprints, {counts[linkage.UNPUBLISHED]:,} in nothing ADS holds; "
        f"{counts[linkage.TOO_RECENT]:,} public under a year, {counts[linkage.NOT_PUBLIC]:,} not public yet."
    )
    if stopped:
        _say(
            f"Stopped early: {stopped}. {counts[linkage.NOT_ASKED]:,} programs are still to ask; run the same command again to carry on."
        )
    _say(f"EXPLORATORY: every unpublished program is a candidate to check by hand. Open {target / 'unpublished.csv'}.")
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
