"""``sdp isochrones``: MIST isochrones (built with MESA) fitted to star clusters, and stellar masses
from Gaia photometry (STL-033 to STL-036). EXPLORATORY.

The science is sdp_stellar.isochrones. The output folder follows how MESA lays out a run: one header of
global values and provenance (``summary.json``, like the header block of MESA's ``history.data``), one
row per cluster (``clusters.csv``, like history's one row per model), one table per cluster of its stars
(``members/<cluster>.csv``, like a ``profileN.data``), and the same standard plots for every cluster
(``plots/<cluster>.svg``, like pgstar's): the colour-magnitude diagram with the fitted isochrone, and
the cost of the best fit at every age and metallicity tried.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from sdp.kernel.domain.errors import DomainError

if TYPE_CHECKING:
    import pyarrow as pa

    from sdp_stellar.isochrones import Grid

isochrones_app = typer.Typer(
    help="MIST isochrones (MESA models) fitted to star clusters' Gaia colour-magnitude diagrams, and "
    "stellar masses and ages from Gaia photometry. Every result is EXPLORATORY.",
    no_args_is_help=True,
)

GRID_FILE = "mist-v1.2-gaia.parquet"
#: the native gaia_source columns a fit reads from the bulk copy
COLUMNS = ("source_id", "parallax", "parallax_error", "phot_g_mean_mag", "bp_rp")


def _say(text: str) -> None:
    typer.echo(text, err=True)


def folder() -> Path:
    from sdp.adapters.local.sources import data_dir

    return data_dir() / "isochrones"


def download(url: str, target: Path) -> None:
    """Stream a URL to a file (replaced by tests). The MIST tarball is about 116 MB."""
    import shutil
    import urllib.request

    tmp = target.with_suffix(target.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "sdp-isochrones"})
    with urllib.request.urlopen(req, timeout=600) as response, tmp.open("wb") as out:
        shutil.copyfileobj(response, out, length=1 << 20)
    tmp.replace(target)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_tarball(path: Path, suffix: str) -> Iterator[tuple[str, str]]:
    """(member name, text) of every member of a (compressed) tarball whose name ends with ``suffix``."""
    import tarfile

    with tarfile.open(path, mode="r:*") as tar:
        for member in sorted(tar.getmembers(), key=lambda m: m.name):
            if member.isfile() and member.name.endswith(suffix):
                handle = tar.extractfile(member)
                if handle is not None:
                    yield member.name, handle.read().decode("utf-8")


def load_grid(path: Path | None = None) -> Grid:
    import pyarrow.parquet as pq

    from sdp_stellar.isochrones import Grid

    path = path or folder() / GRID_FILE
    if not path.exists():
        raise DomainError(f"no isochrone grid at {path}; run `sdp isochrones fetch` first")
    return Grid.from_table(pq.read_table(path))


@isochrones_app.command("fetch")
def isochrones_fetch(
    file: Annotated[
        Path | None, typer.Option(help="A MIST tarball already downloaded (default: download it from mist.science)")
    ] = None,
    out: Annotated[Path | None, typer.Option(help="Where to write the grid (default <SDP data>/isochrones)")] = None,
) -> None:
    """Download MIST v1.2's isochrones with Gaia photometry (about 116 MB) and keep the columns SDP needs
    as one table, with the MIST version, MESA revision and the tarball's SHA-256 recorded in it."""
    import pyarrow.parquet as pq

    from sdp_stellar.isochrones import MistFormatError, grid_from_files, load_settings, parse_cmd

    settings = load_settings()
    url = str(settings["source"]["url"])
    target = out or folder() / GRID_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    if file is None:
        file = target.parent / url.rsplit("/", 1)[1]
        if file.exists():
            _say(f"using the tarball already at {file}")
        else:
            _say(f"downloading {url}")
            download(url, file)
    if not file.exists():
        raise DomainError(f"no file at {file}")
    names: list[str] = []
    parsed = []
    try:
        for name, text in read_tarball(file, str(settings["source"]["member_suffix"])):
            names.append(name.rsplit("/", 1)[-1])
            parsed.append(parse_cmd(text))
            _say(f"read {names[-1]}")
        if not parsed:
            raise DomainError(f"{file} holds no {settings['source']['member_suffix']} files")
        grid = grid_from_files(parsed, settings)
    except MistFormatError as exc:
        raise DomainError(f"{file}: {exc}") from exc
    grid.meta.update({"source_url": url if file.name == url.rsplit("/", 1)[1] else str(file.name)})
    grid.meta.update({"source_sha256": _sha256(file), "files": ",".join(names)})
    tmp = target.with_suffix(".parquet.tmp")
    pq.write_table(grid.to_table(), tmp)
    tmp.replace(target)
    fehs = grid.fehs()
    report: dict[str, Any] = {
        "path": str(target),
        "rows": grid.size,
        "feh": fehs,
        "ages": len(grid.ages(fehs[0])),
        **{k: grid.meta.get(k) for k in ("mist_version", "mesa_revision", "photometric_system", "source_sha256")},
    }
    _say(f"{grid.size:,} isochrone points at {len(fehs)} metallicities written to {target}")
    typer.echo(json.dumps(report, indent=2, sort_keys=True, default=str))


