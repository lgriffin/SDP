"""T3/T4: a local source's catalogue, the sky search over it, and the planner seeing the frames (#71)."""

from __future__ import annotations

import csv
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sdp.adapters.local.catalogue import (
    NOT_SOLVED,
    REFUSED,
    SOLVED,
    TO_DO,
    Catalogue,
    LocalDiscovery,
    coverage,
    descriptor,
    distance_to_footprint,
    find,
)
from sdp.adapters.local.gateway import LocalFrameDialect
from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.names import FROM_YOU, NameBook
from sdp.adapters.local.solving import LocalSolver
from sdp.adapters.local.sources import LocalSources
from sdp.cli import app as cli
from sdp.kernel.application.planning import DEFAULT_BUDGET
from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.integration import MatchStatus, Operation
from sdp.kernel.domain.investigation import Field
from sdp.kernel.ports.integration import PlanInvestigationCommand
from sdp.testing.investigate import gaia_archive, investigate_ports, planning_descriptors, planning_discovery
from sdp.testing.wiring import build_in_memory_kernel
from sdp_investigate.imaging import ImageCounterpart

from .synthetic import M31, bayer_frame, local_world
from .test_local_gateway import gateway, my_frames_profile

pytestmark = pytest.mark.tier("T3")
runner = CliRunner()


def night(tmp_path: Path) -> tuple[LocalSolver, Path]:
    """Two solved subs on two nights, one refused, one not solved yet, a dark and a stack of NGC7000."""
    root, data = tmp_path / "astrophotography", tmp_path / "SDP"
    first = bayer_frame(root / "M31" / "night1" / "img-0001.fits", seed=7)
    bayer_frame(root / "M31" / "night2" / "img-0001.fits", seed=7, rotation_deg=40.0,
                header={"DATE-OBS": "2025-10-02T21:30:00"})  # fmt: skip
    bayer_frame(root / "M31" / "night2" / "cloudy.fits", only=6, seed=7, header={"DATE-OBS": "2025-10-02T21:40:00"})
    from .fits_files import write

    write(root / "M31" / "darks" / "dark-001.fits", {
        "SIMPLE": True, "BITPIX": 16, "NAXIS": 2, "NAXIS1": 8, "NAXIS2": 8, "IMAGETYP": "Dark",
    }, bytes(128))  # fmt: skip
    source = LocalSources(data).add("astro", root, 1.65)
    index = FrameIndex(source, data)
    index.update()
    rows = index.rows()
    solver = LocalSolver(source, data, lambda _n: M31, lambda _c: first.reference)
    solver.run(solver.select(rows), rows)
    bayer_frame(root / "M31" / "night2" / "later.fits", seed=7, header={"DATE-OBS": "2025-10-02T23:00:00"})
    index.update()
    NameBook(source, data).set("M31", *M31, FROM_YOU)
    return solver, data


@pytest.mark.req("LOC-018")
def test_the_catalogue_has_every_frame_with_its_footprint_status_and_night(tmp_path: Path) -> None:
    solver, data = night(tmp_path)
    rows = {r["path"]: r for r in Catalogue(solver.source, data).build()}
    assert {p: r["status"] for p, r in rows.items()} == {
        "M31/darks/dark-001.fits": NOT_SOLVED,
        "M31/night1/img-0001.fits": SOLVED,
        "M31/night2/cloudy.fits": REFUSED,
        "M31/night2/img-0001.fits": SOLVED,
        "M31/night2/later.fits": TO_DO,
    }
    sub = rows["M31/night1/img-0001.fits"]
    assert sub["night"] == "2025-09-14" and sub["target"] == "M31" and sub["mjd"] == pytest.approx(60932.918, abs=1e-3)
    # 1800 pixels of 1.65 arcsec: a 0.825 degree square, so half its diagonal is 0.583 degrees
    assert sub["radius_deg"] == pytest.approx(0.583, abs=0.005) and len(sub["corner_ra"]) == 4
    assert sub["sha256"] and sub["fwhm_arcsec"] is not None
    assert "stars were found" in rows["M31/night2/cloudy.fits"]["reason"]
    assert Catalogue(solver.source, data).path.exists()


