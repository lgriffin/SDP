"""T3: 2MASS and AllWISE copied whole from IRSA's bulk files, our columns only, and read when IRSA is down (DSI-024)."""

from __future__ import annotations

import gzip
import hashlib
import io
from collections.abc import Callable
from pathlib import Path
from typing import IO

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from sdp.adapters.archives.bulk import (
    TWOMASS_PSC_COLUMNS,
    BulkCatalogueGateway,
    BulkSweep,
    Publisher,
    _read_range,
    _size,
)
from sdp.adapters.archives.tap import TapArchiveAdapter, load_sources
from sdp.adapters.bootstrap import archive_gateways
from sdp.adapters.settings import Settings, SettingsError
from sdp.adapters.storage.nas import NasArtifactStore
from sdp.cli import app as cli
from sdp.kernel.domain.archive import CatalogueQuery, Cone

pytestmark = pytest.mark.tier("T3")

TWOMASS = load_sources()["2mass"].descriptor
ALLWISE = load_sources()["allwise"].descriptor
IRSA, AWS = "https://irsa.invalid/allwise/", "https://aws.invalid/allwise/"


# -- 2MASS: IRSA's gzipped, pipe-separated PSC files ---------------------------------------------------


def psc_line(designation: str, ra: float, dec: float, j_msigcom: str = "0.025") -> str:
    """One PSC row as IRSA's files hold it: all 60 columns, the designation padded, \\N for null."""
    row = dict.fromkeys(TWOMASS_PSC_COLUMNS, "1")
    row.update(
        ra=f"{ra:.6f}",
        dec=f"{dec:.6f}",
        designation=f"{designation:<17}",
        j_m="12.467",
        h_m="12.131",
        k_m="11.963",
        j_msigcom=j_msigcom,
        h_msigcom="0.026",
        k_msigcom="0.025",
        ph_qual="AAA",
        jdate="2451809.7124",
        err_maj="0.11",
        err_min="0.06",
        date="2000-09-22",
    )
    return "|".join(row[c] for c in TWOMASS_PSC_COLUMNS)


PSC = {
    "psc_aaa.gz": gzip.compress(
        (
            psc_line("00042876-8955069", 1.119851, -89.91861)
            + "\n"
            + psc_line("00051117-8955596", 1.296576, -89.933235, "\\N")
            + "\n"
        ).encode()
    ),
    "psc_aab.gz": gzip.compress((psc_line("12000000+0000000", 180.0, 0.0) + "\n").encode()),
}
INDEX = b'<a href="psc_aaa.gz">psc_aaa.gz</a> <a href="psc_aab.gz">psc_aab.gz</a> <a href="format_psc.html">x</a>'


def opener_for(served: dict[str, bytes], down: tuple[str, ...] = ()) -> Callable[[str], IO[bytes]]:
    """An HTTP stand-in: these URLs answer with these bytes; a URL under a ``down`` prefix gives no answer."""

    def opener(url: str) -> IO[bytes]:
        if url.startswith(down) or url not in served:
            raise OSError(f"no answer from {url}")
        return io.BytesIO(served[url])

    return opener


def twomass_sweep(tmp_path: Path, files: dict[str, bytes]) -> BulkSweep:
    base = "https://irsa.invalid/2MASS/download/allsky/"
    served = {base: INDEX, **{base + name: data for name, data in files.items()}}
    return BulkSweep(
        store=NasArtifactStore(tmp_path / "store"),
        descriptor=TWOMASS,
        work_dir=tmp_path / "downloads",
        columns=TWOMASS.column_names(),
        publisher=Publisher("2mass-psc", (base,)),
        opener=opener_for(served),
        sleep=lambda _s: None,
    )


def stored(sweep: BulkSweep, stem: str) -> pa.Table:
    artifact = sweep.store.resolve(f"{sweep.prefix}/{stem}.parquet")
    assert artifact is not None
    return pq.read_table(pa.BufferReader(sweep.store.get(artifact)))


