"""T3: certifying the local frames adapter on the person's own drive, and the planner reading the records (#71)."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from sdp.adapters.local.catalogue import Catalogue, LocalDiscovery, descriptor
from sdp.adapters.local.certify import ADAPTER, Certification, admitted, records, reference_template
from sdp.adapters.local.fingerprints import sha256_of
from sdp.adapters.local.lightcurves import LightCurves
from sdp.adapters.local.sidecars import write_sidecars
from sdp.adapters.local.solving import LocalSolver
from sdp.adapters.local.sources import LocalSources
from sdp.cli import app as cli
from sdp.contracts.v1.adapter_compatibility_schema import AdapterCompatibilityRecord as RecordSchema
from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.integration import ConformanceKind, ProductType

from .test_lightcurves import Nights, nights

pytestmark = pytest.mark.tier("T3")
runner = CliRunner()
FRAMES = {
    "M31/night1/img-0000.fits": "2025-09-14T21:00:00",
    "M31/night2/img-0003.fits": "2025-09-17T21:45:00",
}


def _reference(**changes: Any) -> dict[str, Any]:
    frames = [
        {"path": path, "kind": "light", "target": "M 31", "date_obs": stamp, "exposure_s": 10, "ra_deg": 11.3,
         "dec_deg": 41.67}
        for path, stamp in FRAMES.items()
    ]  # fmt: skip
    return {"status": "verified", "verified_by": "Leigh", "frames": frames} | changes


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Nights:
    """The 24 M31 subs, solved (with each file's SHA-256 as solving records it), with .wcs sidecars and
    light curves built."""
    w = nights(tmp_path_factory.mktemp("certify"))
    solver = LocalSolver(w.source, w.data, None, lambda _c: None)  # type: ignore[arg-type,return-value]
    for path in sorted(solver.root.rglob("*.wcs.json")):
        doc = json.loads(path.read_text("utf-8"))
        doc["file"]["sha256"] = sha256_of(w.source.path / doc["frame"])
        path.write_text(json.dumps(doc), "utf-8")
    write_sidecars(solver)
    LightCurves(w.source, w.data, "M31").build(Catalogue(w.source, w.data).build(), w.field)
    return w


def copy_of(w: Nights, tmp_path: Path) -> Certification:
    """A certification over a copy of the data folder, so a failing run leaves the shared one alone."""
    data = tmp_path / "SDP"
    shutil.copytree(w.data, data)
    return Certification(LocalSources(data).get("astro"), data)


@pytest.mark.req("LOC-023")
def test_a_verified_reference_admits_images_and_light_curves(world: Nights) -> None:
    report = Certification(world.source, world.data).run(_reference())
    for product_type in ("image", "timeseries"):
        result = report["product_types"][product_type]
        failed = [f for k in result["kinds"] for f in k["failed"]]
        assert result["admitted"], failed
        assert [k["kind"] for k in result["kinds"]] == [k.value for k in ConformanceKind]
        document = json.loads(Path(result["record"]).read_text("utf-8"))
        RecordSchema.model_validate(document)
        assert document["adapter"] == ADAPTER and document["passed"]
        assert len(document["fixture_digests"]) == len(FRAMES)
        assert {r["ci_run_id"] for r in document["results"]} == {report["run_id"]}
    image = {k["kind"]: k for k in report["product_types"]["image"]["kinds"]}
    assert any("astropy agrees" in c for c in image["scientific"]["checked"])
    assert any("bytes fetched are the bytes that were solved" in c for c in image["operational"]["checked"])
    # the planner now sees both types admitted, and the frames were only read
    d = LocalDiscovery(None, {"local:astro": (world.source, world.data)})._descriptor("local:astro")
    assert d.adapter is not None and dict(d.adapter.conformance) == {"image": True, "timeseries": True}
    assert d.adapter.last_compatibility_check == date.today()
    assert all(p.suffix == ".fits" for p in world.source.path.rglob("*") if p.is_file())


@pytest.mark.req("LOC-023")
def test_an_unverified_or_mistaken_reference_admits_nothing(world: Nights, tmp_path: Path) -> None:
    certification = copy_of(world, tmp_path)
    unverified = certification.run(_reference(status="to_verify"))
    for result in unverified["product_types"].values():
        kinds = {k["kind"]: k for k in result["kinds"]}
        assert not result["admitted"] and not kinds["semantic"]["passed"]
        assert all(kinds[k]["passed"] for k in ("contract", "operational", "scientific"))
        assert "not verified yet" in " ".join(kinds["semantic"]["failed"])
    wrong = _reference()
    wrong["frames"][0] |= {"exposure_s": 300, "date_obs": "2025-09-14T23:00:00"}
    image = certification.run(wrong)["product_types"]["image"]
    failed = " ".join(f for k in image["kinds"] for f in k["failed"])
    assert not image["admitted"]
    assert "SDP reads a 10.0 s exposure, you wrote 300 s" in failed and "you wrote 2025-09-14T23:00:00" in failed
    missing = certification.run(_reference(frames=[{"path": "M31/night1/img-0000.fits", "kind": "light"}]))
    assert "write down its kind, target, date_obs and exposure_s" in json.dumps(missing)
    # failing records stand, and admit nothing
    assert all(not r.passed for r in records(certification.source, certification.data))
    rows = Catalogue(certification.source, certification.data).build()
    assert admitted(certification.source, certification.data, rows) == {}


@pytest.mark.req("LOC-024")
def test_a_changed_reference_frame_withdraws_the_admission(world: Nights, tmp_path: Path) -> None:
    certification = copy_of(world, tmp_path)
    certification.run(_reference())
    rows = Catalogue(certification.source, certification.data).build()
    assert admitted(certification.source, certification.data, rows) == {"image": True, "timeseries": True}
    changed = [r | {"sha256": "0" * 64} if r["path"] in FRAMES else r for r in rows]
    assert admitted(certification.source, certification.data, changed) == {}
    poor = [r | {"grade": "poor"} if r["path"] in FRAMES else r for r in rows]
    assert admitted(certification.source, certification.data, poor) == {}


@pytest.mark.req("LOC-024")
def test_without_a_record_the_frames_are_planned_as_exploratory_evidence(world: Nights) -> None:
    from sdp.kernel.domain.integration import EvidenceRequirement, MatchStatus, match_requirement

    rows = Catalogue(world.source, world.data).rows()
    d = descriptor(world.source, rows, date(2026, 10, 30), True)
    assert d.adapter is not None and dict(d.adapter.conformance) == {}
    assert d.strip_unproven_claims()[1] == (ProductType.IMAGE, ProductType.TIMESERIES)
    need = EvidenceRequirement("frames", "image-test", frozenset({ProductType.IMAGE}))
    match = match_requirement(d, Cone(11.3, 41.67, 0.1), need)
    reason = next(r for r in match.reasons if r.field == "adapter.conformance")
    assert match.status is MatchStatus.PARTIAL and "exploratory evidence only" in reason.reason
    # a record of another adapter, or of images only, claims only what it covers
    only_images = descriptor(world.source, rows, date(2026, 10, 30), False, {"image": True, "timeseries": True})
    assert only_images.adapter is not None and dict(only_images.adapter.conformance) == {"image": True}


@pytest.mark.req("LOC-023")
def test_the_certify_command(world: Nights, tmp_path: Path) -> None:
    certification = copy_of(world, tmp_path)
    env = {"SDP_DATA": str(certification.data)}
    shutil.rmtree(certification.root, ignore_errors=True)
    missing = runner.invoke(cli.app, ["local", "certify", "astro"], env=env)
    assert missing.exit_code != 0 and "--template" in str(missing.exception)
    made = runner.invoke(cli.app, ["local", "certify", "astro", "--template"], env=env)
    assert made.exit_code == 0, made.output
    template = yaml.safe_load(certification.reference_path.read_text("utf-8"))
    assert template["status"] == "to_verify" and len(template["frames"]) == 3
    assert {f["path"].split("/")[1] for f in template["frames"]} == {"night1", "night2", "night3"}
    assert all(v is None for f in template["frames"] for k, v in f.items() if k != "path")
    again = runner.invoke(cli.app, ["local", "certify", "astro", "--template"], env=env)
    assert again.exit_code != 0 and "exists already" in str(again.exception)
    unfilled = runner.invoke(cli.app, ["local", "certify", "astro"], env=env)
    assert unfilled.exit_code == 1 and not json.loads(unfilled.stdout)["product_types"]["image"]["admitted"]
    certification.reference_path.write_text(yaml.safe_dump(_reference()), "utf-8")
    done = runner.invoke(cli.app, ["local", "certify", "astro"], env=env)
    assert done.exit_code == 0, done.output
    assert {t: r["admitted"] for t, r in json.loads(done.stdout)["product_types"].items()} == {
        "image": True,
        "timeseries": True,
    }


def test_the_template_names_one_frame_per_target_and_night() -> None:
    rows = [
        {"path": f"{t}/n{n}/{i}.fits", "target": t, "night": f"2025-09-{n}", "status": "solved"}
        for t in ("M31", "M42") for n in (1, 2) for i in (1, 2)
    ]  # fmt: skip
    rows.append({"path": "M31/n1/x.fits", "target": "M31", "night": "2025-09-1", "status": "refused"})
    assert [f["path"] for f in reference_template(rows, count=3)["frames"]] == [
        "M31/n1/1.fits",
        "M31/n2/1.fits",
        "M42/n1/1.fits",
    ]
