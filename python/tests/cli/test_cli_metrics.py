"""T4: what each pull costs: rows, bytes, archive requests and seconds, and the store's size (#60)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from sdp.adapters.storage.nas import NasArtifactStore
from sdp.cli import app as cli
from sdp.kernel.application.investigate import STORED_BYTES, _MeteredStore
from sdp.testing.fakes.storage import InMemoryArtifactStore
from sdp.testing.investigate import (
    FIELD_CENTRE,
    gaia_archive,
    investigate_ports,
    observation_archive,
    synthetic_catalogue,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T4")
runner = CliRunner()
REGION = f"icrs;circle({FIELD_CENTRE[0]},{FIELD_CENTRE[1]},0.5)"


@pytest.fixture
def k() -> Iterator[InMemoryKernel]:
    catalogue = synthetic_catalogue()
    kernel = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=investigate_ports(catalogue=catalogue, observations=observation_archive(catalogue)),
    )
    cli.use_runtime(lambda: kernel)
    yield kernel
    cli.use_runtime(cli._production_runtime)


def invoke(*args: str, env: dict[str, str] | None = None) -> Any:
    result = runner.invoke(cli.app, list(args), env=env)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


ACQUIRE = (
    "archive", "acquire", "--source", "synthetic-gaia", "--release", "gaia:DR3",
    "--ra", str(FIELD_CENTRE[0]), "--dec", str(FIELD_CENTRE[1]), "--radius", "0.5",
    "--column", "source_key", "--column", "ra_deg", "--column", "dec_deg",
)  # fmt: skip


@pytest.mark.req("INV-017")
def test_acquire_reports_rows_bytes_requests_and_time_and_a_reread_costs_nothing(k: InMemoryKernel) -> None:
    first = invoke(*ACQUIRE)
    assert first["rows"] > 40  # the synthetic archive answers 40 rows a call, so this read paged
    assert first["requests"] == -(-first["rows"] // 40)
    assert first["raw_bytes"] > 0 and first["curated_bytes"] > 0
    assert first["stored_bytes"] > first["raw_bytes"] + first["curated_bytes"]  # the parts as well
    assert first["seconds"] >= 0 and first["truncated"] is False
    again = invoke(*ACQUIRE)
    assert again["reused"] is True and again["requests"] == 0 and again["stored_bytes"] == 0
    assert again["rows"] == first["rows"]


@pytest.mark.req("INV-017")
def test_a_run_reports_seconds_and_stored_bytes_per_stage(k: InMemoryKernel, tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text(yaml.safe_dump(synthetic_profile().to_document(), sort_keys=False))
    run = invoke("investigate", "--region", REGION, "--profile", str(profile))
    timing = run["timing"]
    assert set(timing) == {"identify", "retrieve", "test", "candidates"}
    assert all(t["state"] == "completed" and t["seconds"] >= 0 for t in timing.values())
    assert all(isinstance(t["stored_bytes"], int) and t["stored_bytes"] > 0 for t in timing.values())
    # every byte a stage counted is in the store, and nothing is counted twice
    assert sum(t["stored_bytes"] for t in timing.values()) <= sum(len(b) for b in k.store._blobs.values())

    # a resumed run reads every stage back: nothing new is stored
    resumed = invoke("investigate", "--region", REGION, "--profile", str(profile), "--resume", run["set_id"])
    assert resumed["timing"] == timing
    listed = invoke("investigate", "sets")
    assert listed[0]["timing"] == timing


@pytest.mark.req("INV-017")
def test_the_meter_counts_equal_bytes_once() -> None:
    meter = _MeteredStore(InMemoryArtifactStore())
    meter.put(b"one", "text/plain")
    meter.put(b"one", "text/plain", logical_path="again/one.txt")
    meter.put(b"three", "text/plain")
    meter.add(10)  # an acquisition's own bytes
    assert meter.take() == 3 + 5 + 10 and meter.take() == 0
    assert STORED_BYTES == "stored_bytes"


def test_artifact_usage_sizes_the_store_by_media_type_against_the_free_space_floor(tmp_path: Path) -> None:
    store = NasArtifactStore(tmp_path / "store")
    store.put(b'{"a": 1}', "application/json")
    store.put(b'{"b": 22}', "application/json", logical_path="sets/x.json")
    store.put(b"PAR1" + b"\0" * 96, "application/vnd.apache.parquet")
    env = {"SDP_ARTIFACT_ROOT": str(tmp_path / "store"), "SDP_MIN_FREE_BYTES": "0"}
    usage = invoke("artifact", "usage", env=env)
    assert usage["objects"] == 3 and usage["bytes"] == 8 + 9 + 100
    assert usage["by_media_type"] == {
        "application/json": {"objects": 2, "bytes": 17},
        "application/vnd.apache.parquet": {"objects": 1, "bytes": 100},
    }
    assert usage["acquisition_allowed"] is True and usage["headroom_bytes"] == usage["free_bytes"]
    blocked = invoke("artifact", "usage", env={**env, "SDP_MIN_FREE_BYTES": str(1 << 62)})
    assert blocked["acquisition_allowed"] is False and blocked["headroom_bytes"] < 0
    missing = runner.invoke(cli.app, ["artifact", "usage"], env={"SDP_ARTIFACT_ROOT": ""})
    assert missing.exit_code != 0 and "SDP_ARTIFACT_ROOT" in str(missing.exception)
