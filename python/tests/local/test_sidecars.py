"""T3: a solved frame's solution as other software reads it: lens distortion, ``.wcs`` header files and
solved copies whose pixels are the original bytes (#71)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.wcs import WCS
from typer.testing import CliRunner

from sdp.adapters.local.distortion import fit_distortion
from sdp.adapters.local.fits_header import header_size, read_header, read_header_file
from sdp.adapters.local.gateway import canonical_image
from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.sidecars import export, solved_copy
from sdp.adapters.local.solver import SolverConfig, detect, pixel_to_sky, read_frame, sip_offsets, solving_image
from sdp.adapters.local.solving import LocalSolver
from sdp.adapters.local.sources import LocalSourceError, LocalSources
from sdp.adapters.local.wcs_file import write_wcs_file
from sdp.cli import app as cli

from .synthetic import M31, Truth, bayer_frame
from .test_plate_solver import snapshot, solve_file, worst_error_arcsec

pytestmark = pytest.mark.tier("T3")
runner = CliRunner()
#: a lens that pushes the corners out by about 1.2 arcsec (radial, third order)
K = 6e-10
BENT = {"A_3_0": K, "A_1_2": K, "B_2_1": K, "B_0_3": K}


def with_distortion(truth: Truth) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    tan = solve_file(truth)
    data, _ = read_frame(truth.path)
    image, binning = solving_image(data, "GBRG")
    stars = detect(image, SolverConfig(), binning)
    solution, info = fit_distortion(tan, stars, truth.reference, SolverConfig())
    return tan.wcs, solution.wcs, info


@pytest.mark.req("LOC-013")
def test_a_bent_field_gets_a_distortion_polynomial_that_follows_it(tmp_path: Path) -> None:
    truth = bayer_frame(tmp_path / "bent.fits", distortion=BENT, per_deg2=900)
    tan, sip, info = with_distortion(truth)
    assert worst_error_arcsec(truth, tan, 1800, 1800) > 1.0, "a TAN projection cannot follow the lens"
    assert info["kept"] is True and info["order"] == 3
    assert info["sip_held_out_rms_arcsec"]["3"] < 0.9 * info["tan_held_out_rms_arcsec"]
    assert sip["CTYPE1"] == "RA---TAN-SIP" and sip["A_ORDER"] == 3
    assert worst_error_arcsec(truth, sip, 1800, 1800) < 0.5


@pytest.mark.req("LOC-013")
def test_a_straight_field_keeps_its_plain_tan_solution(tmp_path: Path) -> None:
    truth = bayer_frame(tmp_path / "straight.fits", per_deg2=900)
    tan, sip, info = with_distortion(truth)
    assert info["kept"] is False and "held-out" in info["reason"]
    assert sip == tan and sip["CTYPE1"] == "RA---TAN"


@pytest.mark.req("LOC-013")
def test_a_frame_with_few_stars_is_not_given_a_polynomial(tmp_path: Path) -> None:
    truth = bayer_frame(tmp_path / "sparse.fits", only=30, distortion=BENT)
    _, sip, info = with_distortion(truth)
    assert info == {"kept": False, "reason": "fewer than 40 matched stars"} and "A_ORDER" not in sip


def solved_night(tmp_path: Path) -> tuple[LocalSolver, Path, Path, list[dict[str, Any]]]:
    root, data = tmp_path / "astrophotography", tmp_path / "SDP"
    bayer_frame(root / "M31" / "night1" / "img-0001.fits", distortion=BENT, per_deg2=900)
    bayer_frame(root / "M31" / "night1" / "img-0002.fits", rotation_deg=37.2, distortion=BENT, per_deg2=900)
    source = LocalSources(data).add("astro", root, 1.65)
    index = FrameIndex(source, data)
    index.update()
    rows = index.rows()
    reference = bayer_frame(tmp_path / "ref.fits", distortion=BENT, per_deg2=900).reference
    solver = LocalSolver(source, data, lambda _n: M31, lambda _c: reference)
    assert [o.status for o in solver.run(rows, rows)] == ["solved", "solved"]
    return solver, root, data, rows


@pytest.mark.req("LOC-012")
@pytest.mark.filterwarnings("ignore:The WCS transformation has more axes")  # a header with no pixels
def test_each_solution_has_a_wcs_file_that_astropy_reads_as_sdp_does(tmp_path: Path) -> None:
    solver, _, _, rows = solved_night(tmp_path)
    for row in rows:
        doc = json.loads(solver.solution_path(row["path"]).read_text())
        header = read_header_file(solver.wcs_path(row["path"]))
        assert (header["IMAGEW"], header["IMAGEH"]) == (1800, 1800)
        assert header["CTYPE1"] == "RA---TAN-SIP" and header["SDPSHA"] == doc["file"]["sha256"]
        assert header["SDPFRAME"] == row["path"] and header["SDPNMAT"] == doc["matched"]
        x, y = np.meshgrid(np.linspace(1, 1800, 7), np.linspace(1, 1800, 7))
        ra, dec = WCS(header).all_pix2world(x.ravel(), y.ravel(), 1)
        ours = pixel_to_sky(doc["wcs"], x.ravel(), y.ravel())
        gap = np.hypot((ra - ours[0]) * np.cos(np.radians(dec)), dec - ours[1]) * 3600
        assert gap.max() < 1e-3
        # the inverse polynomial takes the sky back to the same pixels
        u, v = x.ravel() - doc["wcs"]["CRPIX1"], y.ravel() - doc["wcs"]["CRPIX2"]
        f, g = sip_offsets(doc["wcs"], u, v)
        fp, gp = sip_offsets(doc["wcs"], u + f, v + g, "AP")
        assert np.abs(u + f + fp - u).max() < 0.05 and np.abs(v + g + gp - v).max() < 0.05


@pytest.mark.req("LOC-012")
def test_the_sidecars_command_writes_missing_wcs_files_and_a_refusal_removes_one(tmp_path: Path) -> None:
    solver, root, data, rows = solved_night(tmp_path)
    first = str(rows[0]["path"])
    solver.wcs_path(first).unlink()
    result = runner.invoke(cli.app, ["local", "sidecars", "astro"], env={"SDP_DATA": str(data)})
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["written"] == 1 and solver.wcs_path(first).exists()
    (root / first).write_bytes(b"SIMPLE  =" + b" " * 3000)
    FrameIndex(solver.source, data).update()
    (row,) = [r for r in FrameIndex(solver.source, data).rows() if r["path"] == first]
    assert solver.solve_frame(row, rows).status == "refused"
    assert not solver.wcs_path(first).exists()


@pytest.mark.req("LOC-014")
def test_a_solved_copy_has_the_wcs_in_its_header_and_the_original_pixel_bytes(tmp_path: Path) -> None:
    solver, root, data, rows = solved_night(tmp_path)
    before = snapshot(root)
    env = {"SDP_DATA": str(data)}
    result = runner.invoke(cli.app, ["local", "export", "astro", "--kind", "light"], env=env)
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["written"] == 2 and out["to"] == str(data / "exports" / "astro")
    for row in rows:
        original = (root / str(row["path"])).read_bytes()
        copy = (data / "exports" / "astro" / str(row["path"])).read_bytes()
        assert copy[header_size(copy) :] == original[header_size(original) :], "pixel bytes copied unchanged"
        header = read_header(io.BytesIO(copy))
        assert header["CTYPE1"] == "RA---TAN-SIP" and header["BZERO"] == 32768 and header["BAYERPAT"] == "GBRG"
        assert header["SDPSHA"] == json.loads(solver.solution_path(str(row["path"])).read_text())["file"]["sha256"]
    assert snapshot(root) == before, "the source is never written to"
    again = runner.invoke(cli.app, ["local", "export", "astro"], env=env)
    assert json.loads(again.stdout)["written"] == 0, "copies already there are kept"


@pytest.mark.req("LOC-014")
def test_an_export_into_the_source_or_of_changed_bytes_is_refused(tmp_path: Path) -> None:
    solver, root, _, rows = solved_night(tmp_path)
    with pytest.raises(LocalSourceError, match="may not hold each other"):
        export(solver.source, solver, rows, root / "solved")
    # same size and date, other bytes: not the frame that was solved
    path = root / str(rows[0]["path"])
    stat = path.stat()
    raw = bytearray(path.read_bytes())
    raw[-50] ^= 0xFF
    path.write_bytes(bytes(raw))

    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    result = export(solver.source, solver, rows, tmp_path / "out")
    assert result.skipped == [rows[0]["path"]] and result.written == [rows[1]["path"]]


def test_a_copy_drops_the_frames_old_wcs(tmp_path: Path) -> None:
    truth = bayer_frame(tmp_path / "old.fits", header={"CTYPE1": "RA---CAR", "CDELT1": 1.0, "PC1_1": 1.0})
    doc = {"wcs": {**truth.header, "NAXIS1": 1800, "NAXIS2": 1800}, "frame": "old.fits", "matched": 20}
    header = read_header(io.BytesIO(solved_copy(truth.path.read_bytes(), doc)))
    assert header["CTYPE1"] == "RA---TAN" and "CDELT1" not in header and "PC1_1" not in header


@pytest.mark.req("LOC-012")
def test_a_frame_in_a_folder_with_an_accented_name_still_gets_its_wcs_file(tmp_path: Path) -> None:
    truth = bayer_frame(tmp_path / "f.fits")
    doc = {"wcs": {**truth.header, "NAXIS1": 1800, "NAXIS2": 1800}, "frame": "Andromède/img-0001.fits"}
    header = read_header_file(write_wcs_file(tmp_path / "f.wcs", doc))
    assert header["SDPFRAME"] == "Androm\\xe8de/img-0001.fits"


@pytest.mark.req("LOC-014")
def test_a_copy_drops_a_checksum_its_new_header_would_break(tmp_path: Path) -> None:
    truth = bayer_frame(tmp_path / "sum.fits", header={"CHECKSUM": "0123456789ABCDEF", "DATASUM": "12345"})
    doc = {"wcs": {**truth.header, "NAXIS1": 1800, "NAXIS2": 1800}, "frame": "sum.fits"}
    header = read_header(io.BytesIO(solved_copy(truth.path.read_bytes(), doc)))
    assert "CHECKSUM" not in header and "DATASUM" not in header


@pytest.mark.req("LOC-014")
def test_a_solution_with_no_fingerprint_is_not_exported(tmp_path: Path) -> None:
    solver, _, _, rows = solved_night(tmp_path)
    path = solver.solution_path(rows[0]["path"])
    doc = json.loads(path.read_text())
    del doc["file"]["sha256"]
    path.write_text(json.dumps(doc))
    result = export(solver.source, solver, rows, tmp_path / "out")
    assert result.skipped == [rows[0]["path"]] and result.written == [rows[1]["path"]]


@pytest.mark.req("LOC-005")
def test_a_binned_image_keeps_the_frames_distortion(tmp_path: Path) -> None:
    solver, root, _, rows = solved_night(tmp_path)
    doc = json.loads(solver.solution_path(str(rows[0]["path"])).read_text())
    data, header = read_frame(root / str(rows[0]["path"]))
    binned = read_header(io.BytesIO(canonical_image(data, "GBRG", doc["wcs"], header)))
    assert binned["CTYPE1"] == "RA---TAN-SIP" and binned["SDPBIN"] == 2
    # binned pixel p' covers raw pixels 2p'-1 and 2p'; its centre is raw 2p' - 0.5
    xb, yb = np.meshgrid(np.linspace(1, 900, 6), np.linspace(1, 900, 6))
    ra, dec = WCS(binned).all_pix2world(xb.ravel(), yb.ravel(), 1)
    raw_ra, raw_dec = pixel_to_sky(doc["wcs"], 2 * xb.ravel() - 0.5, 2 * yb.ravel() - 0.5)
    assert (np.hypot((ra - raw_ra) * np.cos(np.radians(dec)), dec - raw_dec) * 3600).max() < 1e-3