def test_a_place_inside_a_footprint_is_at_distance_zero() -> None:
    row = {"ra_deg": 10.0, "dec_deg": 0.0, "corner_ra": [9.5, 10.5, 10.5, 9.5], "corner_dec": [-0.5, -0.5, 0.5, 0.5]}
    assert distance_to_footprint(row, 10.2, 0.1) == 0.0
    assert distance_to_footprint(row, 11.0, 0.0) == pytest.approx(0.5, abs=1e-3)
    assert distance_to_footprint(row, 11.0, 1.0) == pytest.approx(math.hypot(0.5, 0.5), abs=2e-3)
    assert distance_to_footprint(row, 190.0, 0.0) == math.inf


@pytest.mark.req("LOC-019")
def test_find_lists_the_solved_frames_over_a_place_by_kind_grade_and_night(tmp_path: Path) -> None:
    solver, data = night(tmp_path)
    rows = Catalogue(solver.source, data).build()
    centre = (rows[1]["ra_deg"], rows[1]["dec_deg"])
    assert [r["path"] for r in find(rows, *centre)] == ["M31/night1/img-0001.fits", "M31/night2/img-0001.fits"]
    assert find(rows, centre[0] + 3.0, centre[1]) == []
    near = (centre[0] + 0.9 / math.cos(math.radians(centre[1])), centre[1])
    assert find(rows, *near) == [] and len(find(rows, *near, radius_deg=0.6)) == 2
    assert [r["night"] for r in find(rows, *centre, since=date(2025, 10, 1))] == ["2025-10-02"]
    assert find(rows, *centre, until=date(2025, 9, 30))[0]["night"] == "2025-09-14"
    assert find(rows, *centre, kind="stack") == []
    poor = [{**r, "grade": "poor"} if r["path"].startswith("M31/night1") else r for r in rows]
    assert [r["path"] for r in find(poor, *centre, grade="usable")] == ["M31/night2/img-0001.fits"]


@pytest.mark.req("LOC-019")
def test_the_find_and_coverage_commands(tmp_path: Path) -> None:
    _, data = night(tmp_path)
    env = {"SDP_DATA": str(data)}
    out = tmp_path / "m31.csv"
    result = runner.invoke(
        cli.app, ["local", "find", "astro", "--at", "M31", "--radius", "1", "--csv", str(out)], env=env
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert (doc["frames"], doc["nights"], doc["hours"]) == (2, 2, round(20 / 3600, 2))
    assert [r["path"] for r in csv.DictReader(out.open())] == ["M31/night1/img-0001.fits", "M31/night2/img-0001.fits"]
    unknown = runner.invoke(cli.app, ["local", "find", "astro", "--at", "NGC7000"], env=env)
    assert unknown.exit_code != 0 and "names file" in str(unknown.exception)
    (m31,) = json.loads(runner.invoke(cli.app, ["local", "coverage", "astro"], env=env).stdout)["targets"]
    assert (m31["target"], m31["nights"], m31["lights"], m31["solved"], m31["refused"], m31["to_do"]) == (
        "M31", 2, 4, 2, 1, 1
    )  # fmt: skip
    assert (m31["first_night"], m31["last_night"]) == ("2025-09-14", "2025-10-02")


@pytest.mark.req("LOC-020")
def test_the_solved_frames_are_described_like_any_archive(tmp_path: Path) -> None:
    solver, data = night(tmp_path)
    rows = Catalogue(solver.source, data).build()
    d = descriptor(solver.source, rows, date(2026, 10, 30))
    assert d.source_id == "local:astro" and d.missions == ("light",) and d.calibration_levels == {1}
    assert Operation.CUTOUT in d.operations and d.cutout_support == {"image": "per-product"}
    centre = (rows[1]["ra_deg"], rows[1]["dec_deg"])
    assert d.coverage.covers_position(*centre) and not d.coverage.covers_position(centre[0] + 5, centre[1])
    assert d.coverage.t_min_mjd == pytest.approx(60932.918, abs=1e-3) and (d.coverage.t_max_mjd or 0) > 60950


@pytest.mark.tier("T4")
@pytest.mark.req("LOC-020")
def test_the_planner_plans_cutouts_of_the_frames_over_a_field(tmp_path: Path) -> None:
    world = local_world(tmp_path)
    g = gateway(world)
    ports = investigate_ports(ImageCounterpart(), catalogue=world.catalogue, resolve=False)
    ports.observation_gateways = {"local:astro": g}
    ports.product_dialects = {"local:astro": LocalFrameDialect(g)}
    discovery = LocalDiscovery(
        planning_discovery(planning_descriptors()[0]), {"local:astro": (world.source, world.data)}
    )
    k = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(world.catalogue)}, investigate=ports, discovery=discovery
    )
    assert k.kernel.plan_investigation is not None
    profile = my_frames_profile()

    def plan(centre: tuple[float, float]) -> Any:
        field_ = Field(Cone(*centre, 0.12), epoch_jyear=2016.0, label="frames")
        return k.kernel.plan_investigation.execute(PlanInvestigationCommand(field_, profile, DEFAULT_BUDGET))  # type: ignore[union-attr]

    here = plan(world.centre)
    local = [i for i in here.items if i.source_id == "local:astro"]
    assert [i.operation for i in local] == [Operation.CUTOUT]
    assert local[0].target.startswith("M31/M31_stacked.fits")
    assert here.gaps == ()
    # uncertified frames are planned as exploratory evidence: every field passes but the adapter's admission
    image_match = next(m for m in here.matches if m.source_id == "local:astro" and m.status is MatchStatus.PARTIAL)
    open_fields = [r.field for r in image_match.reasons if r.outcome is not MatchStatus.SATISFIED]
    assert open_fields == ["adapter.conformance"]
    # a field none of the frames reaches: the local source does not cover it
    away = plan((world.centre[0] + 20.0, world.centre[1]))
    assert all(m.status is not MatchStatus.SATISFIED for m in away.matches if m.source_id == "local:astro")
    assert not [i for i in away.items if i.source_id == "local:astro"]


