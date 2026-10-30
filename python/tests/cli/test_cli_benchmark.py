"""T4: `sdp benchmark` stores known-cluster catalogues where `experiment define` can name them (DSI-015)."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from sdp.adapters.archives.benchmarks import CATALOGUES, benchmark_table
from sdp.cli import app as cli
from sdp.kernel.domain.ids import ArtifactId
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T4")
runner = CliRunner()
#: refusals are printed in a Rich box that wraps at the terminal width, which is narrow on CI runners
WIDE = {"NO_COLOR": "1", "COLUMNS": "200"}


def _plain(output: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", output)


COLUMNS = {
    "name": "Name",
    "ra_deg": "RA_ICRS",
    "dec_deg": "DE_ICRS",
    "pm_ra_cosdec_masyr": "pmRA",
    "pm_dec_masyr": "pmDE",
}


class FakeVizier:
    def __init__(self) -> None:
        self.asked: list[tuple[str, dict[str, str]]] = []

    def fetch(self, benchmark_id: str, overrides: Mapping[str, str] | None = None) -> pa.Table:
        self.asked.append((benchmark_id, dict(overrides or {})))
        raw = pa.table(
            {
                "name": ["Fixture_A"],
                "ra_deg": [1.0],
                "dec_deg": [2.0],
                "pm_ra_cosdec_masyr": [3.0],
                "pm_dec_masyr": [4.0],
            }
        )
        return benchmark_table(CATALOGUES[benchmark_id], raw, COLUMNS)


@pytest.fixture
def k(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[InMemoryKernel, FakeVizier]]:
    kernel = build_in_memory_kernel()
    fake = FakeVizier()
    cli.use_runtime(lambda: kernel)
    monkeypatch.setattr(cli, "_known_clusters", lambda: fake)
    yield kernel, fake
    cli.use_runtime(cli._production_runtime)


def invoke(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.req("DSI-015")
def test_fetch_stores_both_catalogues_and_prints_the_input_to_define_with(k: tuple[InMemoryKernel, FakeVizier]) -> None:
    kernel, fake = k
    out = invoke("benchmark", "fetch")
    assert [(o["benchmark"], o["release"], o["clusters"]) for o in out] == [
        ("hr23", "hr23:v1.0", 1),
        ("cga20", "cga20:v1", 1),
    ]
    assert [a for a, _ in fake.asked] == ["hr23", "cga20"]
    hr23 = out[0]
    assert hr23["input"] == f"benchmark:hr23={hr23['artifact']}"
    assert hr23["columns"] == COLUMNS
    assert kernel.store.resolve("benchmarks/hr23/hr23_v1.0.parquet") == ArtifactId(hr23["artifact"])
    stored = pq.read_table(io.BytesIO(kernel.store.get(ArtifactId(hr23["artifact"]))))
    assert stored.schema.metadata[b"sdp.benchmark.release"] == b"hr23:v1.0"
    assert kernel.store.media_type(ArtifactId(hr23["artifact"])) == "application/vnd.apache.parquet"


def test_fetch_one_catalogue_with_a_column_override(k: tuple[InMemoryKernel, FakeVizier]) -> None:
    _, fake = k
    out = invoke("benchmark", "fetch", "--catalogue", "cga20", "--column", "name=Cluster")
    assert [o["benchmark"] for o in out] == ["cga20"]
    assert fake.asked == [("cga20", {"name": "Cluster"})]


def test_a_column_override_needs_exactly_one_catalogue(k: tuple[InMemoryKernel, FakeVizier]) -> None:
    _, fake = k
    result = runner.invoke(cli.app, ["benchmark", "fetch", "--column", "name=Cluster"], env=WIDE)
    assert result.exit_code != 0
    assert "name exactly one --catalogue" in _plain(result.output)
    assert fake.asked == []


def test_an_unknown_catalogue_is_refused_before_any_download(k: tuple[InMemoryKernel, FakeVizier]) -> None:
    _, fake = k
    result = runner.invoke(cli.app, ["benchmark", "fetch", "--catalogue", "mwsc"], env=WIDE)
    assert result.exit_code != 0
    assert "unknown ['mwsc']; known: ['cga20', 'hr23']" in _plain(result.output)
    assert fake.asked == []


def test_list_shows_what_is_stored(k: tuple[InMemoryKernel, FakeVizier]) -> None:
    before = invoke("benchmark", "list")
    assert [(b["benchmark"], b["artifact"], b["input"]) for b in before] == [
        ("cga20", None, None),
        ("hr23", None, None),
    ]
    assert before[1]["vizier_table"] == "J/A+A/673/A114/clusters"
    fetched = invoke("benchmark", "fetch", "--catalogue", "hr23")
    after = invoke("benchmark", "list")
    assert after[1]["artifact"] == fetched[0]["artifact"] and after[1]["input"] == fetched[0]["input"]
    assert after[0]["artifact"] is None


#: a table saved from a VizieR page: every FIELD carries its UCD and unit
SAVED = (
    b'<?xml version="1.0"?><VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3"><RESOURCE><TABLE>'
    b'<FIELD name="Name" ucd="meta.id;meta.main" unit="" datatype="char" arraysize="*"/>'
    b'<FIELD name="RA_ICRS" ucd="pos.eq.ra;meta.main" unit="deg" datatype="double"/>'
    b'<FIELD name="DE_ICRS" ucd="pos.eq.dec;meta.main" unit="deg" datatype="double"/>'
    b'<FIELD name="pmRA*" ucd="pos.pm;pos.eq.ra" unit="mas/yr" datatype="double"/>'
    b'<FIELD name="pmDE" ucd="pos.pm;pos.eq.dec" unit="mas/yr" datatype="double"/>'
    b"<DATA><TABLEDATA><TR><TD>Fixture_A</TD><TD>130.1</TD><TD>19.67</TD><TD>-36.0</TD><TD>-12.9</TD></TR>"
    b"<TR><TD>Fixture_B</TD><TD>56.75</TD><TD>24.12</TD><TD>19.9</TD><TD>-45.4</TD></TR></TABLEDATA></DATA>"
    b"</TABLE></RESOURCE></VOTABLE>"
)


@pytest.mark.req("DSI-017")
def test_a_saved_votable_is_stored_without_any_download(k: tuple[InMemoryKernel, FakeVizier], tmp_path: Path) -> None:
    kernel, fake = k
    saved = tmp_path / "hr23.vot"
    saved.write_bytes(SAVED)
    out = invoke("benchmark", "fetch", "--file", f"hr23={saved}")
    assert fake.asked == []
    assert [(o["benchmark"], o["clusters"]) for o in out] == [("hr23", 2)]
    stored = pq.read_table(io.BytesIO(kernel.store.get(ArtifactId(out[0]["artifact"]))))
    assert stored.schema.metadata[b"sdp.benchmark.source"] == b"VizieR J/A+A/673/A114/clusters from file hr23.vot"
    assert invoke("benchmark", "list")[1]["artifact"] == out[0]["artifact"]


def test_a_file_names_its_own_catalogue(k: tuple[InMemoryKernel, FakeVizier], tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app, ["benchmark", "fetch", "--catalogue", "hr23", "--file", f"hr23={tmp_path / 'x.vot'}"], env=WIDE
    )
    assert result.exit_code != 0
    assert "--file names its own catalogue" in _plain(result.output)
