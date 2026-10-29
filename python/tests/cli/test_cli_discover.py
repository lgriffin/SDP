"""T4: `sdp discover` end to end on synthetic Gaia: the predeclared run with a verdict, and the whole-sky
sweep from a bulk copy (prepare, sweep, report). The fields are synthetic with planted clusters (SYN-A
known to both benchmarks, SYN-B to neither); they are not Gaia data."""

from __future__ import annotations

import csv
import dataclasses
import json
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from sdp.adapters.archives.dialect import YamlDialectCurator
from sdp.adapters.archives.tap import load_sources
from sdp.adapters.bootstrap import artifact_store, clusterers
from sdp.cli import app as cli
from sdp.cli import discover
from sdp.kernel.application.kernel import Kernel
from sdp.kernel.domain.errors import DomainError
from sdp.testing.fakes.archives import FakeArchive
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel
from sdp_stellar.protocol import StellarClusterProtocol, load_predeclaration
from sdp_stellar.synthetic import make_benchmark, make_field, parquet

pytestmark = pytest.mark.tier("T4")
runner = CliRunner()
GAIA = load_sources()["gaia"].descriptor
PREFIX = "bulk/gaia/gaia_DR3"
PARQUET = "application/vnd.apache.parquet"


def targets() -> list[dict[str, Any]]:
    return list(load_predeclaration()["targets"])


def native(table: pa.Table, first_id: int) -> pa.Table:
    """Synthetic canonical rows written back as native gaia_source columns."""
    rows = []
    for i, r in enumerate(table.to_pylist()):
        flags = json.loads(r["quality_flags"])
        rows.append(
            {
                "source_id": first_id + i,
                "ref_epoch": 2016.0,
                "ra": r["ra_deg"],
                "ra_error": 0.05,
                "dec": r["dec_deg"],
                "dec_error": 0.05,
                "parallax": r["parallax_mas"],
                "parallax_error": r["parallax_mas_err"],
                "pmra": r["pm_ra_cosdec_masyr"],
                "pmra_error": r["pm_ra_cosdec_masyr_err"],
                "pmdec": r["pm_dec_masyr"],
                "pmdec_error": r["pm_dec_masyr_err"],
                **dict.fromkeys(
                    (
                        "ra_dec_corr",
                        "ra_parallax_corr",
                        "ra_pmra_corr",
                        "ra_pmdec_corr",
                        "dec_parallax_corr",
                        "dec_pmra_corr",
                        "dec_pmdec_corr",
                        "parallax_pmra_corr",
                        "parallax_pmdec_corr",
                        "pmra_pmdec_corr",
                    )
                ),
                "phot_g_mean_mag": r["g_mag"],
                "bp_rp": r["bp_rp"],
                "ruwe": r["ruwe"],
                "astrometric_params_solved": flags["astrometric_params_solved"],
                "duplicated_source": False,
            }
        )
    types = {
        "long": pa.int64(),
        "double": pa.float64(),
        "float": pa.float32(),
        "short": pa.int16(),
        "boolean": pa.bool_(),
    }
    schema = pa.schema([(c.name, types[c.datatype]) for c in GAIA.columns])
    return pa.Table.from_pylist(rows, schema=schema)


def field(target_id: str) -> pa.Table:
    return make_field(next(t for t in targets() if t["target_id"] == target_id), 7)


def benchmark_tables() -> dict[str, pa.Table]:
    return {b: make_benchmark(b, targets(), label) for b, label in (("hr23", "hr23:v1.0"), ("cga20", "cga20:v1"))}


def store_benchmarks(store: Any) -> None:
    for b, table in benchmark_tables().items():
        label = "hr23:v1.0" if b == "hr23" else "cga20:v1"
        store.link(discover._benchmark_path(b, label), store.put(parquet(table), PARQUET))