@pytest.mark.req("DSI-024")
def test_the_2mass_files_keep_our_columns_typed_with_the_padding_trimmed_and_nulls_kept(tmp_path: Path) -> None:
    sweep = twomass_sweep(tmp_path, PSC)
    files = sweep.files()
    assert [(f.name, f.md5) for f in files] == [("psc_aaa.gz", ""), ("psc_aab.gz", "")]
    results = sweep.run(files)
    assert sorted((r.name, r.status, r.rows) for r in results) == [
        ("psc_aaa.gz", "stored", 2),
        ("psc_aab.gz", "stored", 1),
    ]
    table = stored(sweep, "psc_aaa")
    assert table.column_names == list(TWOMASS.column_names())
    assert table.schema.field("ra").type == pa.float64() and table.schema.field("ph_qual").type == pa.string()
    assert table.column("designation").to_pylist() == ["00042876-8955069", "00051117-8955596"]
    assert table.column("j_msigcom").to_pylist() == [0.025, None]
    record = sweep.store.get(sweep.store.resolve(sweep.record_path(files[0])))  # type: ignore[arg-type]
    assert (
        b'"checked": "gzip-crc"' in record
        and b'"url": "https://irsa.invalid/2MASS/download/allsky/psc_aaa.gz"' in record
    )
    # nothing waits in the download folder once a file is stored
    assert list((tmp_path / "downloads").iterdir()) == []


@pytest.mark.req("DSI-024")
def test_a_cut_off_2mass_file_fails_its_gzip_check_and_only_that_file(tmp_path: Path) -> None:
    sweep = twomass_sweep(tmp_path, {**PSC, "psc_aab.gz": PSC["psc_aab.gz"][:-6]})
    results = {r.name: r for r in sweep.run(sweep.files())}
    assert results["psc_aaa.gz"].status == "stored"
    assert results["psc_aab.gz"].status == "failed"
    assert sweep.store.resolve(f"{sweep.prefix}/psc_aab.parquet") is None


# -- AllWISE: IRSA's HEALPix Parquet, on IRSA and on AWS -------------------------------------------------

#: AllWISE file names as IRSA lists them, with the stars each holds
WISE = {
    "wise-allwise.parquet/healpix_k0=0/healpix_k5=0/part0.snappy.parquet": [("J000001.00+000001.0", 0.0003, 0.0003)],
    "wise-allwise.parquet/healpix_k0=6/healpix_k5=6390/part0.snappy.parquet": [
        ("J080000.00+300000.0", 120.0, 30.0),
        ("J080001.00+300100.0", 120.004, 30.017),
        ("J080400.00+300000.0", 121.0, 30.0),
    ],
}


def wise_file(stars: list[tuple[str, float, float]]) -> bytes:
    """A Parquet file shaped like IRSA's: every declared column (int64 flags, as IRSA stores them) and many more."""
    n = len(stars)
    columns: dict[str, pa.Array] = {
        "designation": pa.array([s[0] for s in stars]),
        "ra": pa.array([s[1] for s in stars]),
        "dec": pa.array([s[2] for s in stars]),
        "sigra": pa.array([0.04] * n),
        "sigdec": pa.array([0.04] * n),
        "w1mpro": pa.array([12.0 + i for i in range(n)]),
        "w1sigmpro": pa.array([0.02] * n),
        "w2mpro": pa.array([None] * (n - 1) + [11.9], type=pa.float64()),
        "w2sigmpro": pa.array([0.03] * n),
        "ph_qual": pa.array(["AAUU"] * n),
        "cc_flags": pa.array(["0000"] * n),
        "ext_flg": pa.array([0] * n, type=pa.int64()),
    }
    for k in range(60):  # the columns SDP does not read: most of each file
        columns[f"other_{k}"] = pa.array([f"{k}-{i}-" + "x" * 4000 for i in range(n)])
    sink = pa.BufferOutputStream()
    pq.write_table(pa.table(columns), sink, compression="none", write_statistics=False)
    data: bytes = sink.getvalue().to_pybytes()
    return data


def wise_mirror(files: dict[str, bytes], rows: dict[str, int] | None = None) -> dict[str, bytes]:
    """What one mirror serves: the MD5 list, the row counts and the files, under their published names."""
    md5s = "".join(f"{hashlib.md5(d, usedforsecurity=False).hexdigest()}  {n}\n" for n, d in files.items())
    md5s = "1636a37e79772446f038628f6d858f06  wise-allwise.parquet/_common_metadata\n" + md5s
    counts = "path,nrows\n" + "".join(f"{n},{(rows or {}).get(n, len(WISE[n]))}\n" for n in files)
    return {"wise-allwise-md5sums.txt": md5s.encode(), "wise-allwise-row-counts-per-file.csv": counts.encode(), **files}


