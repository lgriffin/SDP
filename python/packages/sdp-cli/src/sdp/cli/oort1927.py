"""``sdp oort1927``: Oort's 1927 detection of galactic rotation, rerun with Gaia (UC-04).

The protocol is docs/protocols/uc04-oort-1927.md; the science is sdp_stellar.oort1927.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from sdp.kernel.domain.errors import DomainError

if TYPE_CHECKING:
    import pyarrow as pa

oort1927_app = typer.Typer(
    help="Oort's 1927 detection of galactic rotation, checked and rerun with Gaia (UC-04).",
    no_args_is_help=True,
)

#: the native gaia_source columns the run reads
COLUMNS = (
    "source_id",
    "ra",
    "dec",
    "parallax",
    "parallax_error",
    "pmra",
    "pmdec",
    "phot_g_mean_mag",
    "bp_rp",
    "ruwe",
    "duplicated_source",
)


def _say(text: str) -> None:
    typer.echo(text, err=True)


@oort1927_app.command("arithmetic")
def oort1927_arithmetic() -> None:
    """Part A: Oort's mean A, r times A, the distance to the centre and the central mass, recomputed. No data."""
    from sdp_stellar.oort1927 import arithmetic

    typer.echo(json.dumps(arithmetic(), indent=2, sort_keys=True))


def _prefilters(choices: dict[str, Any]) -> list[tuple[str, str, Any]]:
    """The loosest of the predeclared cuts, pushed into the Parquet read so a whole-sky pass fits in memory."""
    s = choices["scenarios"]
    d_max = max(hi for _, hi in s["shell_kpc"])
    zp_min = min(s["parallax_zero_point_mas"])
    return [
        ("phot_g_mean_mag", "<", max(s["g_max"])),
        ("parallax", ">", 1.0 / d_max + zp_min - 1e-6),
        ("ruwe", "<", choices["selection"]["ruwe_max"]),
    ]


def scan_bulk(rv: bool) -> Iterator[pa.Table] | None:
    """Each file's stars that pass the shared cuts, one file at a time, from the full bulk copy or with
    ``rv`` from its rvs subset (None when that subset is not built yet)."""
    import pyarrow as pa

    from sdp.adapters.archives.bulk import BulkCatalogueGateway, load_subsets
    from sdp.adapters.archives.tap import load_sources
    from sdp.adapters.bootstrap import artifact_store
    from sdp.cli.discover import _artifact_root
    from sdp_stellar.oort1927 import load_choices, quality_mask

    choices = load_choices()
    gaia = load_sources()["gaia"].descriptor
    if choices["target"]["source_release"] != gaia.release_label:
        raise DomainError(
            f"UC-04 is predeclared on {choices['target']['source_release']!r}; SDP reads {gaia.release_label!r}"
        )
    bulk = BulkCatalogueGateway(artifact_store(_artifact_root()), gaia, subset=load_subsets()["rvs"] if rv else None)
    if not bulk.complete():
        if rv:
            return None
        raise DomainError("the bulk Gaia copy is missing or incomplete; finish `sdp archive bulk`, or pass --parquet")
    columns = (*COLUMNS, "radial_velocity") if rv else COLUMNS

    def report(done: int, total: int) -> None:
        if done % 100 == 0 or done == total:
            _say(f"{'rvs' if rv else 'gaia'} {done}/{total} files")

    def tables() -> Iterator[pa.Table]:
        for table in bulk.scan(columns, _prefilters(choices), progress=report):
            cols = {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}
            yield table.filter(pa.array(quality_mask(cols, choices)))

    return tables()


def read_bulk(rv: bool) -> dict[str, Any] | None:
    """The stars every scenario could use, as arrays; the columns are there even when no star is."""
    import numpy as np
    import pyarrow as pa

    tables = scan_bulk(rv)
    if tables is None:
        return None
    parts = list(tables)
    whole = pa.concat_tables(parts).combine_chunks() if parts else None
    del parts
    if whole is None:
        return {name: np.zeros(0) for name in ((*COLUMNS, "radial_velocity") if rv else COLUMNS)}
    return {name: np.asarray(whole.column(name).to_numpy(zero_copy_only=False)) for name in whole.column_names}


