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
from sdp.kernel.domain.errors import DomainError

if TYPE_CHECKING:
    from sdp.adapters.archives.transport import Transport

delta_app = typer.Typer(
    help="The Gaia release delta engine for the 24 seed clusters: membership, distance and motion changes "
    "under one predeclared selection. Dry run DR2 to DR3 now, DR3 to DR4 from 2 December 2026.",
    no_args_is_help=True,
)

SEEDS_FILE = "seeds.json"


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


def _seeds(base: Path, t: Transport, settings: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """HR23's rows for the seed clusters, fetched once and kept."""
    from sdp.adapters.archives import gaia_delta

    path = base / SEEDS_FILE
    names = [c["hr23"] for c in settings["clusters"]]
    kept: dict[str, dict[str, Any]] = json.loads(path.read_text("utf-8")) if path.exists() else {}
    missing = [n for n in names if n not in kept]
    if missing:
        v = gaia_delta.vizier(t)
        for n in missing:
            _say(f"HR23 row for {n}")
            kept[n] = {
                k: (v if not isinstance(v, bytes) else v.decode()) for k, v in gaia_delta.hr23_cluster(v, n).items()
            }
        _write_json(path, kept)
    return {n: kept[n] for n in names}


def _stars(path: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    return {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}


def _clusters(settings: Mapping[str, Any], only: Sequence[str]) -> list[str]:
    names = [c["hr23"] for c in settings["clusters"]]
    unknown = [n for n in only if n not in names]
    if unknown:
        raise DomainError(f"not in the seed set: {', '.join(unknown)}")
    return [n for n in names if not only or n in only]


@delta_app.command("fetch")
def delta_fetch(
    release: Annotated[str, typer.Option(help="The Gaia release: dr2, dr3 or dr4")] = "dr3",
    cluster: Annotated[list[str] | None, typer.Option(help="Only this seed cluster (HR23 name); repeatable")] = None,
    out: Annotated[Path | None, typer.Option(help="Folder (default <SDP data>/delta)")] = None,
) -> None:
    """Each seed cluster's stars in one release (a cone of its tidal radius), kept as Parquet; clusters
    already fetched are skipped. For DR3 HR23's own member lists are kept too, for the sanity check."""

    from sdp_stellar import dr4_delta as d

    settings = d.load_settings()
    if release not in settings["releases"]:
        raise DomainError(f"unknown release {release!r}; known: {', '.join(settings['releases'])}")
    base = out or folder()
    t = transport()
    seeds = _seeds(base, t, settings)
    table = settings["releases"][release]["table"]
    failed: list[str] = []
    for name in _clusters(settings, cluster or []):
        c = d.Cluster.from_row(seeds[name])
        target = base / release / f"{name}.parquet"
        try:
            _fetch_one(t, settings, release, table, c, name, target, base)
        except TransportError as exc:
            # an archive hiccup costs one cluster, not the run; the next run fetches what is missing
            _say(f"{release} {name}: not fetched ({exc})")
            failed.append(name)
    typer.echo(json.dumps({"release": release, "folder": str(base / release), "failed": failed}, indent=2))
    if failed:
        raise typer.Exit(1)


def _fetch_one(
    t: Transport,
    settings: Mapping[str, Any],
    release: str,
    table: str,
    c: Any,
    name: str,
    target: Path,
    base: Path,
) -> None:
    import pyarrow.parquet as pq

    from sdp.adapters.archives import gaia_delta
    from sdp_stellar import dr4_delta as d

    if not target.exists():
        lo, hi = d.fetch_parallax_window(c, settings)
        query = gaia_delta.cone_query(
            table, d.STAR_COLUMNS, c.ra, c.dec, c.rt, lo, hi, float(settings["selection"]["g_max"])
        )
        stars = gaia_delta.cone(t, query, f"{release} {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            stars.replace_schema_metadata({"sdp.release": settings["releases"][release]["label"], "sdp.query": query}),
            target,
        )
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
    a summary. Before the predeclaration is frozen this is a dry run. EXPLORATORY."""
    from sdp_stellar import dr4_delta as d

    settings = d.load_settings()
    base = out or folder()
    t = transport()
    seeds = _seeds(base, t, settings)
    pair = f"{earlier}->{later}"
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for name in _clusters(settings, cluster or []):
        a_path, b_path = base / earlier / f"{name}.parquet", base / later / f"{name}.parquet"
        if not (a_path.exists() and b_path.exists()):
            skipped.append(name)
            continue
        c = d.Cluster.from_row(seeds[name])
        a, b = _stars(a_path), _stars(b_path)
        a_ids = [int(x) for x in a["source_id"][d.members(a, c, settings)]]
        result = d.flat(d.compare(c, a, b, _matches(base, t, settings, pair, name, a_ids), settings))
        for side, stars in (("earlier", a), ("later", b)):
            listed = base / "hr23-members" / f"{name}.json"
            release = earlier if side == "earlier" else later
            if release == "dr3" and listed.exists():
                check = d.agreement_with_catalogue(
                    stars, d.members(stars, c, settings), json.loads(listed.read_text("utf-8"))
                )
                result |= {f"hr23_{k}": v for k, v in check.items()}
        rows.append(result)
        _say(
            f"{name}: {result['earlier_n']} -> {result['later_n']} members, parallax {result['delta_parallax_mas']:+.4f} mas, "
            f"{result['lost']} lost, {result['new']} new, {result['unmatched']} unmatched"
        )
    target = base / f"deltas-{earlier}-{later}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        names = list(dict.fromkeys(k for r in rows for k in r)) or ["cluster"]
        writer = csv.DictWriter(handle, fieldnames=names, restval="")
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "status": "EXPLORATORY (dry run)" if later != "dr4" else "EXPLORATORY until the predeclaration is frozen",
        "pair": pair,
        "clusters": len(rows),
        "skipped_not_fetched": skipped,
        "settings_sha256": d.settings_sha256(),
        "csv": str(target),
    }
    _write_json(base / f"summary-{earlier}-{later}.json", summary)
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))