class Mirrors:
    """Byte-range reads from stand-in mirrors, counting what was read; a mirror can be down."""

    def __init__(self, served: dict[str, dict[str, bytes]], down: tuple[str, ...] = ()) -> None:
        self.urls = {base + name: data for base, files in served.items() for name, data in files.items()}
        self.down = down
        self.bytes_read = 0

    def opener(self, url: str) -> IO[bytes]:
        return opener_for(self.urls, self.down)(url)

    def read_range(self, url: str, start: int, length: int) -> bytes:
        self.bytes_read += length
        return self.opener(url).read()[start : start + length]

    def size(self, url: str) -> int:
        return len(self.opener(url).read())


def wise_sweep(tmp_path: Path, mirrors: Mirrors) -> BulkSweep:
    return BulkSweep(
        store=NasArtifactStore(tmp_path / "store"),
        descriptor=ALLWISE,
        work_dir=tmp_path / "downloads",
        columns=ALLWISE.column_names(),
        publisher=Publisher(
            "healpix-parquet",
            (IRSA, AWS),
            md5sums="wise-allwise-md5sums.txt",
            row_counts="wise-allwise-row-counts-per-file.csv",
        ),
        opener=mirrors.opener,
        read_range=mirrors.read_range,
        size=mirrors.size,
        sleep=lambda _s: None,
    )


WISE_FILES = {name: wise_file(stars) for name, stars in WISE.items()}


@pytest.mark.req("DSI-024")
def test_allwise_reads_only_our_columns_by_byte_range_and_checks_each_row_count(tmp_path: Path) -> None:
    mirrors = Mirrors({IRSA: wise_mirror(WISE_FILES)})
    sweep = wise_sweep(tmp_path, mirrors)
    files = sweep.files()
    assert [(f.stem, f.rows) for f in files] == [
        ("wise-allwise.parquet_healpix_k0_0_healpix_k5_0_part0", 1),
        ("wise-allwise.parquet_healpix_k0_6_healpix_k5_6390_part0", 3),
    ]
    results = sweep.run(files)
    assert sorted((r.status, r.rows) for r in results) == [("stored", 1), ("stored", 3)]
    # the footer and our 12 columns, not the 60 others
    assert mirrors.bytes_read < sum(len(d) for d in WISE_FILES.values()) / 5
    table = stored(sweep, files[1].stem)
    assert table.column_names == list(ALLWISE.column_names())
    assert table.schema.field("ext_flg").type == pa.int32()  # the declared type, not IRSA's int64
    assert table.column("w2mpro").to_pylist() == [None, None, 11.9]
    record = sweep.store.get(sweep.store.resolve(sweep.record_path(files[1])))  # type: ignore[arg-type]
    assert b'"checked": "rows"' in record


@pytest.mark.req("DSI-024")
def test_an_allwise_file_short_of_its_published_rows_is_refused(tmp_path: Path) -> None:
    name = next(iter(WISE))
    sweep = wise_sweep(tmp_path, Mirrors({IRSA: wise_mirror(WISE_FILES, rows={name: 2})}))
    results = {r.name: r for r in sweep.run(sweep.files())}
    assert results[name].status == "failed" and "the publisher lists 2" in str(results[name].error)
    assert sum(r.status == "stored" for r in results.values()) == 1


@pytest.mark.req("DSI-024")
def test_when_irsa_is_down_the_list_and_every_file_come_from_aws(tmp_path: Path) -> None:
    mirrors = Mirrors({IRSA: wise_mirror(WISE_FILES), AWS: wise_mirror(WISE_FILES)}, down=(IRSA,))
    sweep = wise_sweep(tmp_path, mirrors)
    results = sweep.run(sweep.files())
    assert [r.status for r in results] == ["stored", "stored"]
    for f in sweep.files():
        record = sweep.store.get(sweep.store.resolve(sweep.record_path(f)))  # type: ignore[arg-type]
        assert f'"url": "{AWS}{f.name}"'.encode() in record


def query(columns: tuple[str, ...] = ("designation", "ra", "dec", "w1mpro", "ext_flg")) -> CatalogueQuery:
    return CatalogueQuery("allwise", ALLWISE.release_label, Cone(120.0, 30.0, 0.05), columns, (), 1000, 1000)