# --- clusters --------------------------------------------------------------------------------------


def _float(row: Mapping[str, str], key: str) -> float:
    try:
        return float(row.get(key) or "nan")
    except ValueError:
        return math.nan


def _from_star_rows(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    needed = ("cluster", "source_id", "phot_g_mean_mag", "bp_rp", "parallax", "parallax_error")
    if rows and (missing := [k for k in needed if k not in rows[0]]):
        raise DomainError(f"the stars CSV lacks columns {missing}")
    groups: dict[str, list[Mapping[str, str]]] = {}
    for r in rows:
        groups.setdefault(r["cluster"], []).append(r)
    return [
        {
            "cluster_id": name,
            "matches": "",
            "source_id": [int(r["source_id"]) for r in g],
            **{k: [_float(r, k) for r in g] for k in needed[2:]},
        }
        for name, g in sorted(groups.items())
    ]


def _from_bulk(sweep: str | None, members: Path | None, statuses: list[str]) -> list[dict[str, Any]]:
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc

    from sdp.cli.companions import _arrays, _clusters, _scan

    clusters = _clusters(sweep, members, statuses)
    if not clusters:
        raise DomainError("no clusters to fit")
    seeds = pa.array(sorted({s for c in clusters for s in c.seed_ids}), type=pa.int64())
    _say(f"one pass over the bulk Gaia copy for the members of {len(clusters):,} clusters")
    tables: list[pa.Table] = [t.filter(pc.is_in(t.column("source_id"), value_set=seeds)) for t in _scan(COLUMNS, 0.0)]
    stars = _arrays(tables) if tables else {c: np.zeros(0) for c in COLUMNS}
    row_of = {int(s): i for i, s in enumerate(stars["source_id"])}
    out = []
    for c in clusters:
        rows = [row_of[s] for s in sorted(c.seed_ids) if s in row_of]
        out.append(
            {
                "cluster_id": c.cluster_id,
                "matches": c.matches,
                "source_id": [int(stars["source_id"][r]) for r in rows],
                **{k: [float(stars[k][r]) for r in rows] for k in COLUMNS[1:]},
            }
        )
    return out


def cluster_parallax(parallax: Sequence[float], error: Sequence[float]) -> tuple[float, float] | None:
    """Inverse-variance mean parallax, and the larger of its formal error and the members' robust spread
    over the square root of their number (a cluster has depth); None when no member has one."""
    import numpy as np

    p, e = np.asarray(parallax, dtype=float), np.asarray(error, dtype=float)
    ok = np.isfinite(p) & np.isfinite(e) & (e > 0)
    if ok.sum() < 2:
        return None
    w = 1.0 / e[ok] ** 2
    mean = float(np.sum(w * p[ok]) / np.sum(w))
    spread = 1.4826 * float(np.median(np.abs(p[ok] - np.median(p[ok])))) / math.sqrt(int(ok.sum()))
    return mean, max(1.0 / math.sqrt(float(np.sum(w))), spread)


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "cluster"


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], empty: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        names = list(dict.fromkeys(k for r in rows for k in r)) or list(empty)
        writer = csv.DictWriter(handle, fieldnames=names, restval="")
        writer.writeheader()
        writer.writerows(rows)


