"""``sdp companions``: hidden companions of star-cluster members in Gaia DR3 (EXPLORATORY).

The science is sdp_stellar.companions; the orbits come from sdp.adapters.archives.gaia_nss. Every result
here is a trial of the screen that the Gaia DR4 run will use, not a discovery claim.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from sdp.kernel.domain.errors import DomainError

if TYPE_CHECKING:
    import pyarrow as pa

    from sdp.adapters.archives.transport import Transport

companions_app = typer.Typer(
    help="Hidden companions (white dwarfs, neutron stars, black holes) of star-cluster members, from Gaia "
    "DR3's published orbits. Every result is EXPLORATORY.",
    no_args_is_help=True,
)

#: the native gaia_source columns a screen reads from the bulk copy
COLUMNS = (
    "source_id",
    "ra",
    "dec",
    "parallax",
    "parallax_error",
    "pmra",
    "pmra_error",
    "pmdec",
    "pmdec_error",
    "phot_g_mean_mag",
    "ruwe",
)
ORBITS_FILE = "gaia-dr3-orbits.parquet"


def _say(text: str) -> None:
    typer.echo(text, err=True)


def folder() -> Path:
    from sdp.adapters.local.sources import data_dir

    return data_dir() / "companions"


def transport() -> Transport:
    """The live Gaia TAP service, politely, waiting up to ten minutes for a busy server; tests replace this."""
    from sdp.adapters.archives.transport import PoliteTransport, UrllibTransport

    return PoliteTransport(UrllibTransport(timeout_s=600.0), min_seconds_between_calls=1.0)


def _orbits(path: Path | None) -> dict[str, Any]:
    import pyarrow.parquet as pq

    from sdp_stellar.companions import load_settings

    path = path or folder() / ORBITS_FILE
    if not path.exists():
        raise DomainError(f"no orbit table at {path}; run `sdp companions fetch` first")
    table = pq.read_table(path)
    release = (table.schema.metadata or {}).get(b"sdp.release", b"").decode()
    wanted = load_settings()["source_release"]
    if release != wanted:
        raise DomainError(f"{path} holds orbits of release {release or 'unknown'!r}, not {wanted!r}")
    return {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], empty: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        names = list(dict.fromkeys(k for r in rows for k in r)) or list(empty)
        writer = csv.DictWriter(handle, fieldnames=names, restval="")
        writer.writeheader()
        writer.writerows(rows)


@companions_app.command("fetch")
def companions_fetch(
    out: Annotated[
        Path | None, typer.Option(help="Where to write the orbit table (default <SDP data>/companions)")
    ] = None,
) -> None:
    """Download every Gaia DR3 orbit on the sky with Gaia's masses (about 169,000 rows, about 10 minutes).
    Each part is kept as it arrives, so running it again after a failure resumes where it stopped."""
    import shutil

    import pyarrow.parquet as pq

    from sdp.adapters.archives.gaia_nss import fetch_orbits

    path = out or folder() / ORBITS_FILE
    parts = path.parent / (path.stem + ".parts")
    if parts.exists():
        _say(f"resuming: the parts already downloaded are in {parts}")
    table = fetch_orbits(transport(), progress=lambda i, n: _say(f"orbits part {i}/{n}"), parts_dir=parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp)
    tmp.replace(path)
    shutil.rmtree(parts, ignore_errors=True)
    _say(f"{table.num_rows:,} orbits written to {path}")
    typer.echo(json.dumps({"orbits": table.num_rows, "path": str(path)}, indent=2, sort_keys=True))


@companions_app.command("field")
def companions_field(
    orbits: Annotated[Path | None, typer.Option(help="The orbit table (default the fetched one)")] = None,
    out: Annotated[Path | None, typer.Option(help="Folder for field.csv (default <SDP data>/companions)")] = None,
) -> None:
    """Every published orbit screened at its own distance, no clusters. The known-answer check: Gaia BH1
    (source_id 4373465352415301632) must come out a neutron star or black hole. EXPLORATORY."""
    from sdp_stellar.companions import COMPACT, field_orbits, load_settings, settings_sha256

    o = _orbits(orbits)
    rows = field_orbits(o, load_settings())
    target = (out or folder()) / "field.csv"
    _write_csv(target, rows, ["source_id", "verdict"])
    counts = {v: sum(r["verdict"] == v for r in rows) for v in sorted({r["verdict"] for r in rows})}
    bh1 = next((r for r in rows if r["source_id"] == 4373465352415301632), None)
    _say(
        f"{len(o['source_id']):,} orbits screened: "
        + (", ".join(f"{n} {k.replace('-', ' ')}" for k, n in counts.items()) or "none flagged")
        + f". Gaia BH1 {'is' if bh1 and bh1['verdict'] == COMPACT else 'is NOT'} flagged as a neutron star or black hole."
    )
    _say(f"Every row is a candidate from Gaia's published orbit, not a confirmed companion. Open {target}.")
    if bh1 is None or bh1["verdict"] != COMPACT:
        raise DomainError(
            "the known-answer check failed: Gaia BH1 (4373465352415301632) is "
            + ("missing from the orbit table" if bh1 is None else f"judged {bh1['verdict']!r}")
            + "; the screen is not trustworthy"
        )
    typer.echo(
        json.dumps(
            {
                "orbits": len(o["source_id"]),
                "flagged": counts,
                "gaia_bh1": bh1,
                "csv": str(target),
                "settings_sha256": settings_sha256(),
            },
            indent=2,
            sort_keys=True,
        )
    )


def _clusters(sweep: str | None, members: Path | None, statuses: list[str]) -> list[Any]:
    from sdp.cli.discover import sweep_dir
    from sdp_stellar.companions import clusters_from_rows, clusters_from_tiles

    if (sweep is None) == (members is None):
        raise DomainError(
            "give exactly one of --sweep (a sweep's folder name) or --members (a CSV of cluster,source_id)"
        )
    if members is not None:
        with members.open(newline="", encoding="utf-8") as handle:
            return clusters_from_rows(csv.DictReader(handle))
    assert sweep is not None
    tiles = sorted((sweep_dir(sweep) / "tiles").glob("*b[0-9][0-9][0-9]-t*.json"))
    if not tiles:
        raise DomainError(f"no swept tiles in {sweep_dir(sweep) / 'tiles'}; run `sdp discover sweep` first")
    return clusters_from_tiles((json.loads(p.read_text("utf-8")) for p in tiles), statuses)


def _scan(columns: Sequence[str], parallax_min: float, every: int | None = None) -> Iterator[pa.Table]:
    from sdp.adapters.archives.bulk import BulkCatalogueGateway
    from sdp.adapters.archives.tap import load_sources
    from sdp.adapters.bootstrap import artifact_store
    from sdp.cli.discover import _artifact_root

    bulk = BulkCatalogueGateway(artifact_store(_artifact_root()), load_sources()["gaia"].descriptor)
    if not bulk.complete():
        raise DomainError("the bulk Gaia copy is missing or incomplete; finish `sdp archive bulk` first")

    def report(done: int, total: int) -> None:
        if done % 100 == 0 or done == total:
            _say(f"gaia {done}/{total} files")

    yield from bulk.scan(columns, [("parallax", ">=", parallax_min)], progress=report)


def _arrays(tables: Sequence[pa.Table]) -> dict[str, Any]:
    import numpy as np
    import pyarrow as pa

    if not tables:
        return {c: np.zeros(0) for c in COLUMNS}
    whole = pa.concat_tables(tables).combine_chunks()
    return {name: np.asarray(whole.column(name).to_numpy(zero_copy_only=False)) for name in whole.column_names}


@companions_app.command("screen")
def companions_screen(
    sweep: Annotated[str | None, typer.Option(help="A sweep's folder under <SDP data>/sweep, e.g. gaia-dr3")] = None,
    members: Annotated[Path | None, typer.Option(help="Or a CSV with columns cluster,source_id")] = None,
    status: Annotated[
        list[str] | None, typer.Option(help="Sweep candidate statuses to screen (repeat); default all but rejected")
    ] = None,
    orbits: Annotated[Path | None, typer.Option(help="The orbit table (default the fetched one)")] = None,
    name: Annotated[
        str, typer.Option(help="Folder for this screen's results under <SDP data>/companions")
    ] = "clusters",
) -> None:
    """Find each cluster's members again (blind to RUWE), count its wobblers and dark partners, and set
    both against the field. Two passes over the bulk Gaia copy. EXPLORATORY."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc

    from sdp_stellar.companions import FieldCounts, footprint, load_settings, near_any, screen, settings_sha256

    settings = load_settings()
    statuses = status or ["potentially_new", "inconclusive", "known"]
    clusters = _clusters(sweep, members, statuses)
    if not clusters:
        raise DomainError("no clusters to screen")
    o = _orbits(orbits)
    seeds = pa.array(sorted({s for c in clusters for s in c.seed_ids}), type=pa.int64())
    plx_min = float(settings["membership"]["parallax_min_mas"])

    _say(f"pass 1 of 2: the field's wobble baseline and the seed members of {len(clusters):,} clusters")
    field = FieldCounts(settings)
    seed_tables = []
    for table in _scan(COLUMNS, plx_min):
        field.add(
            table.column("phot_g_mean_mag").to_numpy(zero_copy_only=False),
            table.column("parallax").to_numpy(zero_copy_only=False),
            table.column("ruwe").to_numpy(zero_copy_only=False),
        )
        seed_tables.append(table.filter(pc.is_in(table.column("source_id"), value_set=seeds)))
    seed_stars = _arrays(seed_tables)
    row_of = {int(s): i for i, s in enumerate(seed_stars["source_id"])}
    fps = []
    for c in clusters:
        rows = np.asarray(sorted(row_of[s] for s in c.seed_ids if s in row_of), dtype=np.int64)
        fp = footprint(seed_stars, rows, settings)
        if fp is not None:
            fps.append(fp)

    _say(f"pass 2 of 2: every star inside the footprints of {len(fps):,} clusters")
    kept = []
    for table in _scan(COLUMNS, plx_min):
        ra = table.column("ra").to_numpy(zero_copy_only=False)
        dec = table.column("dec").to_numpy(zero_copy_only=False)
        kept.append(table.filter(pa.array(near_any(fps, ra, dec))))
    stars = _arrays(kept)

    result = screen(clusters, stars, o, field, settings)
    target = folder() / name
    _write_csv(target / "candidates.csv", result["candidates"], ["cluster_id", "source_id", "verdict"])
    _write_csv(target / "clusters.csv", result["clusters"], ["cluster_id", "members"])
    flagged = [r for r in result["candidates"] if r["verdict"] in ("neutron-star-or-black-hole", "dark-partner")]
    summary = {
        "status": "EXPLORATORY",
        "settings_sha256": settings_sha256(),
        "clusters": len(clusters),
        "clusters_screened": sum(1 for c in result["clusters"] if c.get("members")),
        "members": sum(int(c.get("members") or 0) for c in result["clusters"]),
        "orbits_in_clusters": len(result["candidates"]),
        "flagged_in_clusters": len(flagged),
        "field_orbit_verdicts": result["field_orbit_verdicts"],
        "top": result["candidates"][:10],
        "folder": str(target),
    }
    (target / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), "utf-8")
    _say(
        f"{summary['clusters_screened']:,} clusters screened ({summary['members']:,} members): "
        f"{summary['orbits_in_clusters']} members with a Gaia orbit, {len(flagged)} with a dark partner."
    )
    _say(f"Every row is a candidate, not a discovery. Open {target / 'candidates.csv'} and {target / 'clusters.csv'}.")
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))
