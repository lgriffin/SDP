"""``sdp discover``: the discovery use case (UC-01) on real Gaia data, from start to finish.

Two ways in, one pipeline:

- ``run`` is the predeclared experiment: Gaia cones for the four predeclared regions, both benchmark
  catalogues, the full grid with null fields and injections, and a verdict on the gates.
- ``prepare`` / ``sweep`` / ``report`` search the whole sky from the complete bulk Gaia copy (STL-009):
  ``prepare`` reads every bulk file once into a cut catalogue split by declination band, ``sweep``
  clusters it tile by tile (resumable, several tiles at once), ``report`` lists the candidates.
  Sweep results are EXPLORATORY: a promising candidate is a reason for a new predeclared run, not a claim.
"""

from __future__ import annotations

import csv
import json
import os
import time
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Annotated, Any

import typer

from sdp.kernel.domain.errors import DomainError

discover_app = typer.Typer(
    help="Look for star clusters nobody has catalogued (UC-01): the predeclared run, or a whole-sky sweep "
    "of your local Gaia copy. Every sweep result is EXPLORATORY.",
    no_args_is_help=True,
)

PROTOCOL = "uc01-stellar-clusters"
#: The canonical columns a sweep reads; the rest of the curated schema is left behind to save space.
CATALOGUE_COLUMNS = (
    "source_key",
    "ra_deg",
    "dec_deg",
    "parallax_mas",
    "parallax_mas_err",
    "pm_ra_cosdec_masyr",
    "pm_ra_cosdec_masyr_err",
    "pm_dec_masyr",
    "pm_dec_masyr_err",
    "g_mag",
    "bp_rp",
    "ruwe",
    "quality_flags",
)


def _say(text: str) -> None:
    typer.echo(text, err=True)


def _emit(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True, default=_jsonable))


def _jsonable(value: Any) -> Any:
    import numpy as np

    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"{type(value).__name__} is not JSON")


def _artifact_root() -> Path:
    root = os.environ.get("SDP_ARTIFACT_ROOT")
    if not root:
        raise DomainError("SDP_ARTIFACT_ROOT is not set: it names the artifact store holding the Gaia copy")
    return Path(root)


def sweep_dir(name: str) -> Path:
    from sdp.adapters.local.sources import data_dir

    return data_dir() / "sweep" / name


def _benchmark_path(benchmark_id: str, release: str) -> str:
    return f"benchmarks/{benchmark_id}/{release.replace(':', '_')}.parquet"


def _load_benchmarks(store: Any, labels: list[str]) -> tuple[list[Any], list[str]]:
    """The stored benchmark catalogues the settings name, and the ones missing."""
    import io

    import pyarrow.parquet as pq

    from sdp_stellar.benchmark import benchmark_from

    found, missing = [], []
    for label in labels:
        benchmark_id = label.split(":", 1)[0]
        artifact = store.resolve(_benchmark_path(benchmark_id, label))
        if artifact is None:
            missing.append(benchmark_id)
            continue
        found.append(benchmark_from(benchmark_id, pq.read_table(io.BytesIO(store.get(artifact)))))
    return found, missing


# --- prepare: the cut catalogue ------------------------------------------------------------------


def precut_mask(native: Any, cuts: dict[str, Any]) -> Any:
    """Native Gaia rows that can pass the quality cuts: the same thresholds as ``QualityCuts``, tested on
    native columns before the (slower) curation, so the cut catalogue loses no star that passes."""
    import numpy as np
    import pyarrow.compute as pc

    def floats(name: str) -> Any:
        col = pc.fill_null(pc.cast(native.column(name), "float64"), np.nan)
        return np.asarray(col.to_numpy(zero_copy_only=False), dtype=np.float64)

    plx, err = floats("parallax"), floats("parallax_error")
    with np.errstate(divide="ignore", invalid="ignore"):
        poe = np.where(np.isfinite(err) & (err > 0), plx / err, np.nan)
    keep = np.isfinite(floats("pmra")) & np.isfinite(floats("pmdec")) & np.isfinite(plx)
    keep &= np.nan_to_num(floats("ruwe"), nan=np.inf) <= float(cuts["ruwe_max"])
    keep &= np.nan_to_num(poe, nan=-np.inf) >= float(cuts["parallax_over_error_min"])
    keep &= np.nan_to_num(floats("phot_g_mean_mag"), nan=np.inf) <= float(cuts["g_mag_max"])
    keep &= np.isin(floats("astrometric_params_solved"), [float(v) for v in cuts["astrometric_params_solved"]])
    return keep


