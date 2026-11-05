"""``sdp delta``: what a new Gaia release changes for the 24 seed clusters (Discovery Roadmap C5).

The science is sdp_stellar.dr4_delta; the data come through sdp.adapters.archives.gaia_delta. Until the
predeclaration is frozen (before Gaia DR4, 2 December 2026) every run is a dry run: DR2 to DR3.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from sdp.adapters.archives.transport import TransportError
from sdp.adapters.archives.untrusted import UntrustedInput
from sdp.kernel.domain.errors import DomainError

if TYPE_CHECKING:
    from sdp.adapters.archives.transport import Transport

delta_app = typer.Typer(
    help="The Gaia release delta engine for the 24 seed clusters: membership, distance and motion changes "
    "under one predeclared selection. Dry run DR2 to DR3 now, DR3 to DR4 from 2 December 2026.",
    no_args_is_help=True,
)

SEEDS_FILE = "seeds.json"
#: What an archive can do to one cluster's request: fail, answer something refused, or not know the name.
#: Each costs that cluster only; the command names it and exits 1, and the next run tries it again.
ARCHIVE_FAILURES = (TransportError, UntrustedInput, LookupError)


def _say(text: str) -> None:
    typer.echo(text, err=True)


def transport() -> Transport:
    """Live archives, politely; tests replace this."""
    from sdp.adapters.archives.transport import PoliteTransport, UrllibTransport

    return PoliteTransport(UrllibTransport(timeout_s=600.0), min_seconds_between_calls=1.0)


def folder() -> Path:
    from sdp.adapters.local.sources import LocalSourceError, data_dir

    try:
        return data_dir() / "delta"
    except LocalSourceError as exc:
        raise DomainError(f"{exc}, or give --out") from exc


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, sort_keys=True) + "\n", "utf-8")
    tmp.replace(path)


def _seeds(base: Path, t: Transport, settings: Mapping[str, Any], names: Sequence[str]) -> dict[str, dict[str, Any]]:
    """HR23's rows for the named seed clusters, each kept as soon as it is fetched. A cluster whose row
    cannot be fetched is left out (and reported), so one failed lookup costs only that cluster."""
    from sdp.adapters.archives import gaia_delta

    path = base / SEEDS_FILE
    kept: dict[str, dict[str, Any]] = json.loads(path.read_text("utf-8")) if path.exists() else {}
    missing = [n for n in names if n not in kept]
    if missing:
        v = gaia_delta.vizier(t)
        for n in missing:
            _say(f"HR23 row for {n}")
            try:
                row = gaia_delta.hr23_cluster(v, n)
            except ARCHIVE_FAILURES as exc:
                _say(f"HR23 row for {n}: not fetched ({exc})")
                continue
            kept[n] = {k: (x if not isinstance(x, bytes) else x.decode()) for k, x in row.items()}
            _write_json(path, kept)
    return {n: kept[n] for n in names if n in kept}


def _stars(path: Path) -> tuple[dict[str, Any], str]:
    """A kept cone and the query that fetched it."""
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    meta = table.schema.metadata or {}
    query = meta.get(b"sdp.query", b"").decode()
    return {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}, query


def _kept_query(path: Path) -> str | None:
    """The query a kept cone was fetched with; None when the file is not a readable cone."""
    import pyarrow.parquet as pq

    try:
        meta = pq.read_schema(path).metadata or {}
    except (OSError, ValueError):
        return None
    return str(meta.get(b"sdp.query", b"").decode())


def _clusters(settings: Mapping[str, Any], only: Sequence[str]) -> list[str]:
    names = [c["hr23"] for c in settings["clusters"]]
    unknown = [n for n in only if n not in names]
    if unknown:
        raise DomainError(f"not in the seed set: {', '.join(unknown)}")
    return [n for n in names if not only or n in only]


def _cone_query(settings: Mapping[str, Any], release: str, c: Any) -> str:
    """The query for one cluster's cone in one release, from the current settings."""
    from sdp.adapters.archives import gaia_delta
    from sdp_stellar import dr4_delta as d

    lo, hi = d.fetch_parallax_window(c, settings)
    return gaia_delta.cone_query(
        settings["releases"][release]["table"],
        d.STAR_COLUMNS,
        c.ra,
        c.dec,
        c.rt,
        lo,
        hi,
        float(settings["selection"]["g_max"]),
    )


@delta_app.command("fetch")
def delta_fetch(
    release: Annotated[str, typer.Option(help="The Gaia release: dr2, dr3 or dr4")] = "dr3",
    cluster: Annotated[list[str] | None, typer.Option(help="Only this seed cluster (HR23 name); repeatable")] = None,
    out: Annotated[Path | None, typer.Option(help="Folder (default <SDP data>/delta)")] = None,
) -> None:
    """Each seed cluster's stars in one release (a cone of its tidal radius), kept as Parquet; a cone
    already fetched with the current settings is skipped, and one fetched with other settings is fetched
    again. For DR3 HR23's own member lists are kept too, for the sanity check. A cluster the archives
    fail on is named and the command exits 1; running it again fetches only what is missing."""
    from sdp_stellar import dr4_delta as d

    settings = d.load_settings()
    if release not in settings["releases"]:
        raise DomainError(f"unknown release {release!r}; known: {', '.join(settings['releases'])}")
    base = out or folder()
    t = transport()
    names = _clusters(settings, cluster or [])
    seeds = _seeds(base, t, settings, names)
    failed = [n for n in names if n not in seeds]
    for name in (n for n in names if n in seeds):
        try:
            _fetch_one(t, settings, release, d.Cluster.from_row(seeds[name]), name, base)
        except ARCHIVE_FAILURES as exc:
            _say(f"{release} {name}: not fetched ({exc})")
            failed.append(name)
    typer.echo(json.dumps({"release": release, "folder": str(base / release), "failed": failed}, indent=2))
    if failed:
        raise typer.Exit(1)


