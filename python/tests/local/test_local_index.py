"""T3: a local folder source is indexed from headers alone, and nothing is ever written to it (#71)."""

from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sdp.adapters.local.fits_header import NotFits, parse_value, read_header
from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.sources import LocalSourceError, LocalSources, data_dir
from sdp.cli import app as cli

from .fits_files import fits_bytes, raw_sub, write

pytestmark = pytest.mark.tier("T3")
runner = CliRunner()


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A small copy of the astrophotography folder's shapes: subs, calibration folders and a Siril stack."""
    root = tmp_path / "astrophotography"
    write(root / "M31" / "2025-09-14" / "img-0001.fits", raw_sub())
    write(root / "M31" / "2025-09-14" / "img-0002.fits", raw_sub(**{"DATE-OBS": "2025-09-14T22:01:16.0"}))
    write(root / "NGC7000" / "img-0001.FIT", raw_sub(OBJECT="NGC7000", EXPOSURE=25.0, FILTER="DUAL"))
    write(root / "M31" / "darks" / "dark-0001.fits", raw_sub(OBJECT=""))
    write(root / "M31" / "flats" / "flat-0001.fits", raw_sub(IMAGETYP="Flat Field"))
    stack = {
        "SIMPLE": True, "BITPIX": -32, "NAXIS": 3, "NAXIS1": 3536, "NAXIS2": 3536, "NAXIS3": 3,
        "OBJECT": "M31", "STACKCNT": 412, "EXPTIME": 4120.0, "LIVETIME": 4120.0,
    }  # fmt: skip
    write(root / "M31" / "processed" / "r_pp_light_stacked.fit", stack)
    solved = {**stack, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 10.68, "CRVAL2": 41.27}
    write(root / "M31" / "processed" / "linear_calibrated.fit", solved)
    (root / "M31" / "notes.txt").write_text("not a frame")
    (root / "M31" / "broken.fits").write_bytes(b"this is not FITS" * 300)
    return root


@pytest.fixture
def data(tmp_path: Path) -> Path:
    return tmp_path / "SDP"


def snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {
        str(p.relative_to(root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def no_writes_under(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every file opened for writing; a test then asserts none of them lies in the source."""
    opened: list[str] = []
    real_open = builtins.open

    def watched(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(c in mode for c in "wax+"):
            opened.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", watched)
    monkeypatch.setattr(io, "open", watched)
    return opened


def test_header_values_are_read_as_the_standard_writes_them() -> None:
    assert parse_value("'M31     '           / target") == "M31"
    assert parse_value("'it''s'") == "it's"
    assert parse_value("                   T / flag") is True
    assert parse_value("                   F") is False
    assert parse_value("                  16") == 16
    assert parse_value("              -32 / bits") == -32
    assert parse_value("             1.5E+01") == 15.0
    assert parse_value("              2.5D-1") == 0.25
    assert parse_value("") is None


@pytest.mark.req("LOC-002")
def test_only_the_header_is_read_never_the_pixels() -> None:
    header = raw_sub()
    data = fits_bytes(header, b"\xff" * 10 * 2880)

    class Counting(io.BytesIO):
        read_bytes = 0

        def read(self, size: int | None = -1) -> bytes:
            chunk = super().read(size)
            Counting.read_bytes += len(chunk)
            return chunk

    parsed = read_header(Counting(data))
    assert parsed["OBJECT"] == "M31" and parsed["EXPOSURE"] == 10.0 and parsed["BAYERPAT"] == "GBRG"
    assert "HISTORY" not in parsed
    assert Counting.read_bytes == 2880, "one header block, none of the ten pixel blocks"


def test_a_file_that_is_not_fits_or_has_no_end_is_refused() -> None:
    with pytest.raises(NotFits, match="SIMPLE"):
        read_header(io.BytesIO(b"x" * 2880))
    endless = ("SIMPLE  =                    T".ljust(80) + "".ljust(80) * 35).encode()
    with pytest.raises(NotFits, match="END"):
        read_header(io.BytesIO(endless))


@pytest.mark.req("LOC-002")
def test_the_index_has_one_row_per_frame_with_its_kind_and_exposure(folder: Path, data: Path) -> None:
    index = FrameIndex(LocalSources(data).add("astro", folder, 1.65), data)
    result = index.update()
    rows = {r["path"]: r for r in index.rows()}
    assert result.files == 8 and result.read == 8 and result.unreadable == 1
    assert "M31/notes.txt" not in rows
    sub = rows["M31/2025-09-14/img-0001.fits"]
    assert (sub["kind"], sub["exposure_s"], sub["filter"], sub["object"], sub["bayer"]) == (
        "light", 10.0, "CLS", "M31", "GBRG"
    )  # fmt: skip
    assert (sub["naxis1"], sub["bitpix"], sub["has_wcs"]) == (3536, 16, False)
    assert rows["NGC7000/img-0001.FIT"]["exposure_s"] == 25.0
    assert rows["M31/darks/dark-0001.fits"]["kind"] == "dark"
    assert rows["M31/flats/flat-0001.fits"]["kind"] == "flat"
    stack = rows["M31/processed/r_pp_light_stacked.fit"]
    assert (stack["kind"], stack["stackcnt"], stack["exposure_s"]) == ("stack", 412, 4120.0)
    assert rows["M31/processed/linear_calibrated.fit"]["has_wcs"] is True
    assert "SIMPLE" in rows["M31/broken.fits"]["error"]
    assert index.path.is_relative_to(data / "index" / "astro")

    summary = index.summary()
    assert summary["by_kind"] == {"dark": 1, "flat": 1, "light": 3, "stack": 2, "unreadable": 1}
    assert summary["targets"]["M31"] == {"frames": 4, "hours": round((20 + 8240) / 3600, 2)}
    assert summary["with_wcs"] == 1


@pytest.mark.req("LOC-002")
def test_a_rerun_reads_only_new_or_changed_files(folder: Path, data: Path) -> None:
    index = FrameIndex(LocalSources(data).add("astro", folder), data)
    index.update()
    assert index.update().read == 0
    changed = folder / "NGC7000" / "img-0001.FIT"
    changed.write_bytes(fits_bytes(raw_sub(OBJECT="NGC7000", EXPOSURE=60.0)))
    os.utime(changed, ns=(1, 1))
    write(folder / "M31" / "2025-09-15" / "img-0001.fits", raw_sub())
    (folder / "M31" / "darks" / "dark-0001.fits").unlink()
    result = index.update()
    assert (result.read, result.removed, result.files) == (2, 1, 8)
    assert {r["path"]: r for r in index.rows()}["NGC7000/img-0001.FIT"]["exposure_s"] == 60.0


@pytest.mark.req("LOC-001")
def test_nothing_is_ever_written_under_the_source(folder: Path, data: Path, no_writes_under: list[str]) -> None:
    before = snapshot(folder)
    sources = LocalSources(data)
    index = FrameIndex(sources.add("astro", folder), data)
    index.update()
    index.update()
    index.summary()
    assert snapshot(folder) == before
    assert not [p for p in no_writes_under if Path(p).resolve().is_relative_to(folder.resolve())]
    assert all(Path(p).resolve().is_relative_to(data.resolve()) for p in no_writes_under)


@pytest.mark.req("LOC-001")
def test_a_source_and_the_data_folder_never_hold_each_other(folder: Path, data: Path, tmp_path: Path) -> None:
    with pytest.raises(LocalSourceError, match="inside"):
        LocalSources(folder / "SDP").add("astro", folder)
    (data / "frames").mkdir(parents=True)
    with pytest.raises(LocalSourceError, match="inside SDP's data folder"):
        LocalSources(data).add("astro", data / "frames")
    with pytest.raises(LocalSourceError, match="lower-case"):
        LocalSources(data).add("My Frames", folder)
    with pytest.raises(LocalSourceError, match="not a folder"):
        LocalSources(data).add("astro", tmp_path / "missing")
    with pytest.raises(LocalSourceError, match="no local source 'astro'; added: none yet"):
        LocalSources(data).get("astro")


def test_the_data_folder_is_sdp_data_else_the_desktop_folder_else_beside_the_artifacts() -> None:
    assert data_dir({"SDP_DATA": "D:/SDP", "SDP_DESKTOP_DATA": "E:/x"}) == Path("D:/SDP")
    assert data_dir({"SDP_DESKTOP_DATA": "E:/x"}) == Path("E:/x")
    assert data_dir({"SDP_ARTIFACT_ROOT": "D:/SDP/artifacts"}) == Path("D:/SDP")
    with pytest.raises(LocalSourceError, match="SDP_DATA"):
        data_dir({})


@pytest.mark.req("LOC-002")
def test_the_commands_add_index_and_report_with_no_database(folder: Path, data: Path) -> None:
    env = {"SDP_DATA": str(data)}

    def run(*args: str) -> Any:
        result = runner.invoke(cli.app, ["local", *args], env=env)
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    added = run("add", "astro", str(folder), "--scale", "1.65")
    assert added["source"]["read_only"] is True and added["source"]["scale_arcsec"] == 1.65
    assert run("list")["astro"]["path"] == str(folder.resolve())
    indexed = run("index", "astro")
    assert indexed["files"] == 8 and indexed["headers_read"] == 8 and indexed["by_kind"]["stack"] == 2
    assert run("index", "astro")["unchanged"] == 8
    assert run("status", "astro")["targets"]["NGC7000"]["frames"] == 1