def prepare_file(
    root: str, logical_path: str, stem: str, out: str, cuts: dict[str, Any], tile_deg: float
) -> dict[str, Any]:
    """One bulk file: pre-cut, curate column by column, apply the exact cuts, split by declination band.
    Writes ``band=NNN/<stem>.parquet`` files, then the ``_done/<stem>.json`` marker last."""
    import io

    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    from sdp.adapters.archives.dialect import curate_columnar, load_dialects
    from sdp.adapters.bootstrap import artifact_store
    from sdp_stellar.quality import QualityCuts
    from sdp_stellar.sweep import band_of

    started = time.monotonic()
    store = artifact_store(Path(root))
    artifact = store.resolve(logical_path)
    if artifact is None:
        raise DomainError(f"{logical_path} is not in the store")
    native = pq.read_table(io.BytesIO(store.get(artifact)))
    survivors = native.filter(pa.array(precut_mask(native, cuts)))
    curated = curate_columnar(load_dialects()["gaia-dr3"], survivors)
    kept, _ = QualityCuts.from_parameters(cuts).apply(curated)
    kept = kept.select(list(CATALOGUE_COLUMNS))
    dec = np.asarray(kept.column("dec_deg").to_numpy(zero_copy_only=False), dtype=float)
    bands = band_of(dec, tile_deg)
    base = Path(out)
    for band in np.unique(bands).tolist():
        folder = base / f"band={band:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        part = kept.filter(pa.array(bands == band))
        tmp = folder / f".{stem}.parquet.tmp"
        pq.write_table(part, tmp, compression="zstd")
        tmp.replace(folder / f"{stem}.parquet")
    record = {
        "file": stem,
        "rows": native.num_rows,
        "kept": kept.num_rows,
        "bands": sorted({int(b) for b in bands.tolist()}),
        "seconds": round(time.monotonic() - started, 2),
    }
    done = base / "_done"
    done.mkdir(parents=True, exist_ok=True)
    (done / f"{stem}.json").write_text(json.dumps(record, sort_keys=True), "utf-8")
    return record


def _settings_guard(path: Path, settings: dict[str, Any], what: str) -> None:
    """A folder holds results of one set of settings; mixing two would make the counts meaningless."""
    if path.exists():
        before = json.loads(path.read_text("utf-8"))
        if before != settings:
            raise DomainError(
                f"{path.parent} was made with other {what} settings; pass a new --name, or delete that folder to start again"
            )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2, sort_keys=True), "utf-8")


