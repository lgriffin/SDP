"""T11 logic, run offline: the weekly smoke names drifted columns against the last recording (DSI-008)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pytest
from typer.testing import CliRunner

from sdp.adapters.archives.smoke import run_smoke
from sdp.adapters.archives.tap import TapSource
from sdp.adapters.archives.transport import HttpRequest, HttpResponse, TransportError
from sdp.adapters.archives.votable import to_votable
from sdp.cli.app import app

from .conftest import GAIA, gaia_native

pytestmark = pytest.mark.tier("T11")


@dataclass
class FakeLiveTap:
    """Stands in for a live service: answers TAP_SCHEMA with ``columns`` and any cone with fixture rows."""

    columns: dict[str, str]

    def send(self, request: HttpRequest) -> HttpResponse:
        if b"TAP_SCHEMA" in request.body:
            body = to_votable(pa.table({"column_name": list(self.columns), "datatype": list(self.columns.values())}))
        else:
            body = to_votable(gaia_native(3))
        return HttpResponse(200, body)


DECLARED = {c.name: c.datatype for c in GAIA.descriptor.columns}


@pytest.mark.req("DSI-008")
def test_the_smoke_records_then_fails_on_drift_naming_the_column(tmp_path: Path) -> None:
    sources = {"gaia": GAIA}
    first = run_smoke(sources, lambda s: FakeLiveTap(DECLARED), tmp_path / "none", tmp_path / "week1")
    assert [r.problems for r in first] == [()] and first[0].rows == 3
    drifted = {**DECLARED, "ruwe": "double", "new_flag": "short"}

    def live(source: TapSource) -> FakeLiveTap:
        return FakeLiveTap(drifted)

    second = run_smoke(sources, live, tmp_path / "week1", tmp_path / "week2")
    assert second[0].problems == (
        "gaia: column new_flag added (short)",
        "gaia: column ruwe retyped from float to double",
    )
    assert list((tmp_path / "week2" / "gaia").glob("*.json"))


@dataclass
class Unreachable:
    def send(self, request: HttpRequest) -> HttpResponse:
        raise TransportError(f"POST {request.url}: Connection reset by peer")


@pytest.mark.req("DSI-008")
def test_an_unreachable_archive_is_reported_and_the_rest_still_record(tmp_path: Path) -> None:
    # sources are asked in sorted order, so the first one asked is the unreachable 2mass
    transports: list[Unreachable | FakeLiveTap] = [Unreachable(), FakeLiveTap(DECLARED)]
    results = run_smoke({"2mass": GAIA, "gaia": GAIA}, lambda s: transports.pop(0), tmp_path / "none", tmp_path / "rec")
    assert results[0].source_id == "2mass" and results[0].rows is None
    assert "Connection reset by peer" in results[0].problems[0]
    assert results[1].problems == () and results[1].rows == 3
    assert list((tmp_path / "rec" / "gaia").glob("*.json"))


def test_the_cli_lists_the_packaged_sources() -> None:
    result = CliRunner().invoke(app, ["archive", "sources"])
    assert result.exit_code == 0, result.output
    assert {"gaia", "2mass", "ps1"} <= {s["source_id"] for s in __import__("json").loads(result.output)}
