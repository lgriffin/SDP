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
    """The live Gaia TAP service, politely, with the shared archive retry policy; tests replace this. The
    orbits come from async jobs (#199), so no one request has to stay open while the server works."""
    from sdp.adapters.archives.transport import ARCHIVE_RETRY, PoliteTransport, UrllibTransport

    return PoliteTransport.with_policy(UrllibTransport(timeout_s=180.0), ARCHIVE_RETRY)


def _orbits(path: Path | None, any_release: bool = False) -> tuple[dict[str, Any], str]:
    """The orbit table and the Gaia release it says it holds. It must be the trial settings' release,
    unless ``any_release`` (UC-06, which records the release and gives no counted verdict on another)."""
    import pyarrow.parquet as pq

    from sdp_stellar.companions import load_settings

    path = path or folder() / ORBITS_FILE
    if not path.exists():
        raise DomainError(f"no orbit table at {path}; run `sdp companions fetch` first")
    table = pq.read_table(path)
    release = (table.schema.metadata or {}).get(b"sdp.release", b"").decode()
    wanted = load_settings()["source_release"]
    if release != wanted and not (any_release and release):
        raise DomainError(f"{path} holds orbits of release {release or 'unknown'!r}, not {wanted!r}")
    return {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}, release


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

    o, _ = _orbits(orbits)
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


def _scan(columns: Sequence[str], parallax_min: float | None, every: int | None = None) -> Iterator[pa.Table]:
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

    filters = [] if parallax_min is None else [("parallax", ">=", parallax_min)]
    yield from bulk.scan(columns, filters, progress=report)


def _arrays(tables: Sequence[pa.Table]) -> dict[str, Any]:
    import numpy as np
    import pyarrow as pa

    if not tables:
        return {c: np.zeros(0) for c in COLUMNS}
    whole = pa.concat_tables(tables).combine_chunks()
    return {name: np.asarray(whole.column(name).to_numpy(zero_copy_only=False)) for name in whole.column_names}


_FIT_KEYS = (
    "log_age", "feh", "av", "distance_modulus", "on_isochrone_share", "heaviest_living_msun", "turnoff_initial_msun",
)  # fmt: skip


def _earlier_fits(path: Path, grid: Any) -> dict[str, Any]:
    """The fits of an `sdp isochrones fit` run, checked before any Gaia file is read: each a complete fit,
    made with this grid and these isochrone settings (from the run's summary.json beside it)."""
    from sdp_stellar.isochrones import settings_sha256

    try:
        fits = json.loads(path.read_text("utf-8"))
        run = json.loads((path.parent / "summary.json").read_text("utf-8"))
    except FileNotFoundError as exc:
        raise DomainError(
            f"{exc.filename} is missing; --fits takes the fits.json of an `sdp isochrones fit` run"
        ) from exc
    except json.JSONDecodeError as exc:
        raise DomainError(f"{path} or its summary.json is not JSON: {exc}") from exc
    if not isinstance(fits, dict) or not all(isinstance(v, dict) for v in fits.values()):
        raise DomainError(f"{path} is not a fits.json of `sdp isochrones fit`: a mapping of cluster id to fit")
    if bad := [k for k, v in fits.items() if any(not isinstance(v.get(f), int | float) for f in _FIT_KEYS)]:
        raise DomainError(f"{path}: the fits of {', '.join(sorted(bad)[:5])} lack {', '.join(_FIT_KEYS)}")
    ours = {"settings_sha256": settings_sha256(), "source_sha256": grid.meta.get("source_sha256")}
    theirs = {
        "settings_sha256": run.get("settings_sha256"),
        "source_sha256": (run.get("grid") or {}).get("source_sha256"),
    }
    if ours != theirs:
        raise DomainError(
            f"{path} was fitted with other isochrone settings or another MIST grid ({theirs}, now {ours}); "
            "fit the sweep again with `sdp isochrones fit` first"
        )
    return fits


