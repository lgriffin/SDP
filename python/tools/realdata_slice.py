"""Cut a committed slice of a real data table for the T8R tier (TST-011).

The slice is chosen by rule, never by hand, so anyone can cut it again from the same full table and get the
same bytes. The full table stays where it was pulled (the project share, or SDP_REALDATA on the PC).

    uv run python tools/realdata_slice.py nss-orbits <gaia-dr3-orbits.parquet> <out.parquet>
    uv run python tools/realdata_slice.py sweep-tile <tile_id> <out folder> [--sweep gaia-dr3]
    uv run python tools/realdata_slice.py sweep-cluster "NGC 2547" <out folder> [--sweep gaia-dr3]

``nss-orbits`` keeps, from the full Gaia DR3 orbit table the companion screen reads:

- every orbit the screen flags (a dark partner or a neutron star or black hole), so the field list is whole;
- every orbit near one of the screen's cuts (significance, parallax over error, the Chandrasekhar mass),
  where a change of code is most likely to move a verdict;
- every orbit whose source_id is a multiple of 50, for the ordinary, no-mass and unused verdicts.

Only the columns the screen reads are kept. The settings file is read, never changed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from sdp_stellar import companions as c

NSS_COLUMNS = (
    "source_id",
    "nss_solution_type",
    "combination_method",
    "period",
    "a_thiele_innes",
    "b_thiele_innes",
    "f_thiele_innes",
    "g_thiele_innes",
    "parallax",
    "parallax_error",
    "significance",
    "phot_g_mean_mag",
    "m1",
    "m2",
    "m2_lower",
    "fluxratio_upper",
    "flag",
)
#: an orbit within this fraction of a cut counts as near it
NEAR = 0.2
EVERY = 50


def nss_keep(orbits: dict[str, np.ndarray]) -> np.ndarray:
    """The rows of the slice, as a boolean mask over the full table."""
    settings = c.load_settings()
    masses, verdict = c.judge(orbits, settings)
    o, m = settings["orbits"], settings["mass"]

    def near(x: np.ndarray, cut: float) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        return np.isfinite(x) & (np.abs(x - cut) <= NEAR * cut)

    with np.errstate(divide="ignore", invalid="ignore"):
        poe = np.asarray(orbits["parallax"], dtype=float) / np.asarray(orbits["parallax_error"], dtype=float)
    keep = np.isin(verdict, c.FLAGGED)
    keep |= near(orbits["significance"], float(o["significance_min"]))
    keep |= near(poe, float(o["parallax_over_error_min"]))
    keep |= near(masses["m2_lower_msun"], float(m["chandrasekhar_msun"]))
    keep |= np.asarray(orbits["source_id"]) % EVERY == 0
    return keep


def nss_orbits(src: Path, out: Path) -> int:
    table = pq.read_table(src, columns=list(NSS_COLUMNS))
    orbits = {n: table.column(n).to_numpy(zero_copy_only=False) for n in table.column_names}
    keep = nss_keep(orbits)
    sliced = table.filter(pa.array(keep)).sort_by("source_id")
    meta = dict(pq.read_schema(src).metadata or {})
    meta[b"sdp.slice"] = (
        f"tools/realdata_slice.py nss-orbits: flagged, within {NEAR:.0%} of a cut, or source_id % {EVERY} == 0; "
        f"{int(keep.sum())} of {len(keep)} orbits"
    ).encode()
    sliced = sliced.replace_schema_metadata(meta)
    write(sliced, out)
    return int(keep.sum())


def write(table: pa.Table, out: Path) -> None:
    # fixed writer options so the same input gives the same bytes
    pq.write_table(table, out, compression="zstd", compression_level=9, write_statistics=False, store_schema=True)


def sweep_tile(tile_id: str, out: Path, sweep_name: str = "gaia-dr3") -> dict[str, object]:
    """Everything one swept tile read, exactly as the sweep handed it over, and what the sweep wrote for it.

    Writes ``<out>/<tile_id>/``: ``stars.parquet`` (the prepared stars of the tile and its rim inside the
    scale's parallax window, the rows ``sdp discover sweep`` passed to the tile), ``benchmark-<id>.parquet``
    for each stored known-cluster catalogue, ``tile.json`` (the sweep's own result for the tile, the known
    answer), ``sweep-settings.json`` (the tiles' settings guard) and ``manifest.json`` with every SHA-256.
    Needs the PC's SDP data folder (SDP_DATA) and artifact store (SDP_ARTIFACT_ROOT)."""
    import hashlib
    import json
    import shutil

    from sdp.adapters.bootstrap import artifact_store
    from sdp.cli.discover import _artifact_root, _band_table, _load_benchmarks, sweep_dir
    from sdp_stellar import sweep as sw

    settings = sw.load_settings()
    scale_name, band_part, index_part = tile_id.rsplit("-", 2) if tile_id.count("-") >= 2 else ("", *tile_id.split("-"))
    band, index = int(band_part.removeprefix("b")), int(index_part.removeprefix("t"))
    scale = next(x for x in sw.scales(settings) if x.name == scale_name)
    t = sw.tile(band, index, scale.tile_deg, scale.name)
    if t.tile_id != tile_id:
        raise SystemExit(f"{tile_id} parsed as {t.tile_id}")
    folder = sweep_dir(sweep_name)
    recorded = folder / "tiles" / f"{tile_id}.json"
    if not recorded.exists():
        raise SystemExit(f"the sweep has no result for {tile_id} at {recorded}")
    first = sw.tile(band, 0, scale.tile_deg, scale.name)
    reach = sw.band_of(
        np.array([first.dec_min - scale.margin_deg, first.dec_max + scale.margin_deg]), float(settings["tile_deg"])
    )
    table = _band_table(
        folder / "catalogue", list(range(int(reach[0]), int(reach[1]) + 1)), scale.window if scale.name else None
    )
    if table is None:
        raise SystemExit(f"no prepared stars for {tile_id}")
    ra, dec = sw.ra_dec(table)
    rows = table.filter(pa.array(sw.neighbourhood(t, scale.margin_deg, ra, dec)))
    benchmarks, missing, _ = _load_benchmarks(artifact_store(_artifact_root()), list(settings["benchmarks"]))
    if missing:
        raise SystemExit(f"no stored benchmark catalogues {missing}")

    target = out / tile_id
    target.mkdir(parents=True, exist_ok=True)
    write(rows, target / "stars.parquet")
    for b in benchmarks:
        write(b.table, target / f"benchmark-{b.benchmark_id}.parquet")
    shutil.copyfile(recorded, target / "tile.json")
    shutil.copyfile(folder / "tiles" / "_settings.json", target / "sweep-settings.json")
    files = sorted(p for p in target.iterdir() if p.name != "manifest.json")
    manifest = {
        "tile_id": tile_id,
        "stars": rows.num_rows,
        "benchmarks": [b.benchmark_id for b in benchmarks],
        "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        "bytes": sum(p.stat().st_size for p in files),
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", "utf-8")
    return manifest


def _plain(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def cluster_tile(name: str) -> str:
    """The tile of the sweep that owns a known cluster: the shell its catalogue parallax falls in, and the tile
    of that shell holding its catalogue centre. hr23 is asked first, then cga20."""
    from sdp.adapters.bootstrap import artifact_store
    from sdp.cli.discover import _artifact_root, _load_benchmarks
    from sdp_stellar import sweep as sw

    settings = sw.load_settings()
    benchmarks, _, _ = _load_benchmarks(artifact_store(_artifact_root()), list(settings["benchmarks"]))
    for b in benchmarks:
        names = [_plain(str(x)) for x in b.table.column("name").to_pylist()]
        if _plain(name) not in names:
            continue
        i = names.index(_plain(name))
        ra = float(b.table.column("ra_deg")[i].as_py())
        dec = float(b.table.column("dec_deg")[i].as_py())
        plx = float(b.table.column("parallax_mas")[i].as_py()) if "parallax_mas" in b.table.column_names else np.nan
        shells = sw.scales(settings)
        scale = next((x for x in shells if x.owns(np.array([plx]))[0]), shells[0])
        band = int(sw.band_of(np.array([dec]), scale.tile_deg)[0])
        n = sw.tiles_in_band(band, scale.tile_deg)
        t = next(t for t in (sw.tile(band, k, scale.tile_deg, scale.name) for k in range(n)) if t.contains(ra, dec))
        return t.tile_id
    raise SystemExit(f"{name} is in no stored known-cluster catalogue")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="what", required=True)
    nss = sub.add_parser("nss-orbits", help="slice the Gaia DR3 orbit table the companion screen reads")
    nss.add_argument("src", type=Path)
    nss.add_argument("out", type=Path)
    tile = sub.add_parser("sweep-tile", help="export one swept tile's input stars, benchmarks and recorded result")
    tile.add_argument("tile_id")
    tile.add_argument("out", type=Path)
    tile.add_argument("--sweep", default="gaia-dr3")
    named = sub.add_parser("sweep-cluster", help="sweep-tile for the tile that owns a known cluster")
    named.add_argument("name")
    named.add_argument("out", type=Path)
    named.add_argument("--sweep", default="gaia-dr3")
    args = parser.parse_args()
    if args.what == "sweep-cluster":
        import json

        tile_id = cluster_tile(args.name)
        print(json.dumps({"cluster": args.name, **sweep_tile(tile_id, args.out, args.sweep)}, indent=2))
    elif args.what == "nss-orbits":
        n = nss_orbits(args.src, args.out)
        print(f"{n} orbits -> {args.out}")
    else:
        import json

        print(json.dumps(sweep_tile(args.tile_id, args.out, args.sweep), indent=2))


if __name__ == "__main__":
    main()
