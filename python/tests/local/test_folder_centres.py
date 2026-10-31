"""T3: frames whose header has no OBJECT are placed by their folder, and calibration folders are never
offered to the solver as lights (#143)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.names import NameBook, folder_key, parse_folder
from sdp.adapters.local.solver import Reference
from sdp.adapters.local.solving import LocalSolver, folder_names
from sdp.adapters.local.sources import LocalSource, LocalSourceError, LocalSources
from sdp.cli import app as cli

from .fits_files import raw_sub, write
from .synthetic import M31, bayer_frame

pytestmark = pytest.mark.tier("T3")
runner = CliRunner()
FRAME = "raw/Andromeda/lights/img-0001.fits"


def nameless(tmp_path: Path, relative: str = FRAME) -> tuple[LocalSolver, list[dict[str, object]], Reference]:
    """One sub with an empty OBJECT and no pointing, under a folder named for its target."""
    root = tmp_path / "astrophotography"
    truth = bayer_frame(root / relative, rotation_deg=37.0, seed=7, header={"OBJECT": ""})
    data = tmp_path / "SDP"
    source = LocalSources(data).add("astro", root, 1.65)
    index = FrameIndex(source, data)
    index.update()
    reference = truth.reference
    return LocalSolver(source, data, None, lambda _c: reference), index.rows(), reference


def test_a_folder_is_written_as_the_index_writes_paths() -> None:
    assert folder_key("raw\\IC434\\") == folder_key("./raw/IC434") == "raw/IC434"
    assert parse_folder("raw/IC434=85.25,-2.46") == ("raw/IC434", 85.25, -2.46)
    for bad in ("raw/IC434", "raw/IC434=400,0", "=1,2", "../x=1,2", "D:/x=1,2"):
        with pytest.raises(LocalSourceError):
            parse_folder(bad)


@pytest.mark.req("LOC-025")
def test_a_frame_without_object_solves_from_the_centre_given_for_its_folder(tmp_path: Path) -> None:
    solver, rows, _ = nameless(tmp_path)
    (refused,) = solver.run(rows, rows)
    assert refused.status == "refused" and "--folder raw/Andromeda/lights=RA,DEC" in refused.reason

    assert solver.names is not None
    solver.names.set_folder("raw/Andromeda", *M31)  # covers the lights folder inside it
    assert [o.status for o in solver.run(solver.select(rows, retry_refused=True), rows)] == ["solved"]
    doc = json.loads(solver.solution_path(FRAME).read_text())
    assert doc["rough_centre"] == {"ra_deg": M31[0], "dec_deg": M31[1], "from": "folder centre raw/Andromeda"}
    book = yaml.safe_load(solver.names.path.read_text())
    assert book["folders"]["raw/Andromeda"]["from"] == "you"


@pytest.mark.req("LOC-025")
def test_the_deepest_folder_given_wins(tmp_path: Path) -> None:
    book = NameBook(LocalSource("astro", tmp_path), tmp_path / "SDP")
    book.set_folder("raw", 1, 2)
    book.set_folder("raw/IC434/", 85, -2)
    book = NameBook(LocalSource("astro", tmp_path), tmp_path / "SDP")  # read back from the file
    assert book.folder_centre("raw/IC434/lights/a.fits") == ("raw/IC434", (85.0, -2.0))
    assert book.folder_centre("raw/NGC884/b.fits") == ("raw", (1.0, 2.0))
    assert book.folder_centre("other/c.fits") is None


@pytest.mark.req("LOC-026")
def test_a_folder_name_is_looked_up_as_a_target_and_recorded_as_the_guess_it_is(tmp_path: Path) -> None:
    solver, rows, _ = nameless(tmp_path)
    asked: list[str] = []

    def simbad(name: str) -> tuple[float, float] | None:
        asked.append(name)
        return M31 if name == "Andromeda" else None

    solver.resolve_name = simbad
    assert [o.status for o in solver.run(rows, rows)] == ["solved"]
    assert asked == ["Andromeda"], "lights and raw say nothing about the target, so they are never asked"
    doc = json.loads(solver.solution_path(FRAME).read_text())
    assert doc["rough_centre"]["from"] == "folder name Andromeda"


def test_only_folders_that_could_name_a_target_are_tried() -> None:
    assert folder_names("raw/IC434/lights/a.fits") == ["IC434"]
    assert folder_names("raw/IC434/darks/a.fits") == ["IC434"]
    assert folder_names("2026-07-15_22-13-52_plan_WED/02-observation-m3/narrow/a.fits") == [
        "narrow",
        "02-observation-m3",
    ]
    assert folder_names("a.fits") == []


@pytest.mark.req("LOC-027")
def test_frames_in_calibration_folders_are_calibration_even_when_the_header_says_light(tmp_path: Path) -> None:
    root, data = tmp_path / "astro", tmp_path / "SDP"
    for folder in ("lights", "darks", "biases", "flats"):
        write(root / "raw" / "IC434" / folder / "a.fits", raw_sub(OBJECT="", IMAGETYP="LIGHT"))
    write(root / "raw" / "IC434" / "darks" / "real.fits", raw_sub(IMAGETYP="DARK"))
    index = FrameIndex(LocalSources(data).add("astro", root, 1.65), data)
    index.update()
    kinds = {r["path"]: r["kind"] for r in index.rows()}
    assert kinds == {
        "raw/IC434/biases/a.fits": "bias",
        "raw/IC434/darks/a.fits": "dark",
        "raw/IC434/darks/real.fits": "dark",
        "raw/IC434/flats/a.fits": "flat",
        "raw/IC434/lights/a.fits": "light",
    }


@pytest.mark.req("LOC-027")
def test_an_index_written_before_keeps_no_light_in_a_calibration_folder(tmp_path: Path) -> None:
    root, data = tmp_path / "astro", tmp_path / "SDP"
    write(root / "raw" / "IC434" / "darks" / "a.fits", raw_sub(IMAGETYP="LIGHT"))
    index = FrameIndex(LocalSources(data).add("astro", root, 1.65), data)
    index.update()
    import pyarrow as pa
    import pyarrow.parquet as pq

    old = index.table().set_column(3, "kind", pa.array(["light"]))  # what the index said before #143
    pq.write_table(old, index.path)
    result = index.update()
    assert result.reused == 1 and index.rows()[0]["kind"] == "dark"
    assert index.summary()["targets"] == {}, "no longer counted as an unsolved light"


@pytest.mark.req("LOC-025")
def test_the_names_and_solve_commands_take_folder_centres(tmp_path: Path) -> None:
    solver, _rows, _ = nameless(tmp_path)
    env = {"SDP_DATA": str(solver.data)}
    before = json.loads(runner.invoke(cli.app, ["local", "names", "astro"], env=env).stdout)
    assert before["folders_of_frames_without_object"] == {"raw/Andromeda/lights": 1}
    result = runner.invoke(cli.app, ["local", "names", "astro", "--folder", "raw\\Andromeda=10.68,41.27"], env=env)
    doc = json.loads(result.stdout)
    assert doc["folders"]["raw/Andromeda"]["ra_deg"] == 10.68
    assert "folders_of_frames_without_object" not in doc
    bad = runner.invoke(cli.app, ["local", "names", "astro", "--folder", "raw/Andromeda"], env=env)
    assert bad.exit_code != 0
