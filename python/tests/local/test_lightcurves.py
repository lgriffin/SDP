"""T3/T4: light curves of the Gaia stars on a target's own subs, and the variability profile reading them (#71)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest
import yaml
from typer.testing import CliRunner

from sdp.adapters.local.catalogue import Catalogue, LocalDiscovery, descriptor
from sdp.adapters.local.gateway import CURVE_PREFIX, LocalFrameDialect, LocalImageGateway
from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.lightcurves import (
    Field,
    LightCurves,
    _saturation,
    bayer_pattern,
    busiest_target,
    field_cone,
    file_stem,
    green,
    measure_frame,
)
from sdp.adapters.local.solver import sky_to_pixel
from sdp.adapters.local.sources import LocalSource, LocalSources
from sdp.cli import app as cli
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.integration import ProductType
from sdp.kernel.domain.investigation import Field as InvestigationField
from sdp.kernel.domain.investigation import (
    ObservationCriteria,
    Stage,
    StageState,
    TestProfile,
)
from sdp.kernel.ports.investigate import InvestigateFieldCommand, ObservationTarget
from sdp.kernel.science.products import parse_lightcurve
from sdp.testing.investigate import CANONICAL_COLUMNS, gaia_archive, investigate_ports
from sdp.testing.wiring import build_in_memory_kernel
from sdp_investigate.photometry import Variability

from .synthetic import M31, bayer_frame, gaia_field, write_solution

pytestmark = pytest.mark.tier("T3")
runner = CliRunner()
PROFILE = Path(__file__).resolve().parents[3] / "profiles" / "my-frames-variability.yaml"
SIZE, PER_DEG2, SEED = 900, 900.0, 7
NIGHTS, PER_NIGHT = 3, 8
AMPLITUDE = 0.2  # magnitudes: the variable's half range


@dataclass(frozen=True)
class Nights:
    source: LocalSource
    data: Path
    field: Field
    variable: int
    catalogue: pa.Table


def _stars_on_frame(truth: Any) -> tuple[np.ndarray, ...]:
    ra, dec, mag = gaia_field(M31, 1.5, PER_DEG2, SEED)
    x, y = truth.wcs.all_world2pix(ra, dec, 1)
    on = (x > 40) & (x < SIZE - 40) & (y > 40) & (y < SIZE - 40) & (mag < 15.0) & (mag > 9.0)
    return np.flatnonzero(on), ra, dec, mag, x, y


def nights(tmp_path: Path) -> Nights:
    """24 subs of M31 over three nights through passing haze, with one star varying by 0.4 mag."""
    root, data = tmp_path / "astrophotography", tmp_path / "SDP"
    common: dict[str, Any] = {"width": SIZE, "height": SIZE, "per_deg2": PER_DEG2, "seed": SEED}
    first = bayer_frame(root / "M31" / "probe.fits", **common)
    (root / "M31" / "probe.fits").unlink()
    index, ra, dec, mag, x, y = _stars_on_frame(first)
    # the variable: an isolated G 11 to 12.5 star near the middle
    middle = np.hypot(x[index] - SIZE / 2, y[index] - SIZE / 2) < SIZE / 3
    isolated = [
        i for i in index[middle & (mag[index] > 11.0) & (mag[index] < 12.5)]
        if np.sort(np.hypot(x - x[i], y - y[i]))[1] > 20
    ]  # fmt: skip
    variable = int(isolated[0])
    rng = np.random.default_rng(3)
    truths = []
    for n in range(NIGHTS):
        for k in range(PER_NIGHT):
            mjd_hours = 21.0 + k * 0.25
            day = 14 + 3 * n
            stamp = f"2025-09-{day:02d}T{int(mjd_hours):02d}:{int(mjd_hours % 1 * 60):02d}:00"
            phase = 2 * math.pi * (n * PER_NIGHT + k) / 7.3
            relative = f"M31/night{n + 1}/img-{k:04d}.fits"
            truth = bayer_frame(
                root / relative,
                flux_scale=float(rng.uniform(0.7, 1.0)),  # haze: every star dims together
                noise_seed=100 + n * PER_NIGHT + k,
                dimmed={variable: AMPLITUDE * math.sin(phase)},
                header={"DATE-OBS": stamp},
                **common,
            )
            truths.append((relative, truth))
    source = LocalSources(data).add("astro", root, 1.65)
    FrameIndex(source, data).update()
    for relative, truth in truths:
        write_solution(data, source, relative, truth, SIZE, SIZE)
    colour = np.random.default_rng(5).uniform(0.4, 1.6, ra.size)
    table = pa.table({
        "source_id": np.arange(ra.size, dtype=np.int64)[index], "ra": ra[index], "dec": dec[index],
        "pmra": np.zeros(index.size), "pmdec": np.zeros(index.size), "phot_g_mean_mag": mag[index],
        "bp_rp": colour[index],
    })  # fmt: skip
    n = index.size
    catalogue = pa.table({
        "source_key": [f"gaia:DR3:{i}" for i in index], "ra_deg": ra[index], "dec_deg": dec[index],
        "epoch_jyear": [2016.0] * n, "parallax_mas": [1.0] * n, "parallax_mas_err": [0.05] * n,
        "pm_ra_cosdec_masyr": [0.0] * n, "pm_dec_masyr": [0.0] * n, "g_mag": mag[index], "bp_rp": colour[index],
    })  # fmt: skip
    return Nights(source, data, Field.from_table(table), variable, catalogue)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Nights, dict[str, Any]]:
    w = nights(tmp_path_factory.mktemp("lightcurves"))
    rows = Catalogue(w.source, w.data).build()
    return w, LightCurves(w.source, w.data, "M31").build(rows, w.field)


def test_the_green_cells_of_a_gbrg_frame_are_the_mean_of_its_two_greens() -> None:
    raw = np.array([[1.0, 2.0, 5.0, 6.0], [3.0, 4.0, 7.0, 8.0]])  # G B / R G, twice
    assert green(raw, "GBRG").tolist() == [[2.5, 6.5]]
    with pytest.raises(ValueError, match="two greens"):
        green(raw, "RGB")


def test_a_saturated_green_is_not_hidden_by_its_dimmer_partner() -> None:
    data = np.random.default_rng(1).normal(1000.0, 5.0, (120, 120)).astype(np.float32)
    data[60:64, 60:64] = 20000.0
    data[60, 61] = 65000.0  # one green of the RGGB cell at raw (61, 61) clipped; its partner is not
    header = {"BITPIX": 16, "BZERO": 32768}
    flux, _ = measure_frame(data, header, "RGGB", np.array([61.5, 30.5]), np.array([61.5, 30.5]), 3.0)
    assert math.isnan(flux[0]) and np.isfinite(flux[1])


def test_saturation_follows_the_integer_type_and_the_bayer_offsets_shift_the_pattern() -> None:
    assert _saturation({"BITPIX": 16, "BZERO": 32768}) == 65535.0
    assert _saturation({"BITPIX": 16}) == 32767.0
    assert _saturation({"BITPIX": 8}) == 255.0
    assert _saturation({"BITPIX": -32}) == math.inf
    assert _saturation({"BITPIX": 16, "SATURATE": 4095}) == 4095.0
    assert bayer_pattern({"BAYERPAT": "RGGB"}) == "RGGB"
    assert bayer_pattern({"BAYERPAT": "RGGB", "XBAYROFF": 1}) == "GRBG"
    assert bayer_pattern({"BAYERPAT": "RGGB", "YBAYROFF": 1}) == "GBRG"
    assert bayer_pattern({"BAYERPAT": "RGGB", "XBAYROFF": 1, "YBAYROFF": 1}) == "BGGR"
    assert bayer_pattern({}) is None


def test_a_target_name_becomes_a_safe_file_name(tmp_path: Path) -> None:
    assert file_stem("NGC 7000/IC 5070") == "NGC 7000_IC 5070"
    assert file_stem("Sh2-155: Cave") == "Sh2-155_ Cave"
    assert file_stem("../x") == "_x" and file_stem("M31") == "M31" == file_stem(file_stem("M31"))
    (tmp_path / "frames").mkdir()
    source = LocalSources(tmp_path / "data").add("astro", tmp_path / "frames", 1.65)
    curves = LightCurves(source, tmp_path / "data", "NGC 7000/IC 5070")
    assert curves.stars_path.parent == curves.path.parent == curves.root


def test_sky_to_pixel_inverts_a_forward_only_distortion() -> None:
    from sdp.adapters.local.solver import pixel_to_sky

    wcs = {"CRVAL1": 10.0, "CRVAL2": 41.0, "CRPIX1": 450.5, "CRPIX2": 450.5,
           "CD1_1": -4e-4, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": 4e-4,
           "A_ORDER": 2, "B_ORDER": 2, "A_2_0": 2e-6, "A_0_2": -1e-6, "B_1_1": 3e-6}  # fmt: skip
    x, y = np.array([1.0, 300.0, 899.0]), np.array([1.0, 650.0, 20.0])
    back = sky_to_pixel(wcs, *pixel_to_sky(wcs, x, y))
    assert np.allclose(back[0], x, atol=1e-4) and np.allclose(back[1], y, atol=1e-4)


def test_sky_to_pixel_inverts_the_solution() -> None:
    from sdp.adapters.local.solver import pixel_to_sky

    wcs = {"CRVAL1": 10.0, "CRVAL2": 41.0, "CRPIX1": 450.5, "CRPIX2": 450.5,
           "CD1_1": -4e-4, "CD1_2": 1e-4, "CD2_1": 1e-4, "CD2_2": 4e-4}  # fmt: skip
    x, y = np.array([1.0, 300.0, 899.0]), np.array([1.0, 650.0, 20.0])
    back = sky_to_pixel(wcs, *pixel_to_sky(wcs, x, y))
    assert np.allclose(back[0], x, atol=1e-6) and np.allclose(back[1], y, atol=1e-6)


@pytest.mark.req("LOC-021")
def test_each_star_gets_a_calibrated_curve_and_the_variable_stands_out(built: tuple[Nights, dict[str, Any]]) -> None:
    w, summary = built
    assert summary["frames_measured"] == summary["frames_calibrated"] == NIGHTS * PER_NIGHT
    stars = {s["source_id"]: s for s in summary["all_stars"]}
    steady = [s for i, s in stars.items() if i != w.variable and s["g"] < 13.0]
    # haze took up to 0.39 mag; the frame-by-frame calibration takes it out again
    assert np.median([s["scatter"] for s in steady]) < 0.02
    g_of = dict(zip(w.field.source_id.tolist(), w.field.g.tolist(), strict=True))
    offsets = [s["mean_mag"] - g_of[i] for i, s in stars.items() if i != w.variable and s["g"] < 13.0]
    assert abs(float(np.median(offsets))) < 0.02, "on Gaia's G scale"
    assert [c["source_id"] for c in summary["candidates"]] == [w.variable]
    candidate = summary["candidates"][0]
    assert candidate["points"] == NIGHTS * PER_NIGHT and candidate["scatter"] > 0.1
    assert candidate["ls_period_days"] > 0
    assert summary["precision_by_g"]


@pytest.mark.req("LOC-021")
def test_the_curves_and_the_nightly_means_are_written_beside_the_summary(built: tuple[Nights, dict[str, Any]]) -> None:
    import pyarrow.parquet as pq

    w, _ = built
    curves = LightCurves(w.source, w.data, "M31")
    table = pq.read_table(curves.path)
    assert set(table.column("night").to_pylist()) == {"2025-09-14", "2025-09-17", "2025-09-20"}
    assert table.column("comparison").to_pylist().count(True) > 0
    nightly = pq.read_table(curves.nights_path).to_pylist()
    assert {r["points"] for r in nightly if r["source_id"] == w.variable} == {PER_NIGHT}
    assert json.loads(curves.stars_path.read_text())["candidates"][0]["source_id"] == w.variable
    # the frames were only read
    assert all(p.suffix == ".fits" for p in w.source.path.rglob("*") if p.is_file())


@pytest.mark.req("LOC-021")
def test_the_target_with_the_most_nights_and_its_cone_are_the_defaults(built: tuple[Nights, dict[str, Any]]) -> None:
    w, _ = built
    rows = Catalogue(w.source, w.data).rows()
    assert busiest_target(rows) == "M31"
    cone = field_cone(rows, "M31")
    # every sub points the same way: the cone is half a frame's diagonal, 900 pixels of 1.65 arcsec
    assert cone is not None and cone[2] == pytest.approx(0.2917, abs=0.002)
    assert field_cone(rows, "NGC7000") is None


@pytest.mark.req("LOC-021")
def test_the_lightcurve_command(built: tuple[Nights, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    w, _ = built
    asked: list[Any] = []

    def gaia_field(cone: Any, lo: float, hi: float) -> Field:
        asked.append((cone, lo, hi))
        return w.field

    monkeypatch.setattr(cli, "_gaia_field", gaia_field)
    result = runner.invoke(cli.app, ["local", "lightcurve", "astro"], env={"SDP_DATA": str(w.data)})
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["target"] == "M31" and [c["source_id"] for c in out["candidates"]] == [w.variable]
    assert asked[0][1:] == (9.0, 15.0) and "all_stars" not in out
    none = runner.invoke(
        cli.app, ["local", "lightcurve", "astro", "--target", "NGC7000"], env={"SDP_DATA": str(w.data)}
    )
    assert none.exit_code != 0 and "no solved, dated subs of NGC7000" in str(none.exception)


@pytest.mark.req("LOC-022")
def test_the_gateway_offers_each_measured_star_as_a_light_curve(built: tuple[Nights, dict[str, Any]]) -> None:
    w, _ = built
    g = LocalImageGateway(w.source, w.data)
    i = int(np.flatnonzero(w.field.source_id == w.variable)[0])
    target = ObservationTarget(f"gaia:DR3:{w.variable}", float(w.field.ra[i]), float(w.field.dec[i]))
    criteria = ObservationCriteria(radius_arcsec=2.0, product_types=("lightcurve",), missions=("light",))
    (record,) = g.search(target, criteria).records
    assert record.product.product_id == f"{CURVE_PREFIX}M31/{w.variable}" and record.product_type == "lightcurve"
    fetched = g.fetch(record.product)
    curve = parse_lightcurve(fetched.data)
    assert len(curve.mjd) == NIGHTS * PER_NIGHT and np.all(np.diff(curve.mjd) > 0)
    curated = LocalFrameDialect(g).curate("lightcurve", fetched)
    assert curated is not None and curated.data == fetched.data
    images = g.search(target, ObservationCriteria(radius_arcsec=2.0, product_types=("image",))).records
    assert len(images) == NIGHTS * PER_NIGHT and {r.product_type for r in images} == {"image"}
    d = descriptor(w.source, Catalogue(w.source, w.data).rows(), date(2026, 10, 30), True)
    assert ProductType.TIMESERIES in d.product_types
    first, last = record.epoch_mjd, record.epoch_end_mjd
    assert first is not None and last is not None
    for outside in (
        ObservationCriteria(radius_arcsec=2.0, product_types=("lightcurve",), epoch_max_mjd=first - 1.0),
        ObservationCriteria(radius_arcsec=2.0, product_types=("lightcurve",), epoch_min_mjd=last + 1.0),
        ObservationCriteria(radius_arcsec=2.0, product_types=("lightcurve",), min_calibration_level=2),
    ):
        assert g.search(target, outside).records == ()


@pytest.mark.req("LOC-022")
def test_a_build_with_no_stars_does_not_offer_time_series(
    built: tuple[Nights, dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sdp.adapters.local.lightcurves as lightcurves

    w, _ = built
    discovery = LocalDiscovery(None, {w.source.source_id: (w.source, w.data)})
    assert ProductType.TIMESERIES in discovery._descriptor(w.source.source_id).product_types
    empty = tmp_path / "Empty-stars.json"
    empty.write_text(json.dumps({"stars": 0, "all_stars": []}), "utf-8")
    monkeypatch.setattr(lightcurves, "stars_files", lambda _source, _data: [empty])
    discovery = LocalDiscovery(None, {w.source.source_id: (w.source, w.data)})
    assert ProductType.TIMESERIES not in discovery._descriptor(w.source.source_id).product_types


def variability_profile() -> TestProfile:
    profile = TestProfile.from_document(yaml.safe_load(PROFILE.read_text("utf-8")))
    identify = replace(
        profile.identify, source_id="synthetic-gaia", columns=CANONICAL_COLUMNS, quality_cuts=(), resolve=False
    )
    return replace(profile, identify=identify)


@pytest.mark.tier("T4")
@pytest.mark.req("LOC-022")
def test_the_variability_profile_flags_only_the_variable(built: tuple[Nights, dict[str, Any]]) -> None:
    w, _ = built
    g = LocalImageGateway(w.source, w.data)
    ports = investigate_ports(Variability(), catalogue=w.catalogue, resolve=False)
    ports.observation_gateways = {"local:astro": g}
    ports.product_dialects = {"local:astro": LocalFrameDialect(g)}
    k = build_in_memory_kernel(gateways={"synthetic-gaia": gaia_archive(w.catalogue)}, investigate=ports)
    assert k.kernel.investigate_field is not None
    cone = field_cone(Catalogue(w.source, w.data).rows(), "M31")
    assert cone is not None
    field_ = InvestigationField(Cone(cone[0], cone[1], 0.15), epoch_jyear=2016.0, label="M31 subs")
    run = k.kernel.investigate_field.execute(InvestigateFieldCommand(field_, variability_profile(), seed=1))
    assert all(r.state is StageState.COMPLETED for r in run.investigation_set.stages.values())
    stage = run.investigation_set.stages[Stage.TEST].artifact_id
    assert stage is not None
    results = decode_artifact(k.store.get(stage), JSON)["results"]
    assert len(results) >= 10
    flagged = {r["subject"]["object_key"] for r in results if r["p_value"] < 0.01}
    assert len(flagged) == 1 and str(w.variable) in next(iter(flagged))
