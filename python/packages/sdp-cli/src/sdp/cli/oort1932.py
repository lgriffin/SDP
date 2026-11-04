"""``sdp oort1932``: Oort's 1932 force perpendicular to the galactic plane, rerun with Gaia (UC-05).

The protocol is docs/protocols/uc05-oort-1932.md; the science is sdp_stellar.oort1932.
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

oort1932_app = typer.Typer(
    help="Oort's 1932 vertical force and local density, checked and rerun with Gaia (UC-05).",
    no_args_is_help=True,
)

#: the native gaia_source columns the density reads; the velocities add the motions
DENSITY_COLUMNS = (
    "source_id",
    "ra",
    "dec",
    "parallax",
    "parallax_error",
    "phot_g_mean_mag",
    "bp_rp",
    "ruwe",
    "duplicated_source",
)
VELOCITY_COLUMNS = (*DENSITY_COLUMNS, "pmra", "pmdec", "radial_velocity", "radial_velocity_error")


def _say(text: str) -> None:
    typer.echo(text, err=True)


@oort1932_app.command("arithmetic")
def oort1932_arithmetic() -> None:
    """Part A: Oort's constants, shape factors, slope, plateau and units, recomputed. No data."""
    from sdp_stellar.oort1932 import arithmetic

    typer.echo(json.dumps(arithmetic(), indent=2, sort_keys=True))


def scan_bulk(velocities: bool) -> Iterator[pa.Table] | None:
    """Each file's stars that some scenario's column could hold and that pass the shared cuts, one file at a
    time, from the full bulk copy, or with ``velocities`` from its rvs subset (None when that is not built)."""
    import pyarrow as pa

    from sdp.adapters.archives.bulk import BulkCatalogueGateway, load_subsets
    from sdp.adapters.archives.tap import load_sources
    from sdp.adapters.bootstrap import artifact_store
    from sdp.cli.discover import _artifact_root
    from sdp_stellar.oort1932 import column_mask, load_choices, prefilters, quality_mask

    choices = load_choices()
    gaia = load_sources()["gaia"].descriptor
    if choices["target"]["source_release"] != gaia.release_label:
        raise DomainError(
            f"UC-05 is predeclared on {choices['target']['source_release']!r}; SDP reads {gaia.release_label!r}"
        )
    subset = load_subsets()["rvs"] if velocities else None
    bulk = BulkCatalogueGateway(artifact_store(_artifact_root()), gaia, subset=subset)
    if not bulk.complete():
        if velocities:
            return None
        raise DomainError("the bulk Gaia copy is missing or incomplete; finish `sdp archive bulk`, or pass --parquet")

    def report(done: int, total: int) -> None:
        if done % 100 == 0 or done == total:
            _say(f"{'rvs' if velocities else 'gaia'} {done}/{total} files")

    def tables() -> Iterator[pa.Table]:
        for table in bulk.scan(
            VELOCITY_COLUMNS if velocities else DENSITY_COLUMNS, prefilters(choices), progress=report
        ):
            cols = {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}
            keep = column_mask(cols, choices) & quality_mask(cols, choices, velocities=velocities)
            yield table.filter(pa.array(keep))

    return tables()


def read_bulk(velocities: bool) -> dict[str, Any] | None:
    import numpy as np
    import pyarrow as pa

    tables = scan_bulk(velocities)
    if tables is None:
        return None
    parts = list(tables)
    names = VELOCITY_COLUMNS if velocities else DENSITY_COLUMNS
    if not parts:
        return {name: np.zeros(0) for name in names}
    whole = pa.concat_tables(parts).combine_chunks()
    return {name: np.asarray(whole.column(name).to_numpy(zero_copy_only=False)) for name in whole.column_names}