@pytest.mark.req("DSI-024")
def test_a_complete_copy_answers_fields_with_no_call_to_irsa_and_an_incomplete_one_is_not_read(tmp_path: Path) -> None:
    sweep = wise_sweep(tmp_path, Mirrors({IRSA: wise_mirror(WISE_FILES)}))
    sweep.run(sweep.files())
    store = sweep.store
    env = {"SDP_DATABASE_URL": "postgresql://unused", "SDP_ARTIFACT_ROOT": str(tmp_path / "store")}
    gateways = archive_gateways(Settings.from_env(env), store)
    assert isinstance(gateways["allwise"], BulkCatalogueGateway)
    assert isinstance(gateways["2mass"], TapArchiveAdapter)  # no copy of 2MASS: IRSA as before
    g = gateways["allwise"]
    handle = g.query(query())
    assert handle.rendered_text.startswith("BULK SELECT")
    rows = pa.Table.from_batches(list(g.fetch(handle))).to_pylist()
    assert [r["designation"] for r in rows] == ["J080000.00+300000.0", "J080001.00+300100.0"]  # by designation
    assert isinstance(rows[0]["ext_flg"], int)
    assert isinstance(
        archive_gateways(Settings.from_env({**env, "SDP_SURVEY_SOURCE": "tap"}), store)["allwise"], TapArchiveAdapter
    )

    # one file gone: the copy is incomplete, so fields go to IRSA again, and a forced read is refused
    victim = sweep.files()[0]
    (tmp_path / "store" / "paths" / sweep.prefix / f"{victim.stem}.parquet").unlink()
    assert isinstance(archive_gateways(Settings.from_env(env), store)["allwise"], TapArchiveAdapter)
    forced = archive_gateways(Settings.from_env({**env, "SDP_SURVEY_SOURCE": "bulk"}), store)["allwise"]
    with pytest.raises(ValueError, match="1 files missing"):
        list(forced.fetch(forced.query(query())))
    with pytest.raises(SettingsError, match="SDP_SURVEY_SOURCE"):
        Settings.from_env({**env, "SDP_SURVEY_SOURCE": "irsa"})


@pytest.mark.req("DSI-024")
def test_a_range_read_of_a_local_file_matches_its_bytes(tmp_path: Path) -> None:
    path = tmp_path / "f.parquet"
    path.write_bytes(bytes(range(256)) * 4)
    assert _size(path.as_uri()) == 1024
    assert _read_range(path.as_uri(), 250, 10) == bytes(range(250, 256)) + bytes(range(4))


@pytest.mark.req("DSI-024")
def test_the_cli_copies_allwise_from_a_given_folder_and_refuses_what_it_cannot_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "irsa"
    for name, data in wise_mirror(WISE_FILES).items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("SDP_MIN_FREE_BYTES", "0")
    runner = CliRunner()
    run = runner.invoke(cli.app, ["archive", "bulk", "--source", "allwise", "--base-url", folder.as_uri() + "/"])
    assert run.exit_code == 0, run.output
    assert "healpix_k5=6390/part0.snappy.parquet stored 3 rows" in run.stderr
    assert '"source": "allwise"' in run.stdout and '"rows": 4' in run.stdout
    unknown = runner.invoke(cli.app, ["archive", "bulk", "--source", "ps1"])
    assert unknown.exit_code != 0 and "no bulk files SDP reads for 'ps1'" in unknown.output
    subset = runner.invoke(cli.app, ["archive", "bulk", "--source", "2mass", "--subset", "rvs"])
    assert subset.exit_code != 0 and "only the Gaia copy has subsets" in subset.output


@pytest.mark.req("DSI-024")
def test_a_list_short_of_the_releases_files_is_refused_and_not_stored(tmp_path: Path) -> None:
    sweep = twomass_sweep(tmp_path, PSC)
    sweep.publisher = Publisher("2mass-psc", sweep.publisher.mirrors, file_count=3)  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="names 2 files, not the 3 of the release"):
        sweep.files()
    assert sweep.store.resolve(f"{sweep.prefix}/_FILES.txt") is None
    assert BulkCatalogueGateway(sweep.store, TWOMASS).complete() is False


@pytest.mark.req("DSI-024")
def test_a_file_the_publisher_replaced_is_copied_again(tmp_path: Path) -> None:
    sweep = wise_sweep(tmp_path, Mirrors({IRSA: wise_mirror(WISE_FILES)}))
    sweep.run(sweep.files())
    name = next(iter(WISE))
    grown = {**WISE_FILES, name: wise_file([*WISE[name], ("J000002.00+000002.0", 0.0006, 0.0006)])}
    again = wise_sweep(tmp_path, Mirrors({IRSA: wise_mirror(grown, rows={name: 2})}))
    results = {r.name: r for r in again.run(again.files())}
    assert (results[name].status, results[name].rows) == ("stored", 2)
    assert sum(r.status == "reused" for r in results.values()) == 1
    assert stored(again, again.files()[0].stem).num_rows == 2
