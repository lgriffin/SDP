"""T4: `sdp delta fetch` and `sdp delta compare` on invented clusters (STL-032). Not Gaia or VizieR data."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from tests.stellar.test_dr4_delta import stars
from typer.testing import CliRunner

from sdp.adapters.archives import gaia_delta
from sdp.cli import app as cli
from sdp.cli import delta as cmd
from sdp_stellar import dr4_delta as d

pytestmark = pytest.mark.tier("T4")
runner = CliRunner()


def row(name: str) -> dict[str, Any]:
    return {
        "Name": name,
        "RA_ICRS": 100.0,
        "DE_ICRS": 20.0,
        "rt": 0.5,
        "Plx": 2.0,
        "s_Plx": 0.05,
        "pmRA": -5.0,
        "s_pmRA": 0.2,
        "pmDE": 3.0,
        "s_pmDE": 0.2,
    }


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    calls: dict[str, list[str]] = {"cluster": [], "members": [], "cone": [], "match": []}

    def cone(t: object, query: str, what: str) -> pa.Table:
        calls["cone"].append(what)
        s = stars(
            n_members=50, n_field=200, plx_shift=0.01 if "dr3" in query else 0.0, first_id=1 if "dr2" in query else 501
        )
        return pa.table(s)

    def neighbourhood(t: object, match: Any, ids: Any) -> dict[int, int]:
        ids = list(ids)
        calls["match"].append(str(len(ids)))
        return {i: i + 500 for i in ids}

    monkeypatch.setattr(cmd, "transport", lambda: None)
    monkeypatch.setattr(gaia_delta, "vizier", lambda t: None)

    def cluster(v: object, name: str) -> dict[str, Any]:
        calls["cluster"].append(name)
        return row(name)

    def members(v: object, name: str) -> list[int]:
        calls["members"].append(name)
        return list(range(501, 541))

    monkeypatch.setattr(gaia_delta, "hr23_cluster", cluster)
    monkeypatch.setattr(gaia_delta, "hr23_members", members)
    monkeypatch.setattr(gaia_delta, "cone", cone)
    monkeypatch.setattr(gaia_delta, "neighbourhood", neighbourhood)
    return calls


@pytest.mark.req("STL-032")
def test_fetch_keeps_each_cone_and_skips_what_it_has(fakes: dict[str, list[str]], tmp_path: Path) -> None:
    args = [
        "delta",
        "fetch",
        "--release",
        "dr3",
        "--cluster",
        "NGC_2682",
        "--cluster",
        "NGC_188",
        "--out",
        str(tmp_path),
    ]
    assert runner.invoke(cli.app, args).exit_code == 0
    assert (tmp_path / "dr3" / "NGC_2682.parquet").exists() and (tmp_path / "hr23-members" / "NGC_188.json").exists()
    assert len(fakes["cluster"]) == 24 and len(fakes["cone"]) == 2
    assert runner.invoke(cli.app, args).exit_code == 0
    assert len(fakes["cluster"]) == 24 and len(fakes["cone"]) == 2 and len(fakes["members"]) == 2


@pytest.mark.req("STL-032")
def test_compare_writes_the_deltas_and_a_dry_run_summary(fakes: dict[str, list[str]], tmp_path: Path) -> None:
    for release in ("dr2", "dr3"):
        out = runner.invoke(
            cli.app, ["delta", "fetch", "--release", release, "--cluster", "NGC_2682", "--out", str(tmp_path)]
        )
        assert out.exit_code == 0, out.output
    result = runner.invoke(cli.app, ["delta", "compare", "--from", "dr2", "--to", "dr3", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    with (tmp_path / "deltas-dr2-dr3.csv").open(newline="", encoding="utf-8") as handle:
        (only,) = list(csv.DictReader(handle))
    assert only["cluster"] == "NGC_2682" and float(only["delta_parallax_mas"]) == pytest.approx(0.01, abs=0.01)
    assert int(only["unmatched"]) == 0 and int(only["kept"]) == int(only["earlier_n"])
    assert float(only["hr23_catalogue_found_share"]) > 0.9
    summary = json.loads((tmp_path / "summary-dr2-dr3.json").read_text("utf-8"))
    assert summary["status"].startswith("EXPLORATORY") and summary["clusters"] == 1
    assert len(summary["skipped_not_fetched"]) == 23 and summary["settings_sha256"] == d.settings_sha256()
    assert (tmp_path / "matches" / "dr2-dr3" / "NGC_2682.json").exists()


@pytest.mark.req("STL-032")
def test_an_unknown_release_or_cluster_is_refused(fakes: dict[str, list[str]], tmp_path: Path) -> None:
    bad = runner.invoke(cli.app, ["delta", "fetch", "--release", "dr9", "--out", str(tmp_path)])
    assert bad.exit_code != 0 and "unknown release" in str(bad.exception)
    odd = runner.invoke(cli.app, ["delta", "fetch", "--cluster", "NGC_1", "--out", str(tmp_path)])
    assert odd.exit_code != 0 and "not in the seed set" in str(odd.exception)


@pytest.mark.req("STL-032")
def test_an_archive_error_costs_one_cluster_and_the_next_run_fetches_it(
    fakes: dict[str, list[str]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sdp.adapters.archives.transport import TransportError

    good: Any = gaia_delta.cone

    def flaky(t: object, query: str, what: str) -> pa.Table:
        if what.endswith("NGC_188"):
            raise TransportError(f"{what}: HTTP 503")
        return good(t, query, what)

    monkeypatch.setattr(gaia_delta, "cone", flaky)
    args = ["delta", "fetch", "--release", "dr2", "--cluster", "NGC_188", "--cluster", "NGC_2682"]
    first = runner.invoke(cli.app, [*args, "--out", str(tmp_path)])
    assert first.exit_code == 1 and '"NGC_188"' in first.output
    assert (tmp_path / "dr2" / "NGC_2682.parquet").exists() and not (tmp_path / "dr2" / "NGC_188.parquet").exists()
    monkeypatch.setattr(gaia_delta, "cone", good)
    assert runner.invoke(cli.app, [*args, "--out", str(tmp_path)]).exit_code == 0
    assert (tmp_path / "dr2" / "NGC_188.parquet").exists()