def fit_one(
    cluster: Mapping[str, Any], grid: Grid, settings: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    """(clusters.csv row, members rows, the fit or None) for one cluster."""
    import numpy as np

    from sdp_stellar.isochrones import fit_cluster, known_answer, member_masses

    names = [
        str(cluster["cluster_id"]),
        *[m.strip() for m in str(cluster.get("matches") or "").split(",") if m.strip()],
    ]
    row: dict[str, Any] = {
        "cluster_id": cluster["cluster_id"],
        "matches": cluster.get("matches", ""),
        "members": len(cluster["source_id"]),
    }
    plx = cluster_parallax(cluster["parallax"], cluster["parallax_error"])
    fit = None
    if plx is None:
        row["note"] = "no usable parallaxes"
    else:
        try:
            fit = fit_cluster(
                np.asarray(cluster["bp_rp"]), np.asarray(cluster["phot_g_mean_mag"]), plx[0], plx[1], grid, settings
            )
        except ValueError as exc:
            row["note"] = str(exc)
        if fit is None and "note" not in row:
            row["note"] = f"fewer than {settings['fit']['min_stars']} members with a colour and G"
    members: list[dict[str, Any]] = []
    if fit is not None:
        row.update({k: fit[k] for k in (
            "feh", "log_age", "age_myr", "av", "e_bp_rp", "distance_modulus", "distance_pc", "parallax_modulus",
            "parallax_modulus_err", "stars_fitted", "on_isochrone_share", "median_distance", "cost",
            "turnoff_initial_msun", "heaviest_living_msun",
        )})  # fmt: skip
        for k, (lo, hi) in fit["ranges"].items():
            row[f"{k}_lo"], row[f"{k}_hi"] = lo, hi
        on = member_masses(np.asarray(cluster["bp_rp"]), np.asarray(cluster["phot_g_mean_mag"]), fit, grid, settings)
        phase_names = settings["phases"]["names"]
        for i, sid in enumerate(cluster["source_id"]):
            g, c = cluster["phot_g_mean_mag"][i], cluster["bp_rp"][i]
            ph = on["phase"][i]
            members.append(
                {
                    "source_id": sid,
                    "phot_g_mean_mag": g,
                    "bp_rp": c,
                    "abs_g0": _r(g - fit["a_g"] - fit["distance_modulus"]),
                    "bp_rp0": _r(c - fit["e_bp_rp"]),
                    "initial_mass_msun": _r(on["initial_mass"][i]),
                    "star_mass_msun": _r(on["star_mass"][i]),
                    "phase": "" if not math.isfinite(ph) else phase_names.get(str(round(ph)), str(round(ph))),
                    "distance": _r(on["distance"][i]),
                    "on_isochrone": bool(on["on_isochrone"][i]),
                }
            )
    check = None if fit is None else known_answer(names, fit, settings)
    if check is not None:
        row["known_answer"] = f"{check['cluster']} log age {check['window_log_age']}: " + (
            "inside" if check["inside"] else "OUTSIDE"
        )
    return row, members, (None if fit is None else {**fit, "known_answer": check})


def _provenance(grid: Grid) -> dict[str, Any]:
    return {
        k: grid.meta.get(k)
        for k in ("mist_version", "mesa_revision", "photometric_system", "source_url", "source_sha256")
    }


def _r(x: float, digits: int = 4) -> float | None:
    return round(float(x), digits) if math.isfinite(float(x)) else None


@isochrones_app.command("fit")
def isochrones_fit(
    stars: Annotated[
        Path | None,
        typer.Option(
            help="A CSV of cluster,source_id,phot_g_mean_mag,bp_rp,parallax,parallax_error (no Gaia copy needed)"
        ),
    ] = None,
    sweep: Annotated[str | None, typer.Option(help="Or a sweep's folder under <SDP data>/sweep, e.g. gaia-dr3")] = None,
    members: Annotated[Path | None, typer.Option(help="Or a CSV with columns cluster,source_id")] = None,
    status: Annotated[
        list[str] | None, typer.Option(help="Sweep candidate statuses to fit (repeat); default all but rejected")
    ] = None,
    grid_path: Annotated[Path | None, typer.Option("--grid", help="The grid (default the fetched one)")] = None,
    name: Annotated[str, typer.Option(help="Folder for the results under <SDP data>/isochrones")] = "clusters",
) -> None:
    """Fit every cluster's members with one MIST isochrone: age, [Fe/H], extinction and distance, each
    member's mass on it, and the standard plots. EXPLORATORY."""
    from sdp_stellar.isochrones import load_settings, settings_sha256

    if sum(x is not None for x in (stars, sweep, members)) != 1:
        raise DomainError("give exactly one of --stars, --sweep or --members")
    settings = load_settings()
    grid = load_grid(grid_path)
    if stars is not None:
        with stars.open(newline="", encoding="utf-8") as handle:
            clusters = _from_star_rows(list(csv.DictReader(handle)))
    else:
        clusters = _from_bulk(sweep, members, status or ["potentially_new", "inconclusive", "known"])
    target = folder() / name
    rows, fits = [], {}
    for i, c in enumerate(clusters, 1):
        row, member_rows, fit = fit_one(c, grid, settings)
        rows.append(row)
        safe = _safe(str(c["cluster_id"]))
        if fit is not None:
            fits[str(c["cluster_id"])] = fit
            _write_csv(target / "members" / f"{safe}.csv", member_rows, ["source_id"])
            (target / "plots").mkdir(parents=True, exist_ok=True)
            (target / "plots" / f"{safe}.svg").write_text(
                plot_svg(str(c["cluster_id"]), member_rows, fit, grid, settings), "utf-8"
            )
        _say(
            f"[{i}/{len(clusters)}] {c['cluster_id']}: "
            + (
                f"log age {fit['log_age']}, [Fe/H] {fit['feh']}, A_V {fit['av']}, {fit['on_isochrone_share']:.0%} on the isochrone"
                if fit
                else row.get("note", "not fitted")
            )
        )
    _write_csv(target / "clusters.csv", rows, ["cluster_id", "members"])
    checks = [f["known_answer"] for f in fits.values() if f.get("known_answer")]
    summary = {
        "status": "EXPLORATORY",
        "schema": "sdp-stellar/isochrone-run@1",
        "settings_sha256": settings_sha256(),
        "grid": {
            k: grid.meta.get(k)
            for k in ("mist_version", "mesa_revision", "photometric_system", "source_url", "source_sha256")
        },
        "clusters": len(clusters),
        "fitted": len(fits),
        "known_answers": checks,
        "known_answers_verified": bool(settings["known_answers"]["verified"]),
        "folder": str(target),
        "files": {"clusters": "clusters.csv", "members": "members/<cluster>.csv", "plots": "plots/<cluster>.svg"},
    }
    (target / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), "utf-8")
    (target / "fits.json").write_text(json.dumps(fits, indent=1, sort_keys=True), "utf-8")
    outside = [c for c in checks if not c["inside"]]
    _say(f"{len(fits):,} of {len(clusters):,} clusters fitted. Open {target / 'clusters.csv'} and {target / 'plots'}.")
    for c in checks:
        _say(
            f"known answer: {c['cluster']} fitted log age {c['fitted_log_age']}, window {c['window_log_age']}: {'inside' if c['inside'] else 'OUTSIDE'}"
        )
    if outside:
        _say("WARNING: a famous cluster's age is outside its sanity window; read its plot before trusting the run.")
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


