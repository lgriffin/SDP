"""T3: solving a local source offline and fast, and showing its frames are intact (#71).

Rough centres from the names file and the headers, each frame started from its solved neighbour, several
workers, and fingerprints of the originals checked by ``sdp local verify``."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
import yaml
from typer.testing import CliRunner

from sdp.adapters.local import solving
from sdp.adapters.local.fingerprints import Fingerprints
from sdp.adapters.local.index import FrameIndex, pointing
from sdp.adapters.local.names import FROM_SIMBAD, FROM_YOU, NameBook, parse_centre
from sdp.adapters.local.solver import Reference, SolverConfig, Unsolved, detect, read_frame, refine, solving_image
from sdp.adapters.local.solving import LocalSolver
from sdp.adapters.local.sources import LocalSource, LocalSourceError, LocalSources
from sdp.cli import app as cli

from .synthetic import M31, bayer_frame
from .test_plate_solver import snapshot, worst_error_arcsec

pytestmark = pytest.mark.tier("T3")
runner = CliRunner()
UUID = "3f2b1c9e-8d7a-4e6f-9b0c-1a2b3c4d5e6f"


@dataclass
class Night:
    root: Path
    data: Path
    source: LocalSource
    index: FrameIndex
    reference: Reference


def night(tmp_path: Path, frames: int = 3, **header: object) -> Night:
    """One folder of subs a few seconds apart: the field turns a little from one to the next."""
    root = tmp_path / "astrophotography"
    reference = None
    for i in range(frames):
        truth = bayer_frame(root / "M31" / "night1" / f"img-{i + 1:04d}.fits", rotation_deg=37.0 + 0.2 * i, seed=7,
                            header=dict(header))  # fmt: skip
        reference = truth.reference
    assert reference is not None
    data = tmp_path / "SDP"
    source = LocalSources(data).add("astro", root, 1.65)
    index = FrameIndex(source, data)
    index.update()
    return Night(root, data, source, index, reference)


# -- rough centres without the network ------------------------------------------------------------------


def test_the_headers_pointing_is_read_as_degrees_or_sexagesimal() -> None:
    assert pointing({"RA": 10.6847, "DEC": 41.2687}) == (10.6847, 41.2687)
    ra, dec = pointing({"OBJCTRA": "00 42 44.3", "OBJCTDEC": "-41 16 09"})
    assert ra == pytest.approx(10.684583, abs=1e-6) and dec == pytest.approx(-41.269167, abs=1e-6)
    assert pointing({"OBJCTRA": "00:42:44.3", "OBJCTDEC": "+41:16:09"})[1] == pytest.approx(41.269167, abs=1e-6)
    assert pointing({"RA": 10.68}) == (None, None), "both or neither"
    assert pointing({"RA": 400.0, "DEC": 41.0}) == (None, None), "off the sky"
    assert pointing({"OBJCTRA": "soon", "OBJCTDEC": "+41"}) == (None, None)


@pytest.mark.req("LOC-006")
def test_an_index_from_before_the_pointing_columns_is_read_again(tmp_path: Path) -> None:
    n = night(tmp_path, frames=1, RA=10.7, DEC=41.3)
    old = n.index.table().drop_columns(["ra_deg", "dec_deg"])
    import pyarrow.parquet as pq

    pq.write_table(old, n.index.path)
    result = n.index.update()
    assert result.read == 1 and n.index.rows()[0]["ra_deg"] == 10.7


@pytest.mark.req("LOC-007")
def test_each_name_is_asked_of_simbad_once_and_later_runs_need_no_network(tmp_path: Path) -> None:
    n = night(tmp_path, frames=1)
    asked: list[str] = []

    def simbad(name: str) -> tuple[float, float] | None:
        asked.append(name)
        return M31

    rows = n.index.rows()
    first = LocalSolver(n.source, n.data, simbad, lambda _c: n.reference)
    assert [o.status for o in first.run(rows, rows)] == ["solved"]
    assert asked == ["M31"]
    book = yaml.safe_load((n.data / "index" / "astro" / "names.yaml").read_text())
    assert book["names"]["M31"]["from"] == FROM_SIMBAD

    # no SIMBAD at all: the names file is enough
    offline = LocalSolver(n.source, n.data, None, lambda _c: n.reference)
    assert [o.status for o in offline.run(offline.select(rows, resolve=True), rows)] == ["solved"]


@pytest.mark.req("LOC-007")
def test_an_unreachable_simbad_refuses_with_how_to_give_the_centre(tmp_path: Path) -> None:
    n = night(tmp_path, frames=1)

    def down(name: str) -> tuple[float, float] | None:
        raise TimeoutError(name)

    rows = n.index.rows()
    (outcome,) = LocalSolver(n.source, n.data, down, lambda _c: n.reference).run(rows, rows)
    assert outcome.status == "refused"
    assert "SIMBAD could not be reached (TimeoutError)" in outcome.reason
    assert "--centre M31=RA,DEC" in outcome.reason


@pytest.mark.req("LOC-007")
def test_a_centre_you_give_is_kept_and_simbad_never_overwrites_it(tmp_path: Path) -> None:
    source = LocalSource("astro", tmp_path)
    book = NameBook(source, tmp_path / "SDP")
    book.set(*parse_centre("M31=10.6847,41.2687"), FROM_YOU)
    book.set("M31", 1.0, 2.0, FROM_SIMBAD)
    assert NameBook(source, tmp_path / "SDP").get("M31") == (10.6847, 41.2687)
    for bad in ("M31", "=1,2", "M31=1", "M31=400,10", "M31=a,b"):
        with pytest.raises(LocalSourceError, match="NAME=RA,DEC"):
            parse_centre(bad)


@pytest.mark.req("LOC-006")
def test_a_frame_with_no_usable_name_solves_from_its_headers_pointing(tmp_path: Path) -> None:
    # the app wrote a UUID for the name; the header still says roughly where the telescope pointed
    n = night(tmp_path, frames=1, OBJECT=UUID, RA=11.2, DEC=41.6)
    rows = n.index.rows()
    solver = LocalSolver(n.source, n.data, None, lambda _c: n.reference)
    assert [o.status for o in solver.run(rows, rows)] == ["solved"]
    doc = json.loads(solver.solution_path(rows[0]["path"]).read_text())
    assert doc["rough_centre"] == {"ra_deg": 11.2, "dec_deg": 41.6, "from": "the header's RA and DEC"}


# -- each frame starts from its neighbour --------------------------------------------------------------


def stars_of(path: Path) -> object:
    data, _ = read_frame(path)
    image, binning = solving_image(data, "GBRG")
    return detect(image, SolverConfig(), binning)


@pytest.mark.req("LOC-008")
def test_a_neighbours_solution_refines_to_the_frame_or_is_refused(tmp_path: Path) -> None:
    truth = bayer_frame(tmp_path / "a.fits", rotation_deg=37.6, seed=7)
    near = bayer_frame(tmp_path / "b.fits", rotation_deg=37.0, seed=7).header | {"NAXIS1": 1800, "NAXIS2": 1800}
    stars = stars_of(truth.path)
    solution = refine(stars, truth.reference, near)  # type: ignore[arg-type]
    assert worst_error_arcsec(truth, solution.wcs, 1800, 1800) < 0.5
    # a start a quarter of a turn away matches nothing it should
    far = bayer_frame(tmp_path / "c.fits", rotation_deg=127.0, seed=7).header | {"NAXIS1": 1800, "NAXIS2": 1800}
    with pytest.raises(Unsolved):
        refine(stars, truth.reference, far)  # type: ignore[arg-type]
    with pytest.raises(Unsolved, match="another size"):
        refine(stars, truth.reference, {**near, "NAXIS1": 3536})  # type: ignore[arg-type]


@pytest.mark.req("LOC-008")
def test_a_folder_chains_each_frame_from_its_solved_neighbour_and_falls_back_to_triangles(tmp_path: Path) -> None:
    n = night(tmp_path, frames=3)
    rows = n.index.rows()
    solver = LocalSolver(n.source, n.data, lambda _n: M31, lambda _c: n.reference)
    assert [o.status for o in solver.run(rows, rows)] == ["solved"] * 3
    starts = [json.loads(solver.solution_path(r["path"]).read_text())["start"] for r in rows]
    assert starts[0] == {"method": "triangles"}
    assert starts[1:] == [
        {"method": "chained", "from": "M31/night1/img-0001.fits"},
        {"method": "chained", "from": "M31/night1/img-0002.fits"},
    ]
    # the field turned a long way before this frame: the chained start fails, the triangle search does not
    turned = bayer_frame(n.root / "M31" / "night1" / "img-0004.fits", rotation_deg=127.0, seed=7)
    n.index.update()
    rows = n.index.rows()
    (outcome,) = solver.run(solver.select(rows), rows)
    assert outcome.status == "solved"
    doc = json.loads(solver.solution_path(outcome.path).read_text())
    assert doc["start"]["method"] == "triangles"
    assert doc["start"]["chained_failed"]["from"] == "M31/night1/img-0003.fits"
    assert worst_error_arcsec(turned, doc["wcs"], 1800, 1800) < 0.5


@pytest.mark.req("LOC-009")
def test_several_workers_solve_the_same_frames_as_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(solving, "CHAIN", 2)  # four frames make two runs of the folder, side by side
    n = night(tmp_path, frames=4)
    rows = n.index.rows()
    results = {}
    for workers in (1, 3):
        solver = LocalSolver(n.source, n.data, lambda _n: M31, lambda _c: n.reference)
        outcomes = solver.run(solver.select(rows, resolve=True), rows, workers=workers)
        results[workers] = (
            [(o.path, o.status, o.matched) for o in outcomes],
            [json.loads(solver.solution_path(r["path"]).read_text())["wcs"] for r in rows],
        )
    assert results[1][0] == results[3][0]
    assert [o[1] for o in results[3][0]] == ["solved"] * 4
    for one, three in zip(results[1][1], results[3][1], strict=True):
        assert one.keys() == three.keys()
        assert np.allclose([one[k] for k in ("CRVAL1", "CRVAL2", "CD1_1", "CD2_2")],
                           [three[k] for k in ("CRVAL1", "CRVAL2", "CD1_1", "CD2_2")], rtol=0, atol=1e-8)  # fmt: skip


# -- the originals stay intact -----------------------------------------------------------------------


@pytest.mark.req("LOC-010")
def test_a_solution_records_the_fingerprint_of_the_bytes_it_read(tmp_path: Path) -> None:
    n = night(tmp_path, frames=1)
    rows = n.index.rows()
    solver = LocalSolver(n.source, n.data, lambda _n: M31, lambda _c: n.reference)
    solver.run(rows, rows)
    doc = json.loads(solver.solution_path(rows[0]["path"]).read_text())
    assert doc["file"]["sha256"] == hashlib.sha256((n.root / rows[0]["path"]).read_bytes()).hexdigest()
    assert solver.select(rows) == [], "a fingerprint does not make a solved frame look changed"


def flip_a_byte(path: Path, keep_date: bool) -> None:
    stat = path.stat()
    raw = bytearray(path.read_bytes())
    raw[-100] ^= 0xFF
    path.write_bytes(bytes(raw))
    if keep_date:
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    else:
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))


@pytest.mark.req("LOC-011")
def test_verify_finds_missing_modified_and_corrupted_frames_and_keeps_the_first_fingerprints(tmp_path: Path) -> None:
    n = night(tmp_path, frames=3)
    rows = n.index.rows()
    LocalSolver(n.source, n.data, lambda _n: M31, lambda _c: n.reference).run(rows[:1], rows)
    prints = Fingerprints(n.source, n.data)
    first = prints.verify()
    assert (first.checked, first.hashed, len(first.ok), len(first.new), first.intact) == (3, 3, 1, 2, True)
    assert prints.known()["M31/night1/img-0001.fits"]["by"] == "solve"

    frames = sorted(n.root.rglob("*.fits"))
    flip_a_byte(frames[0], keep_date=True)  # a bit gone bad on the drive
    flip_a_byte(frames[1], keep_date=False)  # an edit
    frames[2].rename(frames[2].with_suffix(".bak"))  # gone
    before = snapshot(n.root)
    report = prints.verify()
    assert report.corrupted == ["M31/night1/img-0001.fits"]
    assert report.modified == ["M31/night1/img-0002.fits"]
    assert report.missing == ["M31/night1/img-0003.fits"] and not report.intact
    assert snapshot(n.root) == before, "verifying only reads"

    quick = prints.verify(quick=True)
    assert quick.hashed == 0 and quick.modified == ["M31/night1/img-0002.fits"]
    assert quick.ok == ["M31/night1/img-0001.fits"], "a quick check cannot see a bit gone bad"

    # a fingerprint is never replaced unless asked
    assert prints.verify().corrupted == ["M31/night1/img-0001.fits"]
    accepted = prints.verify(accept=True)
    assert accepted.accepted == ["M31/night1/img-0001.fits", "M31/night1/img-0002.fits"]
    assert prints.verify().modified == [] and prints.verify().corrupted == []


@pytest.mark.req("LOC-011")
def test_the_verify_and_manifest_commands(tmp_path: Path) -> None:
    n = night(tmp_path, frames=2)
    env = {"SDP_DATA": str(n.data)}
    result = runner.invoke(cli.app, ["local", "verify", "astro"], env=env)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["new"] == 2
    manifest = runner.invoke(cli.app, ["local", "manifest", "astro"], env=env)
    out = Path(json.loads(manifest.stdout)["manifest"])
    lines = list(csv.DictReader(out.open()))
    assert [r["path"] for r in lines] == ["M31/night1/img-0001.fits", "M31/night1/img-0002.fits"]
    assert lines[0]["sha256"] == hashlib.sha256((n.root / lines[0]["path"]).read_bytes()).hexdigest()
    flip_a_byte(n.root / lines[1]["path"], keep_date=True)
    failed = runner.invoke(cli.app, ["local", "verify", "astro"], env=env)
    assert failed.exit_code == 1 and json.loads(failed.stdout)["corrupted_paths"] == [lines[1]["path"]]


@pytest.mark.req("LOC-007")
def test_the_names_command_keeps_centres_you_give(tmp_path: Path) -> None:
    n = night(tmp_path, frames=1, OBJECT="NGC7000")
    env = {"SDP_DATA": str(n.data)}
    result = runner.invoke(cli.app, ["local", "names", "astro"], env=env)
    assert json.loads(result.stdout)["frames_name_these_unknown_targets"] == ["NGC7000"]
    result = runner.invoke(cli.app, ["local", "names", "astro", "--centre", "NGC7000=314.7,44.3"], env=env)
    doc = json.loads(result.stdout)
    assert doc["frames_name_these_unknown_targets"] == [] and doc["known"]["NGC7000"]["from"] == FROM_YOU
