"""T3: how good each solved local frame is, and its grade against the rest of its session (#71)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sdp.adapters.local.gateway import LocalImageGateway
from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.quality import GOOD, POOR, USABLE, Grades, grade
from sdp.adapters.local.solving import LocalSolver
from sdp.adapters.local.sources import LocalSources
from sdp.cli import app as cli

from .synthetic import M31, bayer_frame

pytestmark = pytest.mark.tier("T3")
runner = CliRunner()


def solved(tmp_path: Path, frames: dict[str, dict[str, Any]]) -> tuple[LocalSolver, list[dict[str, Any]]]:
    root, data = tmp_path / "astrophotography", tmp_path / "SDP"
    references = [
        bayer_frame(root / "M31" / "night1" / name, seed=7, per_deg2=900, **extra).reference
        for name, extra in frames.items()
    ]
    reference = references[-1]
    source = LocalSources(data).add("astro", root, 1.65)
    index = FrameIndex(source, data)
    index.update()
    rows = index.rows()
    solver = LocalSolver(source, data, lambda _n: M31, lambda _c: reference)
    solver.run(rows, rows)
    return solver, rows


def quality_of(solver: LocalSolver, path: str) -> dict[str, Any]:
    return json.loads(solver.solution_path(path).read_text())["quality"]  # type: ignore[no-any-return]


@pytest.mark.req("LOC-015")
def test_a_solved_frame_records_its_sharpness_sky_transparency_and_depth(tmp_path: Path) -> None:
    solver, rows = solved(tmp_path, {"img-0001.fits": {}})
    q = quality_of(solver, rows[0]["path"])
    # planted stars are Gaussians of sigma 1.7 raw pixels: FWHM 4.0 px, 6.6 arcsec
    assert q["fwhm_arcsec"] == pytest.approx(6.6, rel=0.15)
    assert q["ellipticity"] < 0.1
    assert q["background"] == pytest.approx(600 * 0.75, rel=0.05), "the mean of the four colour cells' sky"
    assert q["matched"] >= 40 and q["detected"] >= q["matched"]
    assert q["trails"] == 0
    assert 10 < q["zero_point"] < 30 and q["limiting_mag"] > 14.0


@pytest.mark.req("LOC-015")
def test_cloud_wind_and_a_satellite_show_in_the_measures(tmp_path: Path) -> None:
    solver, rows = solved(
        tmp_path,
        {
            "img-0001.fits": {},
            "img-0002.fits": {"flux_scale": 0.5},
            "img-0003.fits": {"sigma_px": (2.6, 1.4)},
            "img-0004.fits": {"trail": True},
        },
    )
    clear, cloudy, windy, trail = (quality_of(solver, r["path"]) for r in rows)
    assert clear["zero_point"] - cloudy["zero_point"] == pytest.approx(0.75, abs=0.1), "half the light"
    assert windy["ellipticity"] > clear["ellipticity"] + 0.2
    assert trail["trails"] == 1 and clear["trails"] == 0


def session(**changes: dict[str, Any]) -> list[dict[str, Any]]:
    typical: dict[str, Any] = {
        "fwhm_arcsec": 4.0,
        "ellipticity": 0.05,
        "zero_point": 20.0,
        "background": 300.0,
        "matched": 100,
    }
    frames: list[dict[str, Any]] = [
        {"path": f"M31/n1/img-{i}.fits", "kind": "light", "measures": dict(typical)} for i in range(6)
    ]
    for i, (name, change) in enumerate(changes.items()):
        frames[i]["path"] = f"M31/n1/{name}.fits"
        frames[i]["measures"] |= change
    return frames


@pytest.mark.req("LOC-016")
def test_a_frame_is_graded_against_its_own_session_with_its_reasons() -> None:
    frames = session(
        blurred={"fwhm_arcsec": 6.5},
        soft={"fwhm_arcsec": 5.2},
        cloudy={"zero_point": 19.3},
        moonlit={"background": 700.0},
        trail={"trails": 1},
    )
    frames.append({"path": "M31/n1/refused.fits", "kind": "light", "measures": {}, "refused": "only 3 stars"})
    graded = {g["path"].split("/")[-1]: g for g in grade(frames)}
    assert graded["blurred.fits"]["grade"] == POOR and "wider" in graded["blurred.fits"]["reasons"][0]
    assert graded["soft.fits"]["grade"] == USABLE
    assert graded["cloudy.fits"]["grade"] == POOR and "cloud" in graded["cloudy.fits"]["reasons"][0]
    assert graded["moonlit.fits"]["grade"] == POOR and "brighter" in graded["moonlit.fits"]["reasons"][0]
    assert graded["trail.fits"]["grade"] == USABLE and "trail" in graded["trail.fits"]["reasons"][0]
    assert graded["img-5.fits"] == {**graded["img-5.fits"], "grade": GOOD, "reasons": []}
    assert graded["refused.fits"]["grade"] == POOR and graded["refused.fits"]["reasons"] == ["not solved: only 3 stars"]


@pytest.mark.req("LOC-016")
def test_a_small_session_is_not_compared() -> None:
    frames = session(blurred={"fwhm_arcsec": 9.0})[:3]
    assert {g["grade"] for g in grade(frames)} == {GOOD}
    assert grade(frames)[0]["reasons"] == ["only 3 solved frame(s) in its session, so not compared"]


@pytest.mark.req("LOC-016")
def test_the_quality_command_grades_every_session_and_summarises_each_night(tmp_path: Path) -> None:
    frames: dict[str, dict[str, Any]] = {f"img-{i:04d}.fits": {} for i in range(1, 6)}
    frames["img-0006.fits"] = {"sigma_px": (3.4, 3.4)}
    solver, _ = solved(tmp_path, frames)
    env = {"SDP_DATA": str(solver.data)}
    result = runner.invoke(cli.app, ["local", "quality", "astro"], env=env)
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["frames"] == 6 and out["by_grade"] == {"good": 5, "poor": 1}
    (night,) = out["nights"]
    assert night["night"] == "2025-09-14" and night["target"] == "M31" and night["good"] == 5
    grades = Grades(solver.source, solver.data).by_path()
    assert grades["M31/night1/img-0006.fits"]["grade"] == POOR


@pytest.mark.req("LOC-017")
def test_the_image_gateway_leaves_out_poor_frames(tmp_path: Path) -> None:
    frames: dict[str, dict[str, Any]] = {f"img-{i:04d}.fits": {} for i in range(1, 6)}
    frames["img-0006.fits"] = {"sigma_px": (3.4, 3.4)}
    solver, _ = solved(tmp_path, frames)
    gateway = LocalImageGateway(solver.source, solver.data)
    assert len(gateway.frames()) == 6, "no grades yet: every solved frame"
    runner.invoke(cli.app, ["local", "quality", "astro"], env={"SDP_DATA": str(solver.data)})
    offered = [f.row["path"] for f in LocalImageGateway(solver.source, solver.data).frames()]
    assert "M31/night1/img-0006.fits" not in offered and len(offered) == 5
    assert len(LocalImageGateway(solver.source, solver.data, include_poor=True).frames()) == 6
