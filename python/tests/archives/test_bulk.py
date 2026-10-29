"""T3: the bulk sweep stores publisher files as Parquet, checks MD5s, resumes and stops at the disk floor (#72)."""

from __future__ import annotations

import gzip
import hashlib
import io
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from sdp.adapters.archives.bulk import BulkSweep, parse_md5sum, select_files
from sdp.adapters.archives.tap import load_sources
from sdp.adapters.storage.nas import NasArtifactStore
from sdp.cli import app as cli
from sdp.kernel.domain.errors import StorageExhausted

pytestmark = pytest.mark.tier("T3")

GAIA = load_sources()["gaia"].descriptor
COLUMNS = GAIA.column_names()


def ecsv(rows: list[dict[str, object]]) -> bytes:
    """A gzipped ECSV shaped like ESA's gaia_source files: a # YAML header, a CSV header, all columns."""
    names = [*COLUMNS, "phot_bp_mean_mag", "designation"]  # extra columns SDP does not keep
    header = "# %ECSV 1.0\n# ---\n# delimiter: ','\n# datatype:\n# - {name: source_id, datatype: int64}\n"
    lines = [",".join(names)]
    for r in rows:
        lines.append(",".join("null" if r.get(n) is None else str(r.get(n)) for n in names))
    return gzip.compress((header + "\n".join(lines) + "\n").encode())


def star(i: int, **over: object) -> dict[str, object]:
    row: dict[str, object] = dict.fromkeys(COLUMNS, 0.5)
    row.update(source_id=1000 + i, ra=10.0 + i, dec=-5.0, astrometric_params_solved=31, duplicated_source="False")
    row["phot_bp_mean_mag"], row["designation"] = 12.0, f"Gaia DR3 {1000 + i}"
    row.update(over)
    return row


def publish(folder: Path, files: dict[str, bytes], corrupt: str | None = None) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    sums = []
    for name, data in files.items():
        (folder / name).write_bytes(data)
        md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
        sums.append(f"{'0' * 32 if name == corrupt else md5}  {name}")
    (folder / "_MD5SUM.txt").write_text("\n".join(sums) + "\n")
    return folder.as_uri() + "/"


def sweep_for(tmp_path: Path, base_url: str, **kw: object) -> BulkSweep:
    return BulkSweep(
        store=NasArtifactStore(tmp_path / "store"),
        descriptor=GAIA,
        work_dir=tmp_path / "downloads",
        columns=COLUMNS,
        base_url=base_url,
        sleep=lambda _s: None,
        **kw,  # type: ignore[arg-type]
    )


FILES = {
    "GaiaSource_000000-003111.csv.gz": ecsv([star(0), star(1, parallax=None)]),
    "GaiaSource_003112-005263.csv.gz": ecsv([star(2), star(3), star(4, duplicated_source="True")]),
}


@pytest.mark.req("DSI-013")
def test_each_file_becomes_a_typed_parquet_of_the_declared_columns_with_its_record(tmp_path: Path) -> None:
    sweep = sweep_for(tmp_path, publish(tmp_path / "esa", FILES))
    results = sweep.run(sweep.files(), workers=2)
    assert sorted((r.name, r.status, r.rows) for r in results) == [
        ("GaiaSource_000000-003111.csv.gz", "stored", 2),
        ("GaiaSource_003112-005263.csv.gz", "stored", 3),
    ]
    store = sweep.store
    first = sweep.files()[0]
    artifact = store.resolve(sweep.parquet_path(first))
    assert artifact is not None
    table = pq.read_table(io.BytesIO(store.get(artifact)))
    assert table.column_names == list(COLUMNS)
    assert table.column("source_id").type == "int64" and table.column("parallax").to_pylist()[1] is None
    assert table.column("duplicated_source").to_pylist() == [False, False]
    assert sweep.status(sweep.files())["rows"] == 5
    assert not any((tmp_path / "downloads").iterdir())  # downloads are removed once stored


@pytest.mark.req("DSI-013")
def test_a_rerun_skips_stored_files_and_a_bad_md5_fails_only_that_file(tmp_path: Path) -> None:
    base = publish(tmp_path / "esa", FILES, corrupt="GaiaSource_003112-005263.csv.gz")
    sweep = sweep_for(tmp_path, base)
    first = sweep.run(sweep.files())
    assert [r.status for r in first] == ["stored", "failed"]
    assert "MD5" in (first[1].error or "")
    publish(tmp_path / "esa", FILES)  # the publisher's sums are right again
    again = sweep.run(sweep.files())
    assert [r.status for r in again] == ["reused", "stored"]


@pytest.mark.req("DSI-013")
def test_the_sweep_stops_at_the_disk_floor_before_downloading(tmp_path: Path) -> None:
    sweep = sweep_for(tmp_path, publish(tmp_path / "esa", FILES), min_free_bytes=10**18)
    with pytest.raises(StorageExhausted, match="run it again to resume"):
        sweep.run(sweep.files())
    assert sweep.status(sweep.files())["done"] == 0


def test_md5sum_lists_only_data_files_in_name_order() -> None:
    files = parse_md5sum("b  GaiaSource_2.csv.gz\nc  _README.txt\na  GaiaSource_1.csv.gz\n")
    assert [f.name for f in files] == ["GaiaSource_1.csv.gz", "GaiaSource_2.csv.gz"]
    assert [f.name for f in select_files(files, 1, None)] == ["GaiaSource_2.csv.gz"]
    assert [f.name for f in select_files(files, None, 1)] == ["GaiaSource_1.csv.gz"]


def test_undeclared_columns_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not declared"):
        BulkSweep(NasArtifactStore(tmp_path), GAIA, tmp_path, ("source_id", "nonsense"))


def test_the_cli_sweeps_then_reports_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = publish(tmp_path / "esa", FILES)
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("SDP_MIN_FREE_BYTES", "0")
    runner = CliRunner()
    run = runner.invoke(cli.app, ["archive", "bulk", "--base-url", base, "--count", "1"])
    assert run.exit_code == 0, run.output
    assert "1/1 GaiaSource_000000-003111.csv.gz stored 2 rows" in run.stderr
    status = runner.invoke(cli.app, ["archive", "bulk", "--base-url", base, "--status"])
    assert '"done": 1' in status.stdout and '"files": 2' in status.stdout