@discover_app.command("prepare")
def discover_prepare(
    name: Annotated[str, typer.Option(help="The sweep's folder under <SDP data>/sweep")] = "gaia-dr3",
    workers: Annotated[int, typer.Option(min=1, max=32, help="Files read at once")] = 4,
    count: Annotated[int | None, typer.Option(help="Do at most this many files (try 2 first)")] = None,
) -> None:
    """Read every stored bulk Gaia file once into a cut catalogue for the sweep (STL-009).

    Keeps only stars passing the UC-01 quality cuts (about one in twenty), in canonical units, split
    into 2-degree declination bands. Files already done are skipped, so stop it any time and run it
    again to carry on. Expect several hours for all ~3,400 files at --workers 4, and ~10-20 GB of output.
    """
    from sdp.adapters.archives.bulk import BulkCatalogueGateway
    from sdp.adapters.archives.tap import load_sources
    from sdp.adapters.bootstrap import artifact_store
    from sdp_stellar.sweep import load_settings

    settings = load_settings()
    cuts, tile_deg = dict(settings["quality_cuts"]), float(settings["tile_deg"])
    out = sweep_dir(name) / "catalogue"
    _settings_guard(out / "_settings.json", {"quality_cuts": cuts, "tile_deg": tile_deg}, "catalogue")
    root = _artifact_root()
    gaia = load_sources()["gaia"]
    bulk = BulkCatalogueGateway(artifact_store(root), gaia.descriptor)
    files = bulk.files()
    if not files:
        raise DomainError("no bulk Gaia copy in the store; run `sdp archive bulk` first")
    stored = {f.stem for f in files} - {f.stem for f in bulk.missing()}
    todo = [f for f in files if f.stem in stored and not (out / "_done" / f"{f.stem}.json").exists()]
    if count is not None:
        todo = todo[:count]
    already = sum(1 for stem in stored if (out / "_done" / f"{stem}.json").exists())
    _say(f"{len(files)} files in the release, {len(stored)} stored, {already} already prepared, {len(todo)} to do now.")
    results, failed = [], []
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=workers, mp_context=_spawn()) as pool:
        futures = {
            pool.submit(
                prepare_file, str(root), f"{bulk.prefix}/{f.stem}.parquet", f.stem, str(out), cuts, tile_deg
            ): f.stem
            for f in todo
        }
        for i, future in enumerate(_as_done(futures), start=1):
            stem = futures[future]
            try:
                r = future.result()
            except Exception as exc:  # one bad file never stops the run; it is retried next time
                failed.append(stem)
                _say(f"{i}/{len(todo)} {stem} failed: {exc}")
                continue
            results.append(r)
            _say(f"{i}/{len(todo)} {stem} {r['rows']:,} rows -> {r['kept']:,} kept, {r['seconds']:.1f} s")
    done = len(list((out / "_done").glob("*.json"))) if (out / "_done").exists() else 0
    _emit(
        {
            "catalogue": str(out),
            "files_in_release": len(files),
            "files_stored": len(stored),
            "files_prepared": done,
            "complete": done == len(files),
            "this_run": {
                "files": len(results),
                "failed": failed,
                "stars_kept": sum(r["kept"] for r in results),
                "seconds": round(time.monotonic() - started, 1),
            },
        }
    )
    if failed:
        raise typer.Exit(1)


def _spawn() -> Any:
    """Fresh worker processes on every platform, as Windows always has: no forked copy of a threaded parent."""
    import multiprocessing

    return multiprocessing.get_context("spawn")


def _as_done(futures: dict[Future[Any], str]) -> Iterator[Future[Any]]:
    pending = set(futures)
    while pending:
        finished, pending = wait(pending, return_when=FIRST_COMPLETED)
        yield from sorted(finished, key=lambda f: futures[f])


# --- sweep: tile by tile -------------------------------------------------------------------------


def _band_table(catalogue: Path, bands: list[int]) -> Any:
    import pyarrow as pa
    import pyarrow.parquet as pq

    parts = []
    for band in bands:
        folder = catalogue / f"band={band:03d}"
        for path in sorted(folder.glob("*.parquet")) if folder.is_dir() else []:
            parts.append(pq.read_table(path))
    if not parts:
        return None
    return pa.concat_tables(parts)