def test_coverage_counts_only_light_and_stack_frames() -> None:
    rows: list[dict[str, Any]] = [
        {"kind": "dark", "target": "M31", "status": NOT_SOLVED, "night": None, "exposure_s": 10.0, "grade": None},
        {"kind": "stack", "target": "M31", "status": SOLVED, "night": "2025-09-14", "exposure_s": 3600.0,
         "grade": "good"},
    ]  # fmt: skip
    (m31,) = coverage(rows)
    assert (m31["stacks"], m31["lights"], m31["hours"], m31["good_share"]) == (1, 0, 0.0, 1.0)


def test_a_folder_spread_over_the_sky_gets_a_cone_per_frame() -> None:
    from sdp.adapters.local.catalogue import sessions

    rows = [
        {"status": SOLVED, "path": f"mosaic/img-{i}.fits", "ra_deg": 10.0 + 15 * i, "dec_deg": 20.0, "radius_deg": 0.6}
        for i in range(3)
    ]
    assert [round(c.ra_deg) for c in sessions(rows)] == [10, 25, 40]
    assert len(sessions(rows[:1])) == 1


@pytest.mark.req("LOC-020")
def test_the_planner_is_told_only_of_frames_the_gateway_offers(tmp_path: Path) -> None:
    solver, data = night(tmp_path)
    rows = Catalogue(solver.source, data).build()
    poor = [{**r, "grade": "poor"} for r in rows]
    assert descriptor(solver.source, poor, date(2026, 10, 30)).product_types == frozenset()
    undated = [{**r, "mjd": None} for r in rows]
    assert descriptor(solver.source, undated, date(2026, 10, 30)).product_types == frozenset()


def test_a_changed_frame_loses_its_old_grade(tmp_path: Path) -> None:
    from sdp.adapters.local.quality import Grades, grade_source

    solver, data = night(tmp_path)
    grade_source(solver.source, data, FrameIndex(solver.source, data).rows())
    assert Grades(solver.source, data).by_path()
    path = solver.source.path / "M31" / "night1" / "img-0001.fits"
    path.write_bytes(path.read_bytes() + bytes(2880))
    FrameIndex(solver.source, data).update()
    row = next(r for r in Catalogue(solver.source, data).build() if r["path"] == "M31/night1/img-0001.fits")
    assert (row["status"], row["grade"]) == (TO_DO, None)


@pytest.mark.parametrize(
    "args",
    [["--at", "999,10"], ["--at", "nan,nan"], ["--at", "M31", "--kind", "lights"], ["--at", "M31", "--radius", "-1"]],
)
def test_find_refuses_a_place_kind_or_radius_that_cannot_be(tmp_path: Path, args: list[str]) -> None:
    _, data = night(tmp_path)
    result = runner.invoke(cli.app, ["local", "find", "astro", *args], env={"SDP_DATA": str(data)})
    assert result.exit_code == 2, result.output
