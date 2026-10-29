"""T4 for a field investigation's evidence dossier (INV-021) and its reproduction from that file alone
(INV-022), over the synthetic JWST slice: one image, twelve Gaia stars, one missing from the image."""

from __future__ import annotations

import json
from typing import Any

import pytest
from tests.usecase.test_jwst_slice import run_slice, slice_kernel

from sdp.adapters.products.preview import preview_png
from sdp.kernel.application.rocrate import build_crate_metadata, read_crate, verify_crate, zip_crate
from sdp.kernel.application.set_bundle import compare_tests, data_path, image_slug, overlay_svg, star_status
from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.domain.errors import IntegrityError
from sdp.kernel.domain.investigation import Stage
from sdp.kernel.ports.investigate import TriageCommand
from sdp.testing.jwst import VANISHED
from sdp.testing.wiring import InMemoryKernel

pytestmark = pytest.mark.tier("T4")


@pytest.fixture(scope="module")
def world() -> tuple[InMemoryKernel, dict[str, Any], bytes]:
    k = slice_kernel()
    assert k.kernel.set_dossier is not None
    k.kernel.set_dossier.ports.picture = preview_png
    _, run = run_slice(k)
    assert k.kernel.triage_investigation is not None and k.kernel.query_investigations is not None
    [candidate] = k.kernel.query_investigations.investigations(run["set"].set_id)
    run["candidate"] = candidate
    k.kernel.triage_investigation.execute(
        TriageCommand(candidate.investigation_id, "annotate", "ana", "gone from the F200W image; check F090W")
    )
    return k, run, k.kernel.set_dossier.build(run["set"].set_id)


def rebuilt(files: dict[str, bytes], **changed: bytes) -> bytes:
    """The dossier with some files replaced and its checksums made to agree, as a careful forger would."""
    parts = {path: (data, "application/octet-stream", path) for path, data in {**files, **changed}.items()}
    metadata = build_crate_metadata(
        name="x",
        description="x",
        licence="CC-BY-4.0",
        files=parts,
        authors=[],
        date_published="2026-01-01",
        citations=[],
    )
    return zip_crate(metadata, parts)


@pytest.mark.req("INV-021")
def test_the_dossier_holds_the_whole_set_and_every_file_checks(
    world: tuple[InMemoryKernel, dict[str, Any], bytes],
) -> None:
    _, run, bundle = world
    s = run["set"]
    assert verify_crate(bundle) == []
    metadata, files = read_crate(bundle)
    root = next(e for e in metadata["@graph"] if e["@id"] == "./")
    assert root["author"] and "EXPLORATORY" in root["description"] and root["citation"] == sorted(s.archive_releases)
    for stage in Stage:
        record = s.stages[stage]
        assert record.artifact_id is not None
        assert sha256_id(files[f"stages/{stage.value}.json"]) == record.artifact_id.value, "stored bytes, unchanged"
    manifest = json.loads(files["manifest.json"])
    assert manifest["set_id"] == s.set_id and manifest["seed"] == s.seed

    data = {path: blob for path, blob in files.items() if path.startswith("data/")}
    for path, blob in data.items():
        assert path.startswith(f"data/{sha256_id(blob).removeprefix('sha256:')}")
    retrieved = json.loads(files["stages/retrieve.json"])
    product = retrieved["bundles"][0]["products"][0]
    fits_path = f"data/{product['artifact_id'].removeprefix('sha256:')}.fits"
    assert files[fits_path].startswith(b"SIMPLE"), "the image the tests read is in the dossier"
    assert any(p.endswith(".parquet") for p in data), "and the Gaia rows"

    candidate = run["candidate"]
    plan = json.loads(files["plan.json"])
    assert plan["id"] == s.plan_artifact_id and plan["state"] == "executed"
    [triaged] = json.loads(files["investigations.json"])
    assert triaged["notes"][0]["author"] == "ana"
    assert f"dossiers/{candidate.investigation_id}.json" in files
    assert {"ana", "leigh"} <= {e["name"] for e in metadata["@graph"] if e.get("@type") == "Person"}

    [image] = json.loads(files["images.json"])
    slug = image["picture"]
    assert files[f"images/{slug}.png"].startswith(b"\x89PNG")
    svg = files[f"images/{slug}.svg"].decode()
    assert f'href="{slug}.png"' in svg and svg.count("<g>") == len(image["stars"]) == 12
    assert f">{VANISHED}</text>" in svg, "the flagged star is named on the picture"
    readme = files["README.md"].decode()
    assert "sdp investigate reproduce" in readme and VANISHED in readme and "1 flagged, 2 ambiguous" in readme


@pytest.mark.req("INV-022")
def test_the_test_stage_reproduces_from_the_dossier_alone(world: tuple[InMemoryKernel, dict[str, Any], bytes]) -> None:
    k, run, bundle = world
    assert k.kernel.set_dossier is not None
    reproduction = k.kernel.set_dossier.reproduce(bundle)
    assert reproduction.ok and reproduction.results == len(run["results"]) == 12
    doc = reproduction.to_document()
    assert doc["ok"] and doc["seed"] == 1 and doc["label"] == "EXPLORATORY"
    assert doc["tolerances"] and all(t["rtol"] >= 0 and t["atol"] >= 0 for t in doc["tolerances"].values())