def read_parquet(path: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    return {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}


def _value(doc: dict[str, Any], scale: float = 1.0, fmt: str = ".3g") -> str:
    return f"{doc['value'] * scale:{fmt}} ± {doc['error'] * scale:{fmt}}"


def markdown(report: dict[str, Any]) -> str:
    a = report["arithmetic"]
    lines = [
        "# UC-05: Oort (1932) rerun with Gaia",
        "",
        f"Choices SHA-256 `{report['committed_choices_sha256']}`, match committed: {report['choices_match_committed']}.",
        "",
        "## Part A: the 1932 arithmetic",
        "",
        f"- Formula (9) constant: {a['eq9_constant']['computed']:.4g}, printed {a['eq9_constant']['printed']:.4g}",
        f"- Z0 constant: {a['z0_constant']['computed']:.4g}, printed {a['z0_constant']['printed']:.4g};"
        f" Z0 to reach {a['z0_example']['z_pc']} pc: {a['z0_example']['computed_Z0_km_s']:.1f} km/s,"
        f" printed {a['z0_example']['printed_Z0_km_s']:g}",
        *(
            f"- Shape factor x at a/c = {r['a_over_c'] if r['a_over_c'] is not None else 'infinity'}:"
            f" {r['computed']:.4f}, printed {r['printed']:.3f}"
            for r in a["shape_factor_x"]
        ),
        *(
            f"- dK/dz through the origin over {k}: {v:.3g} s^-2, printed {a['dK_dz_s2']['printed']:.3g}"
            for k, v in a["dK_dz_s2"]["computed_through_origin"].items()
        ),
        f"- Density for a flat layer from his dK/dz: {a['density_flat_layer_msun_pc3']['computed']:.4f} Msun/pc^3;"
        f" his cases {a['density_flat_layer_msun_pc3']['printed_cases']}, adopted"
        f" {a['density_flat_layer_msun_pc3']['printed_adopted']}",
        f"- Plateau: {a['plateau_K_1e9']['computed']:.3f}e-9 cm/s^2, printed {a['plateau_K_1e9']['printed']}e-9",
        f"- 1 Msun/pc^3 = {a['unit_msun_pc3_in_g_cm3']['computed']:.4g} g/cm^3, printed"
        f" {a['unit_msun_pc3_in_g_cm3']['printed']:.4g}",
        "",
        "## Part B: the force from Gaia",
        "",
    ]
    b = report["baseline"]
    if "failed" in b:
        lines += [f"The baseline `{report['baseline_label']}` failed: {b['failed']}", ""]
    else:
        side = report["beside_oort"]
        lines += [
            f"Baseline `{report['baseline_label']}`: {b['n_density']:,} density stars, {b['n_velocity']:,} with"
            f" velocities; the Sun {b['z_sun_pc']:.1f} pc above the plane (Oort's tentative"
            f" {side['z_sun_pc']['oort_tentative']:g}).",
            "",
            f"- Slope near the plane k1 = {_value(b['k1_s2'])} s^-2 ({side['k1_over_oort']:.2f} of Oort's 5.62e-30)",
            f"- Flattening over 200-500 pc: {_value(b['flattening'], fmt='.2f')} (Oort's force: about 0)",
            f"- Plateau |K| = {_value(b['plateau_cm_s2'], 1e9, '.2f')} e-9 cm/s^2 (Oort: 3.8)",
            f"- Local density rho0 = {_value(b['rho0_msun_pc3'], fmt='.4f')} Msun/pc^3 (Oort: 0.092)",
            f"- rho0 less Oort's visible stars (0.038): {side['rho0_less_oorts_visible_stars']['value']:.4f}",
            "",
            "| z (pc) | Gaia K (1e-9 cm/s^2) | Oort mean corrected | Oort preliminary |",
            "|---|---|---|---|",
            *(
                f"| {r['z_pc']} | "
                + (f"{r['gaia_K_1e9']:.2f} ± {r['gaia_error_1e9']:.2f}" if r["gaia_K_1e9"] is not None else "-")
                + f" | {r['oort_mean_corrected_1e9']} | {r['oort_preliminary_1e9']} |"
                for r in side["table14"]
            ),
            "",
        ]
    v, c = report["verdict"], report["comparison"]
    lines += [
        f"**{v['status']}**: {len(v['scenarios_flatten'])} scenarios flatten, {len(v['scenarios_rising'])} do not,"
        f" {len(v['scenarios_failed'])} failed.",
        "",
        f"Against Oort's density {c['oort_rho0_msun_pc3']} ± {c['oort_sigma']:.3f} Msun/pc^3: **{c['status']}**"
        f" ({len(c['scenarios_within'])} of {len(c['z'])} scenarios within).",
        "",
    ]
    if report.get("quick_look"):
        lines += ["A quick look (baseline only): this is not the protocol's verdict.", ""]
    return "\n".join(lines)


@oort1932_app.command("run")
def oort1932_run(
    out: Annotated[Path, typer.Option(help="Folder for report.json and report.md")] = Path("reports/uc05"),
    parquet: Annotated[
        Path | None, typer.Option(help="A Parquet file of native gaia_source columns instead of the bulk copy")
    ] = None,
    rv_parquet: Annotated[
        Path | None, typer.Option(help="The same with motions and radial_velocity, instead of the rvs subset")
    ] = None,
    baseline_only: Annotated[bool, typer.Option("--baseline-only", help="Skip the 80 scenarios (quick look)")] = False,
) -> None:
    """Run UC-05: part A, then Oort's formula (7) in every scenario.

    The density comes from your bulk Gaia copy (one pass over every file) or --parquet; the vertical
    velocities from the rvs subset (`sdp archive bulk --subset rvs`) or --rv-parquet. Both are needed."""
    from sdp_stellar.oort1932 import run

    velocities = read_parquet(rv_parquet) if rv_parquet is not None else read_bulk(velocities=True)
    if velocities is None:
        raise DomainError(
            "UC-05 needs vertical velocities: finish `sdp archive bulk --subset rvs`, or pass --rv-parquet"
        )
    _say(f"{len(velocities['ra']):,} stars with velocities")
    density = read_parquet(parquet) if parquet is not None else read_bulk(velocities=False)
    assert density is not None
    _say(f"{len(density['ra']):,} stars for the density")
    report = run(density, velocities, baseline_only=baseline_only)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), "utf-8")
    (out / "report.md").write_text(markdown(report), "utf-8")
    _say(f"verdict {report['verdict']['status']}; Oort's density {report['comparison']['status']}; wrote {out}")


@oort1932_app.command("export")
def oort1932_export(
    out: Annotated[Path, typer.Option(help="Folder for density.parquet and velocities.parquet")] = Path("reports/uc05"),
    every: Annotated[int, typer.Option(min=1, help="Keep stars whose source_id is a multiple of this")] = 1,
) -> None:
    """Write the column stars the run reads (source_id divisible by --every) for a recorded test."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    out.mkdir(parents=True, exist_ok=True)
    for name, velocities in (("velocities", True), ("density", False)):
        tables = scan_bulk(velocities)
        if tables is None:
            raise DomainError("the rvs subset is not complete; finish `sdp archive bulk --subset rvs` first")
        kept = 0
        writer: pq.ParquetWriter | None = None
        try:
            for table in tables:
                ids = table.column("source_id").to_numpy(zero_copy_only=False)
                sample = table.filter(pa.array(ids % every == 0))
                if writer is None:
                    writer = pq.ParquetWriter(out / f"{name}.parquet", sample.schema, compression="zstd")
                writer.write_table(sample)
                kept += sample.num_rows
        finally:
            if writer is not None:
                writer.close()
        _say(f"wrote {kept:,} stars to {out / f'{name}.parquet'}")