def _fetch_one(t: Transport, settings: Mapping[str, Any], release: str, c: Any, name: str, base: Path) -> None:
    import pyarrow.parquet as pq

    from sdp.adapters.archives import gaia_delta

    target = base / release / f"{name}.parquet"
    query = _cone_query(settings, release, c)
    if _kept_query(target) != query:
        if target.exists():
            _say(f"{release} {name}: the kept cone was fetched with other settings, so it is fetched again")
        stars = gaia_delta.cone(t, query, f"{release} {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".parquet.tmp")
        pq.write_table(
            stars.replace_schema_metadata({"sdp.release": settings["releases"][release]["label"], "sdp.query": query}),
            tmp,
        )
        tmp.replace(target)  # a cone is either whole or absent, so an interrupted write is fetched again
        _say(f"{release} {name}: {stars.num_rows:,} stars")
    members = base / "hr23-members" / f"{name}.json"
    if release == "dr3" and not members.exists():
        _write_json(members, gaia_delta.hr23_members(gaia_delta.vizier(t), name))


def _matches(
    base: Path, t: Transport, settings: Mapping[str, Any], pair: str, name: str, ids: list[int]
) -> dict[int, int] | None:
    from sdp.adapters.archives import gaia_delta

    match = settings["matches"].get(pair)
    if match is None:
        raise DomainError(f"no predeclared way to match stars for {pair}")
    if match.get("identity"):
        return None
    path = base / "matches" / pair.replace("->", "-") / f"{name}.json"
    kept = {int(k): int(v) for k, v in json.loads(path.read_text("utf-8")).items()} if path.exists() else {}
    wanted = [i for i in ids if i not in kept]
    if wanted:
        kept |= gaia_delta.neighbourhood(t, match, wanted)
        _write_json(path, {str(k): v for k, v in sorted(kept.items())})
    return kept


@delta_app.command("compare")
def delta_compare(
    earlier: Annotated[str, typer.Option("--from", help="The earlier release")] = "dr2",
    later: Annotated[str, typer.Option("--to", help="The later release")] = "dr3",
    cluster: Annotated[list[str] | None, typer.Option(help="Only this seed cluster (HR23 name); repeatable")] = None,
    out: Annotated[Path | None, typer.Option(help="Folder (default <SDP data>/delta)")] = None,
) -> None:
    """The predeclared deltas for every seed cluster fetched in both releases: deltas-<from>-<to>.csv and
    a summary. Before the predeclaration is frozen this is a dry run. EXPLORATORY. Only cones fetched
    with the current settings are compared; the summary names every cluster left out and why, and the
    command exits 1 when an archive failed on one."""
    from sdp_stellar import dr4_delta as d

    settings = d.load_settings()
    for release in (earlier, later):
        if release not in settings["releases"]:
            raise DomainError(f"unknown release {release!r}; known: {', '.join(settings['releases'])}")
    base = out or folder()
    t = transport()
    names = _clusters(settings, cluster or [])
    seeds = _seeds(base, t, settings, names)
    pair = f"{earlier}->{later}"
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    stale: list[str] = []
    failed = [n for n in names if n not in seeds]
    unchecked: list[str] = []
    for name in (n for n in names if n in seeds):
        a_path, b_path = base / earlier / f"{name}.parquet", base / later / f"{name}.parquet"
        if not (a_path.exists() and b_path.exists()):
            skipped.append(name)
            continue
        c = d.Cluster.from_row(seeds[name])
        (a, a_query), (b, b_query) = _stars(a_path), _stars(b_path)
        if a_query != _cone_query(settings, earlier, c) or b_query != _cone_query(settings, later, c):
            stale.append(name)
            continue
        a_ids = [int(x) for x in a["source_id"][d.members(a, c, settings)]]
        try:
            counterpart = _matches(base, t, settings, pair, name, a_ids)
        except ARCHIVE_FAILURES as exc:
            _say(f"{name}: not compared ({exc})")
            failed.append(name)
            continue
        result = d.flat(d.compare(c, a, b, counterpart, settings))
        for release, stars in ((earlier, a), (later, b)):
            if release != "dr3":
                continue
            listed = base / "hr23-members" / f"{name}.json"
            if not listed.exists():
                unchecked.append(name)
                continue
            check = d.agreement_with_catalogue(
                stars, d.members(stars, c, settings), json.loads(listed.read_text("utf-8"))
            )
            result |= {f"hr23_{k}": v for k, v in check.items()}
        rows.append(result)
        _say(
            f"{name}: {result['earlier_n']} -> {result['later_n']} members, "
            f"parallax {result['delta_parallax_mas']:+.4f} mas, "
            f"{result['lost']} lost, {result['new']} new, {result['unmatched']} unmatched"
        )
    target = base / f"deltas-{earlier}-{later}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        fields = list(dict.fromkeys(k for r in rows for k in r)) or ["cluster"]
        writer = csv.DictWriter(handle, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "status": "EXPLORATORY (dry run)" if later != "dr4" else "EXPLORATORY until the predeclaration is frozen",
        "pair": pair,
        "clusters": len(rows),
        "skipped_not_fetched": skipped,
        "skipped_fetched_with_other_settings": stale,
        "failed_archive": failed,
        "hr23_check_missing": unchecked,
        "settings_sha256": d.settings_sha256(),
        "csv": str(target),
    }
    _write_json(base / f"summary-{earlier}-{later}.json", summary)
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))
    if failed:
        raise typer.Exit(1)