@pytest.mark.req("INV-022")
def test_a_changed_result_is_reported_and_a_broken_dossier_refused(
    world: tuple[InMemoryKernel, dict[str, Any], bytes],
) -> None:
    k, _, bundle = world
    reproduce = k.kernel.set_dossier.reproduce  # type: ignore[union-attr]
    _, files = read_crate(bundle)
    recorded = json.loads(files["stages/test.json"])
    changed = json.loads(files["stages/test.json"])
    changed["results"][0]["statistic"] = changed["results"][0]["statistic"] + 1.0
    del changed["results"][1]
    differences = compare_tests(recorded, changed, {})
    assert any("statistic" in d for d in differences)
    assert any("missing from the reproduction" in d for d in differences)
    with pytest.raises(IntegrityError, match=r"stages/test\.json is not the test output the manifest records"):
        reproduce(rebuilt(files, **{"stages/test.json": json.dumps(changed).encode()}))

    with pytest.raises(IntegrityError, match="not an evidence dossier"):
        reproduce(b"not a zip")
    fits_path = next(p for p in files if p.endswith(".fits"))
    with pytest.raises(IntegrityError, match="does not hash to its name"):
        reproduce(rebuilt(files, **{fits_path: b"SIMPLE  = T"}))
    metadata = read_crate(bundle)[0]
    tampered = {p: (d, "", "") for p, d in files.items()} | {"manifest.json": (b"{}", "", "")}
    with pytest.raises(IntegrityError, match=r"fails verification: manifest\.json checksum mismatch"):
        reproduce(zip_crate(metadata, tampered))
    manifest = json.loads(files["manifest.json"])
    manifest["profile"]["version"] = "9.9.9"
    with pytest.raises(IntegrityError, match="does not hash"):
        reproduce(rebuilt(files, **{"manifest.json": json.dumps(manifest).encode()}))
    with pytest.raises(IntegrityError, match="lacks the products"):
        reproduce(rebuilt({p: d for p, d in files.items() if p != fits_path}))
    with pytest.raises(IntegrityError, match=r"no stages/test\.json"):
        reproduce(rebuilt({p: d for p, d in files.items() if p != "stages/test.json"}))
    with pytest.raises(KeyError):
        k.kernel.set_dossier.build("invset-missing")  # type: ignore[union-attr]


def test_names_and_marks() -> None:
    assert data_path("sha256:ab", "application/json") == "data/ab.json"
    assert data_path("sha256:ab", "application/octet-stream", b"SIMPLE  =") == "data/ab.fits"
    assert data_path("sha256:ab", "application/octet-stream", b"other") == "data/ab"
    taken: set[str] = set()
    assert image_slug("o1/jw_i2d.fits@cutout(1,2)", taken) == "jw_i2d.fits_cutout_1_2"
    assert image_slug("o2/jw_i2d.fits@cutout(1,2)", taken) == "jw_i2d.fits_cutout_1_2-2"
    assert image_slug("///") == "image"
    base = {"counterpart_px": [1, 1], "flagged": False, "ambiguous": False}
    assert star_status(base) == "matched"
    assert star_status({**base, "ambiguous": True}) == "ambiguous"
    assert star_status({**base, "flagged": True, "ambiguous": True}) == "displaced"
    assert star_status({**base, "counterpart_px": None, "flagged": True}) == "missing"
    star = {"object_key": "s", "predicted_px": [9.5, 0.0], "p_value": 0.5, **base}
    svg = overlay_svg({"width": 10, "height": 20, "stars": [star, {**star, "ambiguous": True}]}, "p.png")
    assert 'cx="10.00" cy="19.50"' in svg, "FITS (x, y) drawn at (x + 0.5, height - 0.5 - y)"
    assert svg.count("stroke-dasharray") == 1 and "<text" not in svg


def _result(subject: str, statistic: float, test: str = "a", **extra: Any) -> dict[str, Any]:
    return {"test_name": test, "test_version": "1", "subject": subject, "statistic": statistic, **extra}


def test_each_test_is_compared_within_its_own_tolerance() -> None:
    before = {"results": [_result("s", 1.0), _result("s", 1.0, test="b")], "null_results": []}
    after = {"results": [_result("s", 1.001), _result("s", 1.001, test="b")], "null_results": []}
    differences = compare_tests(before, after, {"a@1": (0.01, 0.0)})
    assert differences == ['b@1 "s": statistic 1.0 became 1.001'], "b has no tolerance, so it must match exactly"


def test_every_result_for_one_subject_is_compared() -> None:
    twice = {"results": [_result("s", 1.0, k=1), _result("s", 2.0, k=2)], "null_results": []}
    assert (
        compare_tests(twice, {"results": [_result("s", 2.0, k=2), _result("s", 1.0, k=1)], "null_results": []}, {})
        == []
    )
    once = {"results": [_result("s", 1.0, k=1)], "null_results": []}
    assert compare_tests(twice, once, {}) == ['a@1 "s": 2 result(s) became 1']
    moved = {"results": [_result("s", 1.0, k=1), _result("s", 3.0, k=2)], "null_results": []}
    assert any("statistic 2.0 became 3.0" in d for d in compare_tests(twice, moved, {}))


def test_a_star_name_cannot_inject_markup_into_the_picture() -> None:
    star = {
        "object_key": "<script>x</script>",
        "predicted_px": [1.0, 1.0],
        "p_value": 0.01,
        "counterpart_px": None,
        "flagged": True,
        "ambiguous": False,
    }
    svg = overlay_svg({"width": 4, "height": 4, "stars": [star]}, 'p".png')
    assert "<script>" not in svg and "&lt;script&gt;" in svg and 'href="p&quot;.png"' in svg


@pytest.mark.req("INV-021")
def test_a_missing_stage_output_stops_the_dossier() -> None:
    k = slice_kernel()
    _, run = run_slice(k)
    s = run["set"]
    artifact = s.stages[Stage.TEST].artifact_id
    assert artifact is not None and k.kernel.set_dossier is not None
    k.store._blobs.pop(artifact.value)
    with pytest.raises(IntegrityError, match="not in the artifact store"):
        k.kernel.set_dossier.build(s.set_id)