def run_tile(
    table: Any, band: int, index: int, settings: dict[str, Any], benchmarks: list[Any], out: str
) -> dict[str, Any]:
    """One tile in a worker process; writes ``tiles/<tile_id>.json`` and returns a short summary."""
    from sdp.adapters.bootstrap import clusterers
    from sdp_stellar.sweep import sweep_tile, tile

    started = time.monotonic()
    t = tile(band, index, float(settings["tile_deg"]))
    result = sweep_tile(table, t, settings, clusterers(), benchmarks, int(settings["seed"]))
    result["seconds"] = round(time.monotonic() - started, 2)
    path = Path(out) / f"{t.tile_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, sort_keys=True, default=_jsonable), "utf-8")
    tmp.replace(path)
    statuses: dict[str, int] = {}
    for c in result["candidates"]:
        statuses[c["status"]] = statuses.get(c["status"], 0) + 1
    return {
        "tile_id": t.tile_id,
        "stars": result["cuts"]["kept_rows"],
        "statuses": statuses,
        "skipped": result["skipped"],
        "seconds": result["seconds"],
    }


def _bands(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(total))
    lo, _, hi = spec.partition(":")
    first, last = int(lo), int(hi) if hi else int(lo)
    if not 0 <= first <= last < total:
        raise typer.BadParameter(f"bands run 0..{total - 1}; got {spec}", param_hint="--bands")
    return list(range(first, last + 1))


@discover_app.command("sweep")
def discover_sweep(
    name: Annotated[str, typer.Option(help="The sweep's folder under <SDP data>/sweep")] = "gaia-dr3",
    workers: Annotated[int, typer.Option(min=1, max=64, help="Tiles searched at once")] = 4,
    bands: Annotated[
        str | None, typer.Option(help="Declination bands FIRST:LAST (0 = south pole, 89 = north); all when omitted")
    ] = None,
    without_benchmarks: Annotated[
        bool, typer.Option("--without-benchmarks", help="Run with no catalogue of known clusters (nothing can be new)")
    ] = False,
) -> None:
    """Search the prepared catalogue for clusters, tile by tile (STL-009). EXPLORATORY.

    Each 2x2-degree tile is clustered with a half-degree rim of its neighbours, checked against the
    pass marks and both known-cluster catalogues, and run once more on shuffled motions (a null field)
    to see how often the pipeline finds something that is not there. Tiles already done are skipped.
    Try one band first: `sdp discover sweep --bands 45`.
    """
    from sdp.adapters.bootstrap import artifact_store
    from sdp_stellar.sweep import bands as band_count
    from sdp_stellar.sweep import load_settings, neighbourhood, ra_dec, tile, tiles_in_band

    settings = load_settings()
    folder = sweep_dir(name)
    catalogue = folder / "catalogue"
    if not (catalogue / "_settings.json").exists():
        raise DomainError(f"no prepared catalogue in {catalogue}; run `sdp discover prepare` first")
    prepared = json.loads((catalogue / "_settings.json").read_text("utf-8"))
    if prepared != {"quality_cuts": settings["quality_cuts"], "tile_deg": float(settings["tile_deg"])}:
        raise DomainError("the prepared catalogue used other cuts or tiles than this SDP version; prepare it again")
    benchmarks, missing = _load_benchmarks(artifact_store(_artifact_root()), list(settings["benchmarks"]))
    if missing and not without_benchmarks:
        raise DomainError(
            f"no stored {missing}; run `sdp benchmark fetch` first (or pass --without-benchmarks: then nothing is called potentially new)"
        )
    _settings_guard(folder / "tiles" / "_settings.json", settings, "sweep")
    out = folder / "tiles"
    tile_deg, margin = float(settings["tile_deg"]), float(settings["margin_deg"])
    total = band_count(tile_deg)
    chosen = _bands(bands, total)
    summaries: list[dict[str, Any]] = []
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=workers, mp_context=_spawn()) as pool:
        for band in chosen:
            todo = [
                i
                for i in range(tiles_in_band(band, tile_deg))
                if not (out / f"{tile(band, i, tile_deg).tile_id}.json").exists()
            ]
            if not todo:
                continue
            table = _band_table(catalogue, [b for b in (band - 1, band, band + 1) if 0 <= b < total])
            if table is None:
                _say(f"band {band:03d}: no prepared stars")
                continue
            ra, dec = ra_dec(table)
            futures: dict[Future[Any], str] = {}
            for i in todo:
                t = tile(band, i, tile_deg)
                rows = table.filter(neighbourhood(t, margin, ra, dec))
                futures[pool.submit(run_tile, rows, band, i, settings, benchmarks, str(out))] = t.tile_id
            for future in _as_done(futures):
                try:
                    s = future.result()
                except Exception as exc:  # one bad tile never stops the sweep; it is retried next run
                    _say(f"{futures[future]} failed: {exc}")
                    continue
                summaries.append(s)
                found = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(s["statuses"].items())) or "none"
                note = f" (skipped: {s['skipped']})" if s["skipped"] else ""
                _say(f"{s['tile_id']} {s['stars']:,} stars, candidates: {found}, {s['seconds']:.1f} s{note}")
    new = sum(s["statuses"].get("potentially_new", 0) for s in summaries)
    _emit(
        {
            "label": "EXPLORATORY",
            "folder": str(folder),
            "bands": [chosen[0], chosen[-1]],
            "tiles_this_run": len(summaries),
            "potentially_new_this_run": new,
            "seconds": round(time.monotonic() - started, 1),
            "next": "sdp discover report",
        }
    )


