"""T3: SDP's own plate solver on synthetic Vespera-like frames with planted Gaia stars (#71)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from typer.testing import CliRunner

from sdp.adapters.archives.tap import load_sources
from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.solver import (
    Reference,
    SolverConfig,
    Unsolved,
    detect,
    pixel_to_sky,
    read_frame,
    solve,
    solving_image,
)
from sdp.adapters.local.solving import LocalSolver, usable_name
from sdp.adapters.local.sources import LocalSources
from sdp.cli import app as cli
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.investigation import ResolverRecord
from sdp.testing.fakes.archives import FakeArchive
from sdp.testing.investigate import investigate_ports
from sdp.testing.wiring import build_in_memory_kernel

from .synthetic import M31, Truth, bayer_frame, gaia_field

pytestmark = pytest.mark.tier("T3")
runner = CliRunner()


def solve_file(truth: Truth, scale: float = 1.65, bayer: str | None = "GBRG", **config: Any) -> Any:
    data, _ = read_frame(truth.path)
    image, binning = solving_image(data, bayer)
    stars = detect(image, SolverConfig(**config), binning)
    return solve(stars, truth.reference, M31, scale, SolverConfig(**config))


def worst_error_arcsec(truth: Truth, wcs: dict[str, Any], width: int, height: int) -> float:
    """The largest distance between the solution's and the truth's sky position over a pixel grid."""
    xs, ys = np.meshgrid(np.linspace(1, width, 9), np.linspace(1, height, 9))
    ra, dec = pixel_to_sky(wcs, xs.ravel(), ys.ravel())
    true_ra, true_dec = truth.wcs.all_pix2world(xs.ravel(), ys.ravel(), 1)
    return float((np.hypot((ra - true_ra) * np.cos(np.radians(dec)), dec - true_dec) * 3600).max())


@pytest.mark.req("LOC-003")
def test_a_rotated_bayer_frame_far_from_its_object_solves_to_half_an_arcsecond(tmp_path: Path) -> None:
    truth = bayer_frame(tmp_path / "sub.fits", rotation_deg=37.0, centre_offset=(0.45, 0.4))  # 0.6 deg off M31
    solution = solve_file(truth)
    assert worst_error_arcsec(truth, solution.wcs, 1800, 1800) < 0.5
    assert solution.matched >= 12 and solution.rms_arcsec < 1.5
    assert solution.rotation_deg == pytest.approx(37.0, abs=0.05) and not solution.mirrored
    assert solution.scale_arcsec == pytest.approx(1.65, rel=1e-3)
    assert (solution.wcs["NAXIS1"], solution.wcs["CTYPE1"]) == (1800, "RA---TAN")


@pytest.mark.req("LOC-003")
@pytest.mark.parametrize(("rotation", "mirrored", "offset"), [(200.0, False, (0.0, -0.6)), (311.0, True, (0.5, 0.4))])
def test_any_rotation_and_a_mirrored_frame_solve(
    tmp_path: Path, rotation: float, mirrored: bool, offset: tuple[float, float]
) -> None:
    truth = bayer_frame(tmp_path / "sub.fits", rotation_deg=rotation, mirrored=mirrored, centre_offset=offset, seed=5)
    solution = solve_file(truth)
    assert worst_error_arcsec(truth, solution.wcs, 1800, 1800) < 0.5
    assert solution.mirrored is mirrored
    assert solution.rotation_deg == pytest.approx(rotation, abs=0.05)


@pytest.mark.req("LOC-003")
def test_a_stacked_colour_master_solves_from_its_luminance(tmp_path: Path) -> None:
    truth = bayer_frame(tmp_path / "stack.fit", stack=True, width=1200, height=1200, rotation_deg=95.0, seed=11)
    solution = solve_file(truth, bayer=None)
    assert worst_error_arcsec(truth, solution.wcs, 1200, 1200) < 0.5


@pytest.mark.req("LOC-003")
def test_a_frame_with_too_few_stars_is_refused_with_its_reason(tmp_path: Path) -> None:
    truth = bayer_frame(tmp_path / "cloudy.fits", only=8)
    with pytest.raises(Unsolved, match=r"only \d+ stars were found; at least 12 are needed"):
        solve_file(truth)


@pytest.mark.req("LOC-003")
def test_a_wrong_scale_is_refused_not_forced(tmp_path: Path) -> None:
    truth = bayer_frame(tmp_path / "sub.fits")
    with pytest.raises(Unsolved):
        solve_file(truth, scale=2.4)


@pytest.mark.req("LOC-003")
def test_an_rms_over_the_limit_is_refused(tmp_path: Path) -> None:
    truth = bayer_frame(tmp_path / "sub.fits")
    with pytest.raises(Unsolved, match=r"RMS is [\d.]+ arcsec, over the 0\.01 arcsec limit"):
        solve_file(truth, max_rms_arcsec=0.01)


def test_uuid_and_empty_objects_are_not_looked_up() -> None:
    assert usable_name(" M31 ") == "M31"
    assert usable_name("3f2b1c9e-8d7a-4e6f-9b0c-1a2b3c4d5e6f") is None
    assert usable_name("") is None and usable_name(None) is None


# -- a batch over an indexed folder -------------------------------------------------------------------


@dataclass
class Folder:
    root: Path
    data: Path
    reference: Reference


@pytest.fixture
def folder(tmp_path: Path) -> Folder:
    root = tmp_path / "astrophotography"
    truth = bayer_frame(root / "M31" / "night1" / "img-0001.fits")
    bayer_frame(root / "M31" / "night1" / "img-0002.fits", rotation_deg=38.0, seed=7)
    # the app sometimes writes a UUID where the target's name belongs: the folder's other frames lend it
    bayer_frame(
        root / "M31" / "night1" / "img-0003.fits", seed=7, header={"OBJECT": "3f2b1c9e-8d7a-4e6f-9b0c-1a2b3c4d5e6f"}
    )
    bayer_frame(root / "M31" / "night2" / "cloudy.fits", only=6, seed=7)
    bayer_frame(root / "Orphan" / "img-0001.fits", seed=7, header={"OBJECT": ""})
    return Folder(root, tmp_path / "SDP", truth.reference)


def snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {
        str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.mark.req("LOC-004")
def test_a_batch_writes_solutions_and_refusals_under_the_data_folder_only(folder: Folder) -> None:
    source = LocalSources(folder.data).add("astro", folder.root, 1.65)
    index = FrameIndex(source, folder.data)
    index.update()
    before = snapshot(folder.root)
    looked_up: list[str] = []

    def by_name(name: str) -> tuple[float, float] | None:
        looked_up.append(name)
        return M31 if name == "M31" else None

    solver = LocalSolver(source, folder.data, by_name, lambda _c: folder.reference)
    rows = index.rows()
    outcomes = {o.path: o for o in solver.run(solver.select(rows), rows)}
    assert {p: o.status for p, o in outcomes.items()} == {
        "M31/night1/img-0001.fits": "solved",
        "M31/night1/img-0002.fits": "solved",
        "M31/night1/img-0003.fits": "solved",
        "M31/night2/cloudy.fits": "refused",
        "Orphan/img-0001.fits": "refused",
    }
    assert looked_up == ["M31"], "each name is looked up once"
    assert "no rough centre" in outcomes["Orphan/img-0001.fits"].reason
    solution = json.loads((folder.data / "solutions" / "astro" / "M31/night1/img-0003.fits.wcs.json").read_text())
    assert solution["rough_centre"]["from"].startswith("solved neighbour M31/night1/")
    assert solution["gaia"]["artifact_id"] == "sha256:synthetic-gaia" and solution["solver"] == "sdp-solve 2"
    refusal = json.loads((folder.data / "solutions" / "astro" / "M31/night2/cloudy.fits.unsolved.json").read_text())
    assert "stars were found" in refusal["reason"]
    assert snapshot(folder.root) == before, "the frames are never written to"

    # a rerun has nothing to do; --resolve redoes the solved, --retry-refused the refused
    assert solver.select(rows) == []
    assert len(solver.select(rows, resolve=True)) == 5
    assert [r["path"] for r in solver.select(rows, retry_refused=True)] == [
        "M31/night2/cloudy.fits",
        "Orphan/img-0001.fits",
    ]
    assert [r["path"] for r in solver.select(rows, resolve=True, target="m 31", limit=2)] == [
        "M31/night1/img-0001.fits",
        "M31/night1/img-0002.fits",
    ]
    status = solver.status(index)
    assert (status["frames"], status["solved"], status["refused"], status["to_do"]) == (5, 3, 2, 0)


@pytest.mark.req("LOC-004")
def test_a_changed_or_unreadable_frame_loses_its_old_solution(folder: Folder) -> None:
    source = LocalSources(folder.data).add("astro", folder.root, 1.65)
    index = FrameIndex(source, folder.data)
    index.update()
    solver = LocalSolver(source, folder.data, lambda n: M31 if n == "M31" else None, lambda _c: folder.reference)
    first = "M31/night1/img-0001.fits"
    rows = [r for r in index.rows() if r["path"] == first]
    assert [o.status for o in solver.run(rows, index.rows())] == ["solved"]
    # an odd raw axis is binned away for solving, but the solution keeps the frame's own size
    odd = bayer_frame(folder.root / "M31" / "night1" / "img-0009.fits", width=1801, height=1799, seed=7)
    index.update()
    (row,) = [r for r in index.rows() if r["path"] == "M31/night1/img-0009.fits"]
    assert solver.solve_frame(row, index.rows()).status == "solved"
    wcs = json.loads(solver.solution_path(row["path"]).read_text())["wcs"]
    assert (wcs["NAXIS1"], wcs["NAXIS2"]) == (1801, 1799)
    assert worst_error_arcsec(odd, wcs, 1801, 1799) < 0.5
    # the same path with new contents is solved again on an ordinary rerun
    bayer_frame(folder.root / first, rotation_deg=38.0, seed=7)
    index.update()
    assert first in [r["path"] for r in solver.select(index.rows())]
    # a frame that can no longer be read keeps a refusal, never its old solution beside it
    (folder.root / first).write_bytes(b"SIMPLE  =" + b" " * 3000)
    (changed,) = [r for r in index.rows() if r["path"] == first]
    assert solver.solve_frame(changed, index.rows()).status == "refused"
    assert not solver.solution_path(first).exists() and solver.refusal_path(first).exists()


@dataclass(frozen=True)
class NameOnlyResolver:
    resolver_id: str = "names-only"
    release_label: str = "test"

    def by_name(self, name: str) -> ResolverRecord | None:
        return ResolverRecord("M  31", "G", (), *M31) if name == "M31" else None

    def by_position(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> ResolverRecord | None:
        return None


@pytest.mark.req("LOC-004")
def test_the_command_solves_against_gaia_through_the_acquisition_cache(folder: Folder) -> None:
    ra, dec, mag = gaia_field(M31, 1.5, 450.0, 7)  # the field bayer_frame planted, with fainter stars too
    gaia = load_sources()["gaia"].descriptor
    table = pa.table(
        {"source_id": np.arange(ra.size, dtype=np.int64), "ra": ra, "dec": dec, "phot_g_mean_mag": mag},
    )
    archive = FakeArchive(gaia, table)
    kernel = build_in_memory_kernel(
        gateways={"gaia": archive}, investigate=investigate_ports(resolver=NameOnlyResolver())
    )
    cli.use_runtime(lambda: kernel)
    try:
        env = {"SDP_DATA": str(folder.data)}
        for args in (["add", "astro", str(folder.root), "--scale", "1.65"], ["index", "astro"]):
            assert runner.invoke(cli.app, ["local", *args], env=env).exit_code == 0
        result = runner.invoke(cli.app, ["local", "solve", "astro", "--target", "M31", "--gaia-radius", "1.5"], env=env)
        assert result.exit_code == 0, result.output
        run = json.loads(result.stdout)
        assert (run["chosen"], run["solved"], run["refused"]) == (4, 3, 1)
        assert archive.fetch_calls == 1, "one Gaia read serves every frame of the target"
        assert kernel.store.exists(ArtifactId(run["run_artifact"])), "the run is kept as an artifact"
        solved = runner.invoke(cli.app, ["local", "solved", "astro"], env=env)
        assert json.loads(solved.stdout)["solved"] == 3
    finally:
        cli.use_runtime(cli._production_runtime)