# --- field stars ---------------------------------------------------------------------------------


@isochrones_app.command("stars")
def isochrones_stars(
    stars: Annotated[Path, typer.Option(help="A CSV of source_id,phot_g_mean_mag,bp_rp,parallax and optional a_v")],
    feh: Annotated[float | None, typer.Option(help="[Fe/H] of the grid used (default the settings', 0.0)")] = None,
    grid_path: Annotated[Path | None, typer.Option("--grid", help="The grid (default the fetched one)")] = None,
    out: Annotated[Path | None, typer.Option(help="The CSV to write (default <SDP data>/isochrones/stars.csv)")] = None,
) -> None:
    """Mass and age of stars with no cluster, each from its own parallax and photometry, with the 16th to
    84th percentile range. An age from one star is usually poorly fixed; a mass much better. EXPLORATORY."""
    import numpy as np

    from sdp_stellar.isochrones import extinction, field_star_estimates, load_settings, settings_sha256

    settings = load_settings()
    grid = load_grid(grid_path)
    with stars.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if rows and (missing := [k for k in ("source_id", "phot_g_mean_mag", "bp_rp", "parallax") if k not in rows[0]]):
        raise DomainError(f"the stars CSV lacks columns {missing}")
    zp = float(settings["parallax"]["zero_point_mas"])
    plx = np.asarray([_float(r, "parallax") for r in rows]) - zp
    av = np.asarray([_float(r, "a_v") if r.get("a_v") else 0.0 for r in rows])
    a_g, e_c = extinction(av, settings)
    with np.errstate(divide="ignore", invalid="ignore"):
        abs_g = np.asarray([_float(r, "phot_g_mean_mag") for r in rows]) + 5 * np.log10(plx / 100.0) - a_g
    abs_g = np.where(plx > 0, abs_g, np.nan)
    colour0 = np.asarray([_float(r, "bp_rp") for r in rows]) - e_c
    apparent = np.asarray([_float(r, "phot_g_mean_mag") for r in rows])
    plx_err = np.asarray([_float(r, "parallax_error") if r.get("parallax_error") else 0.0 for r in rows])
    with np.errstate(divide="ignore", invalid="ignore"):
        modulus_err = 5.0 / math.log(10.0) * np.hypot(plx_err, float(settings["parallax"]["systematic_mas"])) / plx
    estimates = field_star_estimates(abs_g, colour0, grid, settings, feh, apparent, modulus_err)
    phase_names = settings["phases"]["names"]
    table = []
    for r, e, mg in zip(rows, estimates, abs_g, strict=True):
        row: dict[str, Any] = {
            "source_id": r["source_id"],
            "abs_g0": _r(mg),
            "feh": e["feh"],
            "off_grid": e["off_grid"],
        }
        for key in ("initial_mass_msun", "star_mass_msun", "log_age"):
            for q, v in zip(("p16", "p50", "p84"), e.get(key, [None] * 3), strict=True):
                row[f"{key}_{q}"] = v
        row["phase"] = phase_names.get(str(e["phase"]), "") if "phase" in e else ""
        row["nearest_distance"] = e.get("nearest_distance")
        table.append(row)
    target = out or folder() / "stars.csv"
    _write_csv(target, table, ["source_id"])
    summary = {
        "status": "EXPLORATORY",
        "stars": len(table),
        "off_grid": sum(1 for t in table if t["off_grid"]),
        "csv": str(target),
        "settings_sha256": settings_sha256(),
        "grid": _provenance(grid),
    }
    target.with_suffix(".json").write_text(json.dumps(summary, indent=2, sort_keys=True), "utf-8")
    _say(f"{len(table):,} stars estimated, {summary['off_grid']} off the grid. Open {target}.")
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