@discover_app.command("report")
def discover_report(
    name: Annotated[str, typer.Option(help="The sweep's folder under <SDP data>/sweep")] = "gaia-dr3",
) -> None:
    """Gather every tile done so far into candidates.csv (potentially new first) and a summary. EXPLORATORY."""
    from sdp_stellar.sweep import candidate_rows, summarise

    folder = sweep_dir(name)
    tiles = sorted((folder / "tiles").glob("b*-t*.json")) if (folder / "tiles").is_dir() else []
    if not tiles:
        raise DomainError(f"no swept tiles in {folder / 'tiles'}; run `sdp discover sweep` first")
    results = [json.loads(p.read_text("utf-8")) for p in tiles]
    rows = candidate_rows(results)
    path = folder / "candidates.csv"
    fields = list(rows[0]) if rows else ["candidate_id", "status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    summary = summarise(results)
    rate = summary["null_false_positive_rate"]
    _say(
        f"{summary['tiles']} tiles searched ({summary['stars_searched']:,} stars). Candidates: "
        + (", ".join(f"{n} {k.replace('_', ' ')}" for k, n in summary["candidates"].items()) or "none")
        + (f". Null fields with a false candidate: {rate:.1%}." if rate is not None else ".")
    )
    _say(f"Every row is a candidate, not a confirmed discovery. Open {path} (potentially new first).")
    _emit({**summary, "csv": str(path), "top": rows[:10]})


# --- run: the predeclared experiment -------------------------------------------------------------


@discover_app.command("run")
def discover_run(
    signer: Annotated[str, typer.Option(envvar="SDP_SIGNER", help="Principal in allowed_signers")],
    null_fields: Annotated[
        int | None, typer.Option(help="Null fields per region (predeclared 20); fewer for a quick look")
    ] = None,
    bootstrap_resamples: Annotated[
        int | None, typer.Option(help="Resamples for the recovery intervals (predeclared 1000)")
    ] = None,
    row_cap: Annotated[int, typer.Option(help="Most Gaia stars read per region")] = 1_000_000,
    seed: Annotated[int, typer.Option()] = 0,
) -> None:
    """The predeclared UC-01 experiment on real Gaia data, end to end, with a verdict.

    Freezes the predeclaration (once), reads the four predeclared regions from Gaia (your bulk copy
    when it is complete, else the archive), adds both known-cluster catalogues, runs every search,
    null field and injection, and checks the gates. Expect 10 minutes to an hour.
    """
    from sdp.adapters.archives.tap import load_sources
    from sdp.cli.app import state
    from sdp.kernel.domain.archive import CatalogueQuery, Cone
    from sdp.kernel.ports.driving import AcquireDatasetCommand, DefineExperimentCommand, FreezePredeclarationCommand

    runtime = state.runtime()
    kernel = runtime.kernel
    module = kernel.ports.protocols.get(PROTOCOL, None)
    _say("1/5 Freezing the predeclaration (regions, cuts, pass marks) in the ledger...")
    frozen, _ = kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(module.predeclare(), signer))
    labels = list(module.predeclare().parameters["benchmarks"])
    benchmarks, missing = _benchmark_artifacts(runtime.store, labels)
    if missing:
        raise DomainError(f"no stored {missing}; run `sdp benchmark fetch` first")
    gaia = load_sources()["gaia"].descriptor
    inputs = dict(benchmarks)
    targets = module.predeclare().targets
    for i, target in enumerate(targets, start=1):
        _say(f"2/5 Reading Gaia for {target.target_id} ({i}/{len(targets)})...")
        ra, dec, radius = (float(target.spec[k]) for k in ("ra_deg", "dec_deg", "radius_deg"))
        result = kernel.acquire_dataset.execute(
            AcquireDatasetCommand(
                CatalogueQuery(
                    "gaia",
                    gaia.release_label,
                    Cone(ra, dec, radius),
                    gaia.column_names(),
                    row_cap=row_cap,
                    chunk_rows=min(row_cap, 200_000),
                ),
                curate=True,
            )
        )
        if result.curated is None:
            raise DomainError(f"Gaia for {target.target_id} was not curated")
        if result.rows >= row_cap:
            _say(f"    warning: {target.target_id} filled its {row_cap:,}-row cap; raise --row-cap")
        inputs[f"field:{target.target_id}"] = result.curated.artifact_id
    params: dict[str, Any] = {}
    if null_fields is not None:
        params["null_fields_per_region"] = null_fields
    if bootstrap_resamples is not None:
        params["bootstrap_resamples"] = bootstrap_resamples
    _say("3/5 Defining the experiment...")
    experiment = kernel.define_experiment.execute(
        DefineExperimentCommand(PROTOCOL, frozen.hash, inputs, params, seed=seed)
    )
    _say("4/5 Running: quality cuts, clustering grid, null fields, injections, cross-match...")
    experiment = kernel.run_experiment.execute(experiment.experiment_id)
    _say("5/5 Checking the gates...")
    report = kernel.validate_experiment.execute(experiment.experiment_id)
    narrative = dict(experiment.findings.narrative) if experiment.findings is not None else {}
    dossiers = narrative.get("dossiers", [])
    statuses: dict[str, int] = {}
    for d in dossiers:
        statuses[d["status"]] = statuses.get(d["status"], 0) + 1
    _say(
        f"Verdict {report.verdict.status.value}. Candidates: "
        + (", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(statuses.items())) or "none")
        + f". See `sdp experiment show {experiment.experiment_id}`."
    )
    _emit(
        {
            "experiment_id": experiment.experiment_id,
            "verdict": report.verdict.status.value,
            "checks": {c.gate_id: c.outcome.value for c in report.checks},
            "candidates": statuses,
            "potentially_new": [
                {"candidate_id": d["candidate_id"], "statement": d["statement"], "reasons": d["reasons"]}
                for d in dossiers
                if d["status"] == "potentially_new"
            ],
        }
    )


def _benchmark_artifacts(store: Any, labels: list[str]) -> tuple[dict[str, Any], list[str]]:
    found, missing = {}, []
    for label in labels:
        benchmark_id = label.split(":", 1)[0]
        artifact = store.resolve(_benchmark_path(benchmark_id, label))
        if artifact is None:
            missing.append(benchmark_id)
        else:
            found[f"benchmark:{benchmark_id}"] = artifact
    return found, missing