def invoke(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


# --- the sweep from a bulk copy ------------------------------------------------------------------


@pytest.fixture
def bulk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "artifacts"
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(root))
    monkeypatch.setenv("SDP_DATA", str(tmp_path / "data"))
    store = artifact_store(root)
    names = ["GaiaSource_000000-000001.csv.gz", "GaiaSource_000002-000003.csv.gz", "GaiaSource_000004-000005.csv.gz"]
    listing = "".join(f"{'0' * 32}  {n}\n" for n in names)
    store.put(listing.encode(), "text/plain", logical_path=f"{PREFIX}/_MD5SUM.txt")
    # two files stored, the third still downloading
    for name, (target_id, first) in zip(names, (("praesepe", 1_000_000), ("pleiades", 2_000_000)), strict=False):
        stem = name.removesuffix(".csv.gz")
        store.link(f"{PREFIX}/{stem}.parquet", store.put(parquet(native(field(target_id), first)), PARQUET))
    store_benchmarks(store)
    return tmp_path


@pytest.mark.req("STL-009")
def test_prepare_sweep_report_finds_the_planted_cluster_in_no_benchmark(bulk: Path) -> None:
    prepared = invoke("discover", "prepare", "--workers", "1")
    assert (prepared["files_in_release"], prepared["files_stored"], prepared["files_prepared"]) == (3, 2, 2)
    assert prepared["complete"] is False and prepared["this_run"]["failed"] == []
    catalogue = bulk / "data" / "sweep" / "gaia-dr3" / "catalogue"
    assert prepared["catalogue"] == str(catalogue)
    band = 54  # Dec 18..20, where SYN-B sits
    parts = sorted(p.name for p in (catalogue / f"band={band:03d}").glob("*.parquet"))
    assert parts == ["GaiaSource_000000-000001.parquet"]
    part = pq.read_table(catalogue / f"band={band:03d}" / parts[0])
    assert part.column_names == list(discover.CATALOGUE_COLUMNS)
    assert part.column("source_key")[0].as_py().startswith("gaia:DR3:")
    assert prepared["this_run"]["stars_kept"] == sum(
        pq.read_table(p).num_rows for p in catalogue.glob("band=*/*.parquet")
    )
    assert invoke("discover", "prepare", "--workers", "1")["this_run"]["files"] == 0  # resumes, nothing to redo

    swept = invoke("discover", "sweep", "--bands", f"{band}", "--workers", "2")
    assert swept["label"] == "EXPLORATORY" and swept["bands"] == [band, band]
    assert swept["potentially_new_this_run"] == 1
    tiles = sorted((bulk / "data" / "sweep" / "gaia-dr3" / "tiles").glob("b054-*.json"))
    assert len(tiles) == swept["tiles_this_run"] > 100
    again = invoke("discover", "sweep", "--bands", f"{band}", "--workers", "2")
    assert again["tiles_this_run"] == 0  # every tile already done

    report = invoke("discover", "report")
    assert report["label"] == "EXPLORATORY"
    assert report["candidates"]["potentially_new"] == 1
    with open(report["csv"], encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    first = rows[0]
    assert first["status"] == "potentially_new"
    syn_b_ra = 130.1 - 0.4 / math.cos(math.radians(19.97))
    assert abs(float(first["ra_deg"]) - syn_b_ra) < 0.1 and abs(float(first["dec_deg"]) - 19.97) < 0.1
    assert report["top"][0]["candidate_id"] == first["candidate_id"]


def test_the_sweep_refuses_without_a_catalogue_or_without_benchmarks(bulk: Path) -> None:
    result = runner.invoke(cli.app, ["discover", "sweep", "--bands", "54"])
    assert result.exit_code != 0 and "run `sdp discover prepare` first" in str(result.exception)
    invoke("discover", "prepare", "--workers", "1", "--count", "1")
    for p in (bulk / "artifacts").rglob("*"):
        if p.is_file() and "cga20" in str(p):
            p.unlink()  # the cga20 catalogue was never fetched
    result = runner.invoke(cli.app, ["discover", "sweep", "--bands", "54"])
    assert result.exit_code != 0 and "no stored ['cga20']; run `sdp benchmark fetch` first" in str(result.exception)
    assert (
        invoke("discover", "sweep", "--bands", "54", "--without-benchmarks", "--workers", "2")[
            "potentially_new_this_run"
        ]
        == 0
    )


def test_bands_out_of_range_are_refused(bulk: Path) -> None:
    invoke("discover", "prepare", "--workers", "1", "--count", "1")
    result = runner.invoke(cli.app, ["discover", "sweep", "--bands", "80:95"])
    assert result.exit_code != 0 and "bands run 0..89; got 80:95" in result.output


def test_a_report_needs_swept_tiles(bulk: Path) -> None:
    result = runner.invoke(cli.app, ["discover", "report"])
    assert result.exit_code != 0 and "run `sdp discover sweep` first" in str(result.exception)


def test_prepare_needs_a_bulk_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SDP_DATA", str(tmp_path / "data"))
    result = runner.invoke(cli.app, ["discover", "prepare"])
    assert result.exit_code != 0 and "run `sdp archive bulk` first" in str(result.exception)


def test_one_folder_holds_results_of_one_set_of_settings(tmp_path: Path) -> None:
    path = tmp_path / "x" / "_settings.json"
    discover._settings_guard(path, {"a": 1}, "sweep")
    discover._settings_guard(path, {"a": 1}, "sweep")
    with pytest.raises(DomainError, match=r"was made with other sweep settings; pass a new --name"):
        discover._settings_guard(path, {"a": 2}, "sweep")


def test_the_precut_keeps_exactly_the_stars_the_quality_cuts_keep() -> None:
    from sdp.adapters.archives.dialect import curate_columnar, load_dialects
    from sdp_stellar.quality import QualityCuts

    cuts = load_predeclaration()["parameters"]["quality_cuts"]
    rows = native(field("h-chi-persei"), 1)
    curated = curate_columnar(load_dialects()["gaia-dr3"], rows)
    exact, _ = QualityCuts.from_parameters(cuts).apply(curated)
    pre = discover.precut_mask(rows, cuts)
    assert pre.sum() == exact.num_rows < rows.num_rows


# --- the predeclared run -------------------------------------------------------------------------


@pytest.fixture
def k() -> Iterator[InMemoryKernel]:
    base = build_in_memory_kernel(StellarClusterProtocol(), services={"clusterers": clusterers()})
    sky = pa.concat_tables([native(field(t["target_id"]), 10_000_000 * (i + 1)) for i, t in enumerate(targets())])
    ports = dataclasses.replace(
        base.kernel.ports, curator=YamlDialectCurator.packaged(), gateways={"gaia": FakeArchive(GAIA, sky)}
    )
    kernel = dataclasses.replace(base, kernel=Kernel.build(ports))
    cli.use_runtime(lambda: kernel)
    yield kernel
    cli.use_runtime(cli._production_runtime)


def test_the_predeclared_run_needs_both_benchmarks(k: InMemoryKernel) -> None:
    result = runner.invoke(cli.app, ["discover", "run", "--signer", "leigh"])
    assert result.exit_code != 0 and "no stored ['hr23', 'cga20']; run `sdp benchmark fetch` first" in str(
        result.exception
    )


@pytest.mark.req("STL-009")
@pytest.mark.tier("T8")
def test_the_predeclared_run_reads_gaia_and_issues_a_verdict(k: InMemoryKernel) -> None:
    store_benchmarks(k.store)
    out = invoke("discover", "run", "--signer", "leigh", "--null-fields", "3", "--bootstrap-resamples", "100")
    assert out["verdict"] == "VERIFIED"
    assert set(out["checks"].values()) == {"pass"}
    assert [c["candidate_id"][:8] for c in out["potentially_new"]] == ["praesepe"]
    assert out["potentially_new"][0]["statement"].endswith("is a candidate open cluster, not a confirmed discovery.")
    manifest = k.kernel.query_evidence.manifest(out["experiment_id"])
    assert manifest is not None
    roles = {i["role"] for i in manifest["inputs"]}
    assert {"benchmark:hr23", "benchmark:cga20", "field:praesepe"} <= roles
    # freezing again is the same entry, so a second run reuses the predeclaration
    assert len([e for e in k.ledger.entries() if e.kind == "predeclaration"]) == 1