def _uc06_test(
    clusters: Sequence[dict[str, Any]],
    settings: dict[str, Any],
    orbits: dict[str, Any],
    orbit_release: str,
    listing: bytes,
    grid: Any,
) -> dict[str, Any]:
    """The stacked test, and whether its verdict counts: only on the declared release's orbits and stars,
    on the predeclared cluster list, and with Gaia BH1 flagged under these same settings."""
    import hashlib

    import numpy as np

    from sdp.adapters.archives.tap import load_sources
    from sdp_stellar import companions as comp

    test = comp.stacked_test(clusters, settings)
    declared = str(settings["source_release"])
    stars_release = load_sources()["gaia"].descriptor.release_label
    _, verdicts = comp.judge(orbits, settings)
    ids = np.asarray(orbits["source_id"], dtype=np.int64)
    bh1 = bool(np.any(verdicts[ids == comp.GAIA_BH1] == comp.COMPACT))
    reasons = []
    if orbit_release != declared:
        reasons.append(f"the orbits are {orbit_release}, not {declared}")
    if stars_release != declared:
        reasons.append(f"the stars are {stars_release}, not {declared}")
    if hashlib.sha256(listing).hexdigest() != comp.UC06_CLUSTER_LIST_SHA256:
        reasons.append("the cluster list is not the predeclared one")
    if not bh1:
        reasons.append("Gaia BH1 is not flagged as a neutron star or black hole (known-answer check)")
    test["gaia_bh1_flagged"] = bh1
    test["counts"] = not reasons
    test["not_counted_because"] = reasons
    test["h2_search"] = "searched (isochrones)" if grid is not None else "not searched: run with --isochrones"
    return test


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
    isochrones: Annotated[
        bool,
        typer.Option(
            help="Also fit each cluster with a MIST isochrone (`sdp isochrones fetch` first): the members' masses "
            "on it and whether a partner is heavier than any living star of the cluster"
        ),
    ] = False,
    fits: Annotated[
        Path | None,
        typer.Option(
            help="With --isochrones: a fits.json from `sdp isochrones fit`; its clusters are not fitted again"
        ),
    ] = None,
    protocol: Annotated[
        str,
        typer.Option(
            help="Settings to screen with: 'trial' (the DR3 trial, companions.json) or 'uc06' (the predeclared "
            "Gaia DR4 settings of protocol UC-06: crowding-matched field and the stacked test)"
        ),
    ] = "trial",
) -> None:
    """Find each cluster's members again (blind to RUWE), count its wobblers and dark partners, and set
    both against the field. Two passes over the bulk Gaia copy. EXPLORATORY."""
    import hashlib

    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc

    from sdp_stellar import companions as comp
    from sdp_stellar.companions import FieldCounts, footprint, near_any, screen

    files = {"trial": comp.SETTINGS_FILE, "uc06": comp.UC06_FILE}
    if protocol not in files:
        raise DomainError(f"--protocol is 'trial' or 'uc06', not {protocol!r}")
    settings = comp.load_settings(files[protocol])
    if protocol == "uc06" and comp.frozen_sha256(settings) != comp.UC06_FROZEN_SHA256:
        raise DomainError("uc06.json differs from the predeclared UC-06 settings; it cannot be used for UC-06")
    grid = None
    columns: tuple[str, ...] = COLUMNS
    if isochrones:
        from sdp.cli.isochrones import load_grid

        grid = load_grid()
        columns = (*COLUMNS, "bp_rp")
    earlier: dict[str, Any] | None = None
    if fits is not None:
        if grid is None:
            raise DomainError("--fits needs --isochrones")
        if not fits.exists():
            raise DomainError(f"no fits at {fits}; run `sdp isochrones fit` first")
        earlier = _earlier_fits(fits, grid)
    statuses = status or ["potentially_new", "inconclusive", "known"]
    clusters = _clusters(sweep, members, statuses)
    if not clusters:
        raise DomainError("no clusters to screen")
    o, orbit_release = _orbits(orbits, any_release=protocol == "uc06")
    seeds = pa.array(sorted({s for c in clusters for s in c.seed_ids}), type=pa.int64())
    plx_min = float(settings["membership"]["parallax_min_mas"])

    density = None
    if settings.get("crowding"):
        density = comp.SourceDensity(int(settings["crowding"]["healpix_level"]))
        _say("pass 0: how crowded every patch of sky is (every Gaia source, any parallax)")
        for table in _scan(("source_id",), None):
            density.add(table.column("source_id").to_numpy(zero_copy_only=False))

    def crowd(table: pa.Table) -> pa.Table:
        if density is None:
            return table
        ids = table.column("source_id").to_numpy(zero_copy_only=False)
        return table.append_column("crowding", pa.array(density.of(ids), type=pa.int64()))

    _say(f"pass 1 of 2: the field's wobble baseline and the seed members of {len(clusters):,} clusters")
    field = FieldCounts(settings)
    seed_tables = []
    for table in _scan(columns, plx_min):
        table = crowd(table)
        field.add(
            table.column("phot_g_mean_mag").to_numpy(zero_copy_only=False),
            table.column("parallax").to_numpy(zero_copy_only=False),
            table.column("ruwe").to_numpy(zero_copy_only=False),
            table.column("crowding").to_numpy(zero_copy_only=False) if density is not None else None,
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
    for table in _scan(columns, plx_min):
        table = crowd(table)
        ra = table.column("ra").to_numpy(zero_copy_only=False)
        dec = table.column("dec").to_numpy(zero_copy_only=False)
        kept.append(table.filter(pa.array(near_any(fps, ra, dec))))
    stars = _arrays(kept)

    result = screen(clusters, stars, o, field, settings, grid, earlier)
    target = folder() / name
    _write_csv(target / "candidates.csv", result["candidates"], ["cluster_id", "source_id", "verdict"])
    _write_csv(target / "clusters.csv", result["clusters"], ["cluster_id", "members"])
    flagged = [r for r in result["candidates"] if r["verdict"] in ("neutron-star-or-black-hole", "dark-partner")]
    listing = json.dumps(
        {c.cluster_id: sorted(c.seed_ids) for c in clusters}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    (target / "cluster-list.json").write_bytes(listing)
    summary = {
        "status": "EXPLORATORY",
        "protocol": settings.get("protocol"),
        "settings_file": files[protocol],
        "settings_sha256": comp.settings_sha256(files[protocol]),
        "frozen_sha256": comp.frozen_sha256(settings),
        "cluster_list_sha256": hashlib.sha256(listing).hexdigest(),
        "clusters": len(clusters),
        "clusters_screened": sum(1 for c in result["clusters"] if c.get("members")),
        "clusters_out_of_scope": sum(1 for c in result["clusters"] if c.get("out_of_scope")),
        **(
            {"test": _uc06_test(result["clusters"], settings, o, orbit_release, listing, grid)}
            if "test" in settings
            else {}
        ),
        "members": sum(int(c.get("members") or 0) for c in result["clusters"]),
        "orbits_in_clusters": len(result["candidates"]),
        "flagged_in_clusters": len(flagged),
        "field_orbit_verdicts": result["field_orbit_verdicts"],
        "top": result["candidates"][:10],
        "folder": str(target),
    }
    if grid is not None:
        from sdp_stellar.isochrones import settings_sha256 as iso_sha256

        heavy = [r for r in result["candidates"] if r.get("heavier_than_living")]
        summary["isochrones"] = {
            "settings_sha256": iso_sha256(),
            "grid": {k: grid.meta.get(k) for k in ("mist_version", "mesa_revision", "source_sha256")},
            "clusters_fitted": sum(1 for c in result["clusters"] if "iso_log_age" in c),
            "fits_reused": sum(1 for c in result["clusters"] if c.get("iso_from")),
            "partners_heavier_than_living": len(heavy),
        }
    (target / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), "utf-8")
    _say(
        f"{summary['clusters_screened']:,} clusters screened ({summary['members']:,} members): "
        f"{summary['orbits_in_clusters']} members with a Gaia orbit, {len(flagged)} with a dark partner."
    )
    _say(f"Every row is a candidate, not a discovery. Open {target / 'candidates.csv'} and {target / 'clusters.csv'}.")
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))