def read_parquet(path: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    return {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}


def markdown(report: dict[str, Any]) -> str:
    a = report["arithmetic"]
    pm = report["proper_motions"]
    rv = report["radial_velocities"]
    lines = [
        "# UC-04: Oort (1927) rerun with Gaia",
        "",
        f"Choices SHA-256 `{report['committed_choices_sha256']}`, match committed: {report['choices_match_committed']}.",
        "",
        "## Part A: the 1927 arithmetic",
        "",
        f"- Mean A over Table 2 (weights 1/error^2): {a['weighted_mean_A_km_s_pc']['computed']:.4f}"
        f" ± {a['weighted_mean_A_km_s_pc']['computed_error']:.4f} km/s/pc; printed"
        f" {a['weighted_mean_A_km_s_pc']['printed']:.4f} ± {a['weighted_mean_A_km_s_pc']['printed_error']:.4f}",
        *(
            f"- {r['group']}: r A x parallax = {r['computed_A']:.4f}, printed A {r['printed_A']:.3f}"
            for r in a["rA_times_parallax"]
        ),
        *(
            f"- R with K2/K1 = {v['k2_over_k1']:g}: {v['R_pc']:,.0f} pc, printed {v['R_pc_printed']:,.0f}"
            for v in a["radius_from_A"].values()
        ),
        f"- Central mass: {a['central_mass_solar']['computed']:.2e} M_sun; printed at least"
        f" {a['central_mass_solar']['printed_at_least']:.0e}",
        "",
    ]
    for title, m, params in (
        ("Proper motions (decides)", pm, "UVWABCK"),
        ("Radial velocities (Oort's method)", rv, "UVWACK"),
    ):
        lines += [f"## {title}", ""]
        if "not_run" in m:
            lines += [m["not_run"], ""]
            continue
        b = m["baseline"]
        if "failed" in b:
            lines += [f"The baseline failed: {b['failed']}", ""]
        else:
            lines += [
                f"Baseline `{m['baseline_label']}`: {b['n_stars']:,} stars, {b['n_clipped']:,} clipped.",
                "",
                "| | value | error |",
                "|---|---|---|",
                *(f"| {p} | {b[p]['value']:.2f} | {b[p]['error']:.2f} |" for p in params),
                "",
                f"Amplitude hypot(A, C) {b['derived']['amplitude']:.2f} ± {b['derived']['amplitude_error']:.2f} km/s/kpc,"
                + (
                    f" l0 {b['derived']['l0_deg']:.1f} ± {b['derived']['l0_error_deg']:.1f} deg (modern galactic longitude)."
                    if b["derived"]["l0_deg"] is not None
                    else " l0 undefined (no double wave)."
                ),
                "",
            ]
        r, c = m["reading"], m["comparison"]
        lines += [
            f"**{r['status']}**: {len(r['scenarios_detected'])} scenarios detect rotation, {len(r['scenarios_null'])}"
            f" are null, {len(r['scenarios_failed'])} failed.",
            "",
            f"Against Oort's A = {c['oort_A_km_s_kpc']:.1f} ± {c['oort_error']:.1f} km/s/kpc: **{c['status']}**"
            f" ({len(c['scenarios_within'])} of {len(c['z'])} scenarios within).",
            "",
        ]
    if report.get("quick_look"):
        lines += ["A quick look (baseline only): this is not the protocol's verdict.", ""]
    return "\n".join(lines)


@oort1927_app.command("run")
def oort1927_run(
    out: Annotated[Path, typer.Option(help="Folder for report.json and report.md")] = Path("reports/uc04"),
    parquet: Annotated[
        Path | None, typer.Option(help="A Parquet file of native gaia_source columns instead of the bulk copy")
    ] = None,
    rv_parquet: Annotated[
        Path | None, typer.Option(help="The same with radial_velocity, instead of the rvs bulk subset")
    ] = None,
    baseline_only: Annotated[bool, typer.Option("--baseline-only", help="Skip the 48 scenarios (quick look)")] = False,
) -> None:
    """Run UC-04: part A, then the rotation fit to proper motions and, where built, to radial velocities.

    Reads the whole sky from your bulk Gaia copy (one pass over every file), or from --parquet. The
    radial-velocity method reads the rvs subset (`sdp archive bulk --subset rvs`) and is skipped,
    with a note, until that subset is complete."""
    from sdp_stellar.oort1927 import RV_PARAMETERS, load_choices, method, run

    columns = read_parquet(parquet) if parquet is not None else read_bulk(rv=False)
    assert columns is not None
    _say(f"{len(columns['ra']):,} stars pass the shared cuts")
    report = run(columns, None, baseline_only=baseline_only)
    del columns  # one catalogue in memory at a time: the radial velocities are read after
    rv_columns = (
        read_parquet(rv_parquet) if rv_parquet is not None else (None if parquet is not None else read_bulk(rv=True))
    )
    if rv_columns is not None:
        _say(f"{len(rv_columns['ra']):,} stars with a radial velocity pass the shared cuts")
        report["radial_velocities"] = method(
            rv_columns, ("r",), RV_PARAMETERS, load_choices(), baseline_only=baseline_only
        )
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), "utf-8")
    (out / "report.md").write_text(markdown(report), "utf-8")
    v = report["verdict"]
    _say(f"verdict {v['status']}; Oort's A {report['proper_motions']['comparison']['status']}; wrote {out}")


@oort1927_app.command("export")
def oort1927_export(
    out: Annotated[Path, typer.Option(help="Parquet file to write")] = Path("reports/uc04/oort-sample.parquet"),
    every: Annotated[int, typer.Option(min=1, help="Keep stars whose source_id is a multiple of this")] = 100,
    rv: Annotated[bool, typer.Option("--rv", help="From the rvs subset, with radial_velocity")] = False,
) -> None:
    """Write a fixed sample of the stars the run reads (source_id divisible by --every) for a recorded test."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    tables = scan_bulk(rv=rv)
    if tables is None:
        raise DomainError("the rvs subset is not complete; finish `sdp archive bulk --subset rvs` first")
    out.parent.mkdir(parents=True, exist_ok=True)
    kept = seen = 0
    writer: pq.ParquetWriter | None = None
    try:
        for table in tables:  # one file at a time: the sample is written as it is drawn
            seen += table.num_rows
            ids = table.column("source_id").to_numpy(zero_copy_only=False)
            sample = table.filter(pa.array(ids % every == 0))
            if writer is None:
                writer = pq.ParquetWriter(out, sample.schema, compression="zstd")
            writer.write_table(sample)
            kept += sample.num_rows
    finally:
        if writer is not None:
            writer.close()
    _say(f"wrote {kept:,} of {seen:,} stars to {out}")
