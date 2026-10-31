"""``sdp einstein1921``: Einstein's 1921 globular-cluster calculation, rerun with Gaia (UC-03).

The protocol is docs/protocols/uc03-einstein-1921.md; the science is sdp_relativity.einstein1921.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from sdp.kernel.domain.errors import DomainError

einstein1921_app = typer.Typer(
    help="Einstein's 1921 globular-cluster calculation (M13), checked and rerun with Gaia (UC-03).",
    no_args_is_help=True,
)


def _say(text: str) -> None:
    typer.echo(text, err=True)


def _jsonable(value: Any) -> Any:
    import numpy as np

    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"{type(value).__name__} is not JSON")


@einstein1921_app.command("arithmetic")
def einstein1921_arithmetic() -> None:
    """Part A: Einstein's eq. (5) as printed, recomputed, and with A = 3 pi / 32. Needs no data."""
    from sdp_relativity.einstein1921 import arithmetic

    typer.echo(json.dumps(arithmetic(), indent=2, sort_keys=True))


def read_bulk_cone() -> dict[str, Any]:
    """The predeclared M13 cone from the local bulk Gaia copy (SDP_ARTIFACT_ROOT)."""
    import pyarrow as pa

    from sdp.adapters.archives.bulk import BulkCatalogueGateway
    from sdp.adapters.archives.tap import load_sources
    from sdp.adapters.bootstrap import artifact_store
    from sdp.cli.discover import _artifact_root
    from sdp.kernel.domain.archive import CatalogueQuery, Cone
    from sdp_relativity.einstein1921 import load_choices

    target = load_choices()["target"]
    gaia = load_sources()["gaia"].descriptor
    if target["source_release"] != gaia.release_label:
        raise DomainError(
            f"UC-03 is predeclared on {target['source_release']!r}; this SDP reads {gaia.release_label!r}"
        )
    bulk = BulkCatalogueGateway(artifact_store(_artifact_root()), gaia)
    if not bulk.complete():
        raise DomainError("the bulk Gaia copy is missing or incomplete; finish `sdp archive bulk`, or pass --parquet")
    cap = 2_000_000
    query = CatalogueQuery(
        "gaia",
        gaia.release_label,
        Cone(float(target["start_ra_deg"]), float(target["start_dec_deg"]), float(target["cone_radius_deg"])),
        gaia.column_names(),
        row_cap=cap,
        chunk_rows=200_000,
    )
    table = pa.Table.from_batches(list(bulk.fetch(bulk.query(query))))
    if table.num_rows >= cap:
        raise DomainError(f"the cone filled its {cap:,}-row cap, so it is incomplete")
    return {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}


def markdown(report: dict[str, Any]) -> str:
    """A short human summary of the JSON report."""
    a = report["arithmetic"]
    b = report["baseline"]
    p = b["physical"]
    band = b["bootstrap"]["percentiles"].get("mass_to_light", {})
    v = report["verdict"]
    lines = [
        "# UC-03: Einstein (1921) rerun with Gaia",
        "",
        f"Choices SHA-256 `{report['committed_choices_sha256']}`, match committed: {report['choices_match_committed']}.",
        "",
        "## Part A: the 1921 arithmetic",
        "",
        "| | coefficient in eq. (5) | 2a (N = 2000) | 2a (x100 stars) |",
        "|---|---|---|---|",
        f"| As printed | 1,2 | {a['printed']['two_a_cm']:.3g} cm = {a['printed']['two_a_ly']:g} ly"
        f" | {a['printed']['two_a_with_factor_ly']:g} ly |",
        f"| Recomputed, Einstein's A | {a['einstein_coefficient']['coefficient']:.3f}"
        f" | {a['einstein_coefficient']['two_a_ly']:.3f} ly | {a['einstein_coefficient']['two_a_with_factor_ly']:.1f} ly |",
        f"| A = 3 pi / 32 | {a['corrected_coefficient']['coefficient']:.3f}"
        f" | {a['corrected_coefficient']['two_a_ly']:.3f} ly | {a['corrected_coefficient']['two_a_with_factor_ly']:.1f} ly |",
        "",
        "## Baseline",
        "",
        f"- Distance {p['distance_pc']:,.0f} pc; Plummer a {p['a_pc']:.2f} pc (2a = {p['two_a_ly']:.1f} ly)",
        f"- Central dispersion {p['sigma0_kms']:.2f} km/s; Einstein's v (rms speed) {p['v_rms_kms']:.2f} km/s against his 26",
        f"- Dynamical mass {p['mass_solar']:,.0f} M_sun ({p['mass_solar_einstein_a']:,.0f} with Einstein's A)",
        f"- Light in G {p['light_g_solar']:,.0f} L_sun; mass-to-light {p['mass_to_light']:.2f}"
        + (f" (bootstrap 16-84%: {band['p16']:.2f} to {band['p84']:.2f})" if band else ""),
        "",
        "## Verdict",
        "",
        *(
            ["A quick look (fewer resamples or no scenarios): this is not the protocol's verdict.", ""]
            if report.get("quick_look")
            else []
        ),
        f"**{v['status']}** at threshold {v['threshold']}: {len(v['scenarios_below'])} scenarios below,"
        f" {len(v['scenarios_at_or_above'])} at or above, {len(v['scenarios_failed'])} failed.",
    ]
    return "\n".join(lines) + "\n"


@einstein1921_app.command("run")
def einstein1921_run(
    out: Annotated[Path, typer.Option(help="Folder for report.json and report.md")] = Path("reports/uc03"),
    parquet: Annotated[
        Path | None, typer.Option(help="A Parquet cone with native gaia_source columns instead of the bulk copy")
    ] = None,
    resamples: Annotated[
        int | None, typer.Option(min=0, help="Bootstrap resamples (predeclared 100); fewer for a quick look")
    ] = None,
    baseline_only: Annotated[bool, typer.Option("--baseline-only", help="Skip the 48 scenarios (quick look)")] = False,
) -> None:
    """Run UC-03 on the M13 cone: part A, the fit, the bootstrap, every scenario and the verdict.

    Reads the predeclared cone from your bulk Gaia copy, or from --parquet. Expect 5 to 15 minutes for
    the full run. A run with fewer resamples or without the scenarios is a quick look, and its
    verdict is not the protocol's.
    """
    from sdp_relativity.einstein1921 import run

    if parquet is not None:
        import pyarrow.parquet as pq

        table = pq.read_table(parquet)
        columns = {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}
        _say(f"Read {table.num_rows:,} stars from {parquet}.")
    else:
        _say("Reading the M13 cone from the bulk Gaia copy...")
        columns = read_bulk_cone()
        _say(f"Read {len(columns['ra']):,} stars.")
    _say("Fitting the baseline, the bootstrap and the scenarios...")
    report = run(columns, resamples=resamples, all_scenarios=not baseline_only)
    report["quick_look"] = baseline_only or (resamples is not None and resamples < 100)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=_jsonable), "utf-8")
    (out / "report.md").write_text(markdown(report), "utf-8")
    _say(f"Verdict {report['verdict']['status']}. Wrote {out / 'report.json'} and {out / 'report.md'}.")