# --- plots ---------------------------------------------------------------------------------------


def _scaler(lo: float, hi: float, a: float, b: float) -> Callable[[float], float]:
    span = (hi - lo) or 1.0
    return lambda v: a + (v - lo) / span * (b - a)


def plot_svg(
    title: str, members: Sequence[Mapping[str, Any]], fit: Mapping[str, Any], grid: Grid, settings: Mapping[str, Any]
) -> str:
    """Two panels, as pgstar shows the same plots for every run: left, the members' dereddened
    colour-magnitude diagram (absolute G down the axis, bright at the top) with the fitted isochrone,
    members off it drawn hollow; right, the best cost at each age and [Fe/H] tried, darker is better."""
    from html import escape

    import numpy as np

    from sdp_stellar.isochrones import line

    ln = line(grid.isochrone(float(fit["feh"]), float(fit["log_age"])), settings["phases"]["fit"], 0.05)
    pts = [
        (m["bp_rp0"], m["abs_g0"], m["on_isochrone"])
        for m in members
        if m["bp_rp0"] is not None and m["abs_g0"] is not None
    ]
    xs = [p[0] for p in pts] or [0.0, 1.0]
    ys = [p[1] for p in pts] or [0.0, 10.0]
    x0, x1 = min(xs) - 0.2, max(xs) + 0.2
    y0, y1 = min(ys) - 0.5, max(ys) + 0.5
    sx, sy = _scaler(x0, x1, 60, 400), _scaler(y0, y1, 50, 470)  # bright (small G) at the top
    w, h = 820, 520
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="sans-serif" font-size="12">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="20" y="22" font-size="15" font-weight="bold">{escape(title)}</text>',
        f'<text x="20" y="40" fill="#444">log age {fit["log_age"]} ({fit["age_myr"]:,} Myr), [Fe/H] {fit["feh"]}, A_V {fit["av"]}, '
        f"m-M {fit['distance_modulus']} ({fit['distance_pc']:,} pc), {fit['on_isochrone_share']:.0%} of {fit['stars_fitted']} on the isochrone. EXPLORATORY</text>",
        '<rect x="60" y="50" width="340" height="420" fill="none" stroke="#999"/>',
    ]
    # one polyline per phase, since the isochrone is not drawn across a phase change
    breaks = np.nonzero(np.diff(ln.phase) != 0)[0] + 1
    for part in np.split(np.arange(ln.size), breaks):
        keep = [(sx(ln.colour[i]), sy(ln.mag[i])) for i in part if x0 <= ln.colour[i] <= x1 and y0 <= ln.mag[i] <= y1]
        if len(keep) > 1:
            out.append(
                '<polyline fill="none" stroke="#c0392b" stroke-width="1.5" points="'
                + " ".join(f"{a:.1f},{b:.1f}" for a, b in keep)
                + '"/>'
            )
    for c, m, on in pts:
        fill = "#1f4e79" if on else "none"
        out.append(
            f'<circle cx="{sx(c):.1f}" cy="{sy(m):.1f}" r="2.2" fill="{fill}" stroke="#1f4e79" stroke-width="0.8"/>'
        )
    for v in _ticks(x0, x1):
        out.append(f'<text x="{sx(v):.1f}" y="486" text-anchor="middle">{v:g}</text>')
    for v in _ticks(y0, y1):
        out.append(f'<text x="54" y="{sy(v) + 4:.1f}" text-anchor="end">{v:g}</text>')
    out.append('<text x="230" y="506" text-anchor="middle">(BP-RP)0</text>')
    out.append('<text x="18" y="260" transform="rotate(-90 18 260)" text-anchor="middle">M_G</text>')

    surf = fit.get("surface") or []
    if surf:
        ages = sorted({s["log_age"] for s in surf})
        fehs = sorted({s["feh"] for s in surf})
        lo = min(s["cost"] for s in surf)
        hi = max(s["cost"] for s in surf)
        cw, ch = 330 / max(len(ages), 1), 420 / max(len(fehs), 1)
        out.append('<text x="645" y="44" text-anchor="middle">best cost by age and [Fe/H]</text>')
        for s in surf:
            shade = 0.15 + 0.85 * min(math.log1p(s["cost"] - lo) / math.log1p(max(hi - lo, 1e-9)), 1.0)
            grey = int(255 * shade)
            x = 470 + ages.index(s["log_age"]) * cw
            y = 50 + (len(fehs) - 1 - fehs.index(s["feh"])) * ch
            out.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cw + 0.3:.1f}" height="{ch + 0.3:.1f}" fill="rgb({grey},{grey},{grey})"/>'
            )
        bx = 470 + ages.index(fit["log_age"]) * cw + cw / 2 if fit["log_age"] in ages else None
        by = 50 + (len(fehs) - 1 - fehs.index(fit["feh"])) * ch + ch / 2 if fit["feh"] in fehs else None
        if bx is not None and by is not None:
            out.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="5" fill="none" stroke="#c0392b" stroke-width="2"/>')
        for v in _ticks(min(ages), max(ages)):
            out.append(
                f'<text x="{470 + (v - min(ages)) / max(max(ages) - min(ages), 1e-9) * 330:.1f}" y="486" text-anchor="middle">{v:g}</text>'
            )
        for f in fehs:
            out.append(
                f'<text x="464" y="{50 + (len(fehs) - 1 - fehs.index(f)) * ch + ch / 2 + 4:.1f}" text-anchor="end">{f:g}</text>'
            )
        out.append('<text x="635" y="506" text-anchor="middle">log10 age / yr</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    span = hi - lo
    if span <= 0:
        return [round(lo, 2)]
    raw = span / n
    step = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * step >= raw:
            step *= m
            break
    first = math.ceil(lo / step) * step
    return [round(first + i * step, 6) for i in range(int((hi - first) / step) + 1)]
