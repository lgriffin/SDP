"""T9: a recorded experiment re-runs from its evidence and inputs alone and comes out the same (NFR-004).

The release workflow runs infrastructure/reproduce.sh, which runs `sdp reproduce` in a fresh container
built from the pinned lock files; these tests hold the comparison itself to account."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sdp.adapters.bootstrap import clusterers
from sdp.cli.app import app
from sdp.kernel.application.rocrate import build_crate_metadata, zip_crate
from sdp.kernel.ports.driving import DefineExperimentCommand, FreezePredeclarationCommand
from sdp.kernel.science.protocol import ScientificProtocol
from sdp.testing.reproduce import ReproductionError, index_inputs, load_manifest, reproduce
from sdp.testing.synthetic import SERIES_MEDIA_TYPE, SyntheticLinearProtocol, make_series
from sdp.testing.wiring import build_in_memory_kernel
from sdp_stellar.protocol import PARQUET, StellarClusterProtocol, load_predeclaration
from sdp_stellar.synthetic import make_benchmark, make_field, parquet

pytestmark = [pytest.mark.tier("T9"), pytest.mark.req("NFR-004")]


def record(
    tmp: Path,
    protocol: ScientificProtocol,
    inputs: dict[str, tuple[bytes, str]],
    params: dict[str, Any],
    seed: int,
    services: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run once, as the original experiment, and leave its input bytes in ``tmp/inputs``."""
    k = build_in_memory_kernel(protocol, services=services)
    frozen, _ = k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(protocol.predeclare(), "leigh"))
    artifacts = {}
    (tmp / "inputs").mkdir(parents=True, exist_ok=True)
    for i, (role, (data, media_type)) in enumerate(sorted(inputs.items())):
        (tmp / "inputs" / f"input-{i}").write_bytes(data)
        artifacts[role] = k.store.put(data, media_type)
    experiment = k.kernel.define_experiment.execute(
        DefineExperimentCommand(protocol.protocol_id.name, frozen.hash, artifacts, params, seed=seed)
    )
    k.kernel.run_experiment.execute(experiment.experiment_id)
    k.kernel.validate_experiment.execute(experiment.experiment_id)
    manifest = k.kernel.query_evidence.manifest(experiment.experiment_id)
    assert manifest is not None
    (tmp / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def synthetic(tmp: Path, noise: float = 0.1) -> dict[str, Any]:
    series = make_series(seed=3, noise=noise)
    return record(tmp, SyntheticLinearProtocol(), {"series": (series, SERIES_MEDIA_TYPE)}, {}, seed=42)


def test_a_synthetic_experiment_reproduces_from_its_manifest_and_inputs(tmp_path: Path) -> None:
    manifest = synthetic(tmp_path)
    result = reproduce(load_manifest(tmp_path / "manifest.json"), index_inputs(tmp_path / "inputs"))
    assert result.ok, result.differences
    assert result.reproduced_verdict == manifest["verdict"]["status"]


def test_the_bundle_is_verified_before_anything_runs(tmp_path: Path) -> None:
    synthetic(tmp_path)
    data = (tmp_path / "manifest.json").read_bytes()
    files = {"manifest.json": (data, "application/json", "Evidence manifest")}
    meta = build_crate_metadata(
        name="x",
        description="x",
        licence="CC-BY-4.0",
        files=files,
        authors=["a"],
        date_published="2026-10-28",
        citations=[],
    )
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(zip_crate(meta, files))
    assert load_manifest(bundle) == json.loads(data)
    meta["@graph"][-1]["sha256"] = "0" * 64
    bundle.write_bytes(zip_crate(meta, files))
    with pytest.raises(ReproductionError, match="checksum mismatch"):
        load_manifest(bundle)


def test_a_changed_finding_or_verdict_is_named(tmp_path: Path) -> None:
    manifest = synthetic(tmp_path)
    slope = next(f for f in manifest["findings"] if f["name"] == "slope")
    slope["value"] += 1e-3
    manifest["verdict"]["status"] = "VERIFIED" if manifest["verdict"]["status"] != "VERIFIED" else "REJECTED"
    result = reproduce(manifest, index_inputs(tmp_path / "inputs"))
    assert not result.ok
    assert any(d.startswith("finding slope: value") for d in result.differences)
    assert any(d.startswith("verdict:") for d in result.differences)


def test_an_input_that_is_not_there_byte_for_byte_stops_the_reproduction(tmp_path: Path) -> None:
    synthetic(tmp_path)
    (tmp_path / "inputs" / "input-0").write_bytes(b"{}")
    with pytest.raises(ReproductionError, match="inputs not found by checksum"):
        reproduce(load_manifest(tmp_path / "manifest.json"), index_inputs(tmp_path / "inputs"))


def test_an_edited_predeclaration_stops_the_reproduction(tmp_path: Path) -> None:
    manifest = synthetic(tmp_path)
    manifest["intent"]["predeclaration"]["gates"][0]["threshold"] = 1.0
    with pytest.raises(ReproductionError, match="does not hash"):
        reproduce(manifest, index_inputs(tmp_path / "inputs"))


def test_uc01_with_seeded_nulls_and_injections_reproduces_through_the_cli(tmp_path: Path) -> None:
    pre = load_predeclaration()
    inputs = {f"field:{t['target_id']}": (parquet(make_field(t, 7)), PARQUET) for t in pre["targets"]}
    for label in pre["parameters"]["benchmarks"]:
        bid = label.split(":")[0]
        inputs[f"benchmark:{bid}"] = (parquet(make_benchmark(bid, list(pre["targets"]), label)), PARQUET)
    params = {"null_fields_per_region": 1, "bootstrap_resamples": 50}
    manifest = record(
        tmp_path, StellarClusterProtocol(), inputs, params, seed=11, services={"clusterers": clusterers()}
    )
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app, ["reproduce", str(tmp_path / "manifest.json"), "--inputs", str(tmp_path / "inputs"), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    report = json.loads((out / "reproduction.json").read_text())
    assert report["ok"] and report["reproduced_verdict"] == manifest["verdict"]["status"]
    assert list(out.glob("experiments/uc01-stellar-clusters/*/manifest.json"))
