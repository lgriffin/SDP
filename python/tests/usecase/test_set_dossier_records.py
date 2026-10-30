"""Records for the evidence dossier (INV-021) and its reproduction (INV-022): every file's name, type
and description, the README and picture word for word, the ledger lines, the tolerances read from
the manifest, and the comparison's edge cases."""

from __future__ import annotations

import json
import math
from typing import Any

import pytest
from tests.usecase.test_set_dossier import dossier_world, rebuilt

from sdp.kernel.application.rocrate import read_crate
from sdp.kernel.application.set_bundle import (
    _close,
    compare_tests,
    image_slug,
    overlay_svg,
    readme,
)
from sdp.kernel.domain.canonical import canonical_json, sha256_id
from sdp.kernel.domain.errors import IntegrityError
from sdp.kernel.domain.investigation import Stage
from sdp.testing.wiring import InMemoryKernel

pytestmark = pytest.mark.tier("T4")

World = tuple[InMemoryKernel, dict[str, Any], bytes]
IMAGE = "jw-synth-o001_t001_nircam_clear-f200w/jw-synth-o001_t001_nircam_clear-f200w_i2d.fits"
SLUG = "jw-synth-o001_t001_nircam_clear-f200w_i2d.fits"
DESCRIPTIONS = {
    "README.md": ("text/markdown", "What is in this dossier"),
    "dossiers/inv-000001.json": ("application/json", "Candidate dossier for j-vanished"),
    "images.json": ("application/json", "Each image with its envelope checks and the Gaia stars on it"),
    f"images/{SLUG}.png": ("image/png", f"{IMAGE}, pixel y upward"),
    f"images/{SLUG}.svg": ("image/svg+xml", f"{IMAGE} with its Gaia stars marked"),
    "investigations.json": ("application/json", "Every candidate with its state and triage history"),
    "manifest.json": ("application/json", "Investigation-set manifest: field, profile, seed, multiplicity"),
    "plan.json": ("application/json", "The acquisition plan, its approval and its execution"),
    **{f"stages/{s.value}.json": ("application/json", f"What the {s.value} stage wrote") for s in Stage},
}


# Per test, as in test_set_dossier: a shared world would hide mutants from every test but the first.
@pytest.fixture
def world() -> World:
    return dossier_world()


def entities(bundle: bytes) -> dict[str, dict[str, Any]]:
    metadata, _ = read_crate(bundle)
    return {e["@id"]: e for e in metadata["@graph"]}


def pretty(data: bytes) -> bytes:
    return (json.dumps(json.loads(data), indent=2, sort_keys=True) + "\n").encode()


@pytest.mark.req("INV-021")
def test_every_file_is_named_typed_and_described(world: World) -> None:
    _, run, bundle = world
    s = run["set"]
    graph = entities(bundle)
    described = {
        path: (e["encodingFormat"], e["description"])
        for path, e in graph.items()
        if e.get("@type") == "File" and not path.startswith("data/")
    }
    assert described == DESCRIPTIONS
    root = graph["./"]
    assert root["name"] == f"Investigation {s.set_id} of {s.field.field_id}"
    assert root["license"] == "CC-BY-4.0" and root["datePublished"] == s.started_at.date().isoformat()
    assert root["description"] == (
        f"Evidence dossier for investigation set {s.set_id}: 1 candidate(s) from 12 object(s) under profile "
        f"{s.profile.profile_id} v{s.profile.version}. EXPLORATORY."
    )


@pytest.mark.req("INV-021")
def test_the_documents_are_indented_and_sorted_and_no_stage_is_bundled_twice(world: World) -> None:
    _, run, bundle = world
    _, files = read_crate(bundle)
    for path in ("investigations.json", "images.json", "plan.json"):
        assert files[path] == pretty(files[path]), path
    named = {r.artifact_id.value.removeprefix("sha256:") for r in run["set"].stages.values() if r.artifact_id}
    data = {path.removeprefix("data/").split(".")[0] for path in files if path.startswith("data/")}
    assert len(data) == 15 and not named & data, "a stage file sits under its own name, not again under data/"


@pytest.mark.req("INV-021")
def test_the_readme_says_what_the_dossier_holds(world: World) -> None:
    _, run, bundle = world
    s = run["set"]
    _, files = read_crate(bundle)
    contents = "\n".join(
        f"- `{path}`: {desc}" for path, (_, desc) in sorted(DESCRIPTIONS.items()) if path != "README.md"
    )
    assert files["README.md"].decode() == (
        f"# Investigation {s.set_id}\n"
        "\n"
        f"Field **{s.field.field_id}** ({canonical_json(s.field.region.to_document())}), profile "
        f"**{s.profile.profile_id}** v{s.profile.version} (hash `{s.profile_hash}`), seed 1, started "
        f"{s.started_at.isoformat()}.\n"
        "\n"
        "Every result here is **EXPLORATORY**: it names objects worth a closer look, not findings. "
        "12 object(s) were examined with 1 test(s); 1 candidate(s) were opened.\n"
        "\n"
        "## Candidates\n"
        "\n"
        "- `j-vanished` (inv-000001, open): score 1, dossier `dossiers/inv-000001.json`\n"
        "\n"
        "## Images\n"
        "\n"
        f"- `{IMAGE}` (`images/{SLUG}.svg`): 12 Gaia star(s), 1 flagged, 2 ambiguous; no envelope\n"
        "\n"
        "## Reproduce\n"
        "\n"
        "The Test stage re-runs from this file alone, with no database or archive:\n"
        "\n"
        "    sdp investigate reproduce <this file>.zip\n"
        "\n"
        "It checks every file's SHA-256, runs the recorded tests with the recorded profile and seed over\n"
        "the products under `data/`, and reports any result that differs beyond the tests' tolerance.\n"
        "\n"
        "## Contents\n"
        "\n"
        f"{contents}\n"
        "- `data/`: 15 artifact(s), each named by the SHA-256 of its bytes\n"
    )


def test_the_readme_says_when_nothing_stood_out_and_how_each_image_checked(world: World) -> None:
    _, run, _ = world
    star = {"flagged": False, "ambiguous": False}
    images = [
        {"product_id": "o/passed.fits", "passed": True, "stars": [star]},
        {"product_id": "o/failed.fits", "passed": False, "stars": []},
    ]
    files = {"images/passed.fits.svg": (b"", "", "x"), "data/ab": (b"", "", "y"), "data/cd": (b"", "", "z")}
    text = readme(run["set"], [], images, files)
    assert "## Candidates\n\nNone stood out at the profile's threshold.\n\n## Images\n\n" in text
    assert (
        "- `o/passed.fits` (`images/passed.fits.svg`): 1 Gaia star(s), 0 flagged, 0 ambiguous; checks passed\n" in text
    )
    assert "- `o/failed.fits`: 0 Gaia star(s), 0 flagged, 0 ambiguous; checks FAILED\n" in text
    assert text.endswith(
        "## Contents\n\n- `images/passed.fits.svg`: x\n- `data/`: 2 artifact(s), each named by the SHA-256 of its bytes\n"
    )
    assert "## Images" not in readme(run["set"], [], [], {})


@pytest.mark.req("INV-021")
def test_the_plans_ledger_entries_travel_with_the_dossier(world: World) -> None:
    k, run, _ = world
    s = run["set"]
    assert k.kernel.set_dossier is not None
    plan_id = json.loads(read_crate(world[2])[1]["plan.json"])["document"]["plan_id"]
    k.ledger.append(kind="approval", subject_id="other", subject_hash="sha256:" + "0" * 64, signer="x", payload={})
    entry = k.ledger.append(kind="approval", subject_id=plan_id, subject_hash=plan_id, signer="leigh", payload={})
    again = k.ledger.append(kind="approval", subject_id=plan_id, subject_hash=plan_id, signer="leigh", payload={"n": 2})
    bundle = k.kernel.set_dossier.build(s.set_id)
    _, files = read_crate(bundle)
    lines = canonical_json(entry.to_document()) + "\n" + canonical_json(again.to_document()) + "\n"
    assert files["ledger.jsonl"] == lines.encode()
    described = entities(bundle)["ledger.jsonl"]
    assert described["encodingFormat"] == "application/x-ndjson"
    assert described["description"] == "Signed ledger entries for the plan's approval"


def test_a_missing_set_is_named(world: World) -> None:
    k, _, _ = world
    assert k.kernel.set_dossier is not None
    with pytest.raises(KeyError) as missing:
        k.kernel.set_dossier.build("invset-missing")
    assert missing.value.args == ("invset-missing",)


@pytest.mark.req("INV-022")
def test_a_reproduction_reports_its_set_tolerances_and_no_differences(world: World) -> None:
    k, run, bundle = world
    assert k.kernel.set_dossier is not None
    reproduction = k.kernel.set_dossier.reproduce(bundle)
    assert reproduction.differences == []
    assert reproduction.to_document() == {
        "set_id": run["set"].set_id,
        "ok": True,
        "seed": 1,
        "results_compared": 12,
        "tolerances": {"image-counterpart@1.0.0": {"rtol": 1e-09, "atol": 0.0}},
        "differences": [],
        "label": "EXPLORATORY",
    }


def with_manifest(bundle: bytes, change: Any) -> bytes:
    _, files = read_crate(bundle)
    manifest = json.loads(files["manifest.json"])
    change(manifest)
    return rebuilt(files, **{"manifest.json": json.dumps(manifest).encode()})


@pytest.mark.req("INV-022")
@pytest.mark.parametrize(
    ("policy", "expected"),
    [({"rtol": 0.5}, (0.5, 0.0)), ({"atol": 0.25}, (0.0, 0.25)), (None, (0.0, 0.0))],
    ids=["rtol only", "atol only", "no policy"],
)
def test_a_test_without_a_tolerance_must_match_exactly(world: World, policy: Any, expected: Any) -> None:
    k, _, bundle = world
    assert k.kernel.set_dossier is not None

    def change(manifest: dict[str, Any]) -> None:
        manifest["tests_applied"][0]["policy"] = policy

    reproduction = k.kernel.set_dossier.reproduce(with_manifest(bundle, change))
    assert reproduction.tolerances == {"image-counterpart@1.0.0": expected}


@pytest.mark.req("INV-022")
def test_a_manifest_without_tests_or_stages(world: World) -> None:
    k, _, bundle = world
    assert k.kernel.set_dossier is not None
    reproduction = k.kernel.set_dossier.reproduce(with_manifest(bundle, lambda m: m.pop("tests_applied")))
    assert reproduction.tolerances == {} and reproduction.ok
    with pytest.raises(IntegrityError, match=r"^stages/retrieve\.json is not the retrieve output"):
        k.kernel.set_dossier.reproduce(with_manifest(bundle, lambda m: m.pop("stages")))
    with pytest.raises(IntegrityError, match=r"^the recorded profile does not hash to the recorded profile_hash$"):
        k.kernel.set_dossier.reproduce(with_manifest(bundle, lambda m: m.update(profile_hash="sha256:00")))


@pytest.mark.req("INV-022")
def test_a_recorded_result_without_a_subject_is_malformed(world: World) -> None:
    k, _, bundle = world
    assert k.kernel.set_dossier is not None
    _, files = read_crate(bundle)
    test = json.loads(files["stages/test.json"])
    del test["results"][0]["subject"]
    data = json.dumps(test).encode()
    manifest = json.loads(files["manifest.json"])
    for st in manifest["stages"]:
        if st["stage"] == "test":
            st["artifact_id"] = sha256_id(data)
    with pytest.raises(IntegrityError, match="malformed"):
        k.kernel.set_dossier.reproduce(
            rebuilt(files, **{"stages/test.json": data, "manifest.json": json.dumps(manifest).encode()})
        )


def test_two_problems_are_listed_together(world: World) -> None:
    k, _, bundle = world
    assert k.kernel.set_dossier is not None
    _, files = read_crate(bundle)
    fits = sorted(p for p in files if p.endswith(".fits"))
    broken = rebuilt(files, **{fits[0]: b"SIMPLE  = T", "data/" + "0" * 64: b"x"})
    with pytest.raises(IntegrityError, match="does not hash to its name; data/"):
        k.kernel.set_dossier.reproduce(broken)


def test_closeness_by_kind() -> None:
    assert _close(None, None, 0.0, 0.0) and not _close(None, 0.0, 0.0, 0.0) and not _close(0.0, None, 0.0, 0.0)
    assert not _close(True, 2.0, 0.5, 0.5) and not _close(2.0, True, 0.5, 0.5), "a flag is not a number"
    assert _close(math.nan, math.nan, 0.0, 0.0)
    assert not _close(math.nan, 1.0, 0.0, 0.0) and not _close(1.0, math.nan, 0.0, 0.0)
    assert _close(1.0, 1.05, 0.1, 0.0) and not _close(1.0, 1.05, 0.0, 0.0)
    assert not _close(1.0, [1.0], 0.0, 0.0) and not _close([1.0], 1.0, 0.0, 0.0)
    assert _close([1.0, 2.0], [1.0, 2.05], 0.1, 0.0) and not _close([1.0, 2.0], [1.0, 3.0], 0.1, 0.0)
    assert not _close([1.0], [1.0, 2.0], 0.0, 0.0)
    assert _close({"a": 1.0}, {"a": 1.05}, 0.1, 0.0) and not _close({"a": 1.0}, {"b": 1.0}, 0.0, 0.0)
    assert not _close({"a": 1.0}, [1.0], 0.0, 0.0) and not _close([1.0], {"a": 1.0}, 0.0, 0.0)
    assert _close("x", "x", 0.0, 0.0) and not _close("x", "y", 0.0, 0.0)


def _result(subject: str, test: str = "a", **extra: Any) -> dict[str, Any]:
    return {"test_name": test, "test_version": "1", "subject": subject, "statistic": 1.0, **extra}


def test_every_key_on_either_side_is_compared() -> None:
    before = {"results": [_result("s"), _result("t", note="x")], "null_results": []}
    after = {"results": [_result("u"), _result("s", extra=1), _result("t")], "null_results": []}
    assert compare_tests(before, after, {}) == [
        'a@1 "s": extra None became 1',
        "a@1 \"t\": note 'x' became None",
        'a@1 "u": new in the reproduction',
    ]


def test_null_results_are_compared_by_test() -> None:
    null = {"test_name": "a", "test_version": "1", "trials": 5}
    before = {"results": [], "null_results": [null]}
    assert compare_tests(before, {"results": [], "null_results": [{**null, "trials": 6}]}, {}) == [
        "null a@1: trials 5 became 6"
    ]
    assert compare_tests(before, {"results": [], "null_results": []}, {}) == ["null a@1: missing from the reproduction"]


def test_an_image_slug_keeps_the_file_name_only() -> None:
    assert image_slug("obs/dir/JW_I2D.fits") == "JW_I2D.fits"
    assert image_slug("obs/@x@") == "x"
    taken: set[str] = set()
    assert [image_slug("a/f", taken), image_slug("b/f", taken), image_slug("c/f", taken)] == ["f", "f-2", "f-3"]
    assert taken == {"f", "f-2", "f-3"}


def test_the_picture_marks_each_kind_of_star() -> None:
    base = {"p_value": 0.01234, "flagged": False, "ambiguous": False, "counterpart_px": [1, 1]}
    stars = [
        {**base, "object_key": "m", "predicted_px": [2.0, 3.0]},
        {**base, "object_key": "a", "predicted_px": [10.0, 20.0], "ambiguous": True},
        {**base, "object_key": "d", "predicted_px": [30.0, 40.0], "flagged": True},
        {**base, "object_key": "x", "predicted_px": [50.0, 60.0], "flagged": True, "counterpart_px": None},
        {**base, "object_key": "q", "predicted_px": [5.0, 6.0], "counterpart_px": None},
    ]
    label = 'font-family="sans-serif" font-size="2.60" stroke="#000" stroke-width="0.50" paint-order="stroke"'
    assert overlay_svg({"width": 140, "height": 70, "stars": stars}, "p.png") == "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 70" width="140" height="70">',
            '<image href="p.png" x="0" y="0" width="140" height="70" preserveAspectRatio="none"/>',
            "<g><title>m: matched, p = 0.0123</title>",
            '<circle cx="2.50" cy="66.50" r="2.00" fill="none" stroke="#7fd49b" stroke-width="1.5"/>',
            "</g>",
            "<g><title>a: ambiguous, p = 0.0123</title>",
            '<circle cx="10.50" cy="49.50" r="2.00" fill="none" stroke="#f2c14e" stroke-width="1.5" '
            'stroke-dasharray="4 3"/>',
            "</g>",
            "<g><title>d: displaced, p = 0.0123</title>",
            '<path d="M30.50,27.00L33.00,29.50L30.50,32.00L28.00,29.50Z" fill="none" stroke="#ff7a6b" stroke-width="3"/>',
            f'<text x="33.70" y="26.30" fill="#ff7a6b" {label}>d</text>',
            "</g>",
            "<g><title>x: missing, p = 0.0123</title>",
            '<rect x="48.50" y="7.50" width="4.00" height="4.00" fill="none" stroke="#ff7a6b" stroke-width="3"/>',
            f'<text x="53.70" y="6.30" fill="#ff7a6b" {label}>x</text>',
            "</g>",
            "<g><title>q: missing, p = 0.0123</title>",
            '<rect x="3.50" y="61.50" width="4.00" height="4.00" fill="none" stroke="#ff7a6b" stroke-width="1.5"/>',
            "</g>",
            "</svg>",
            "",
        ]
    )
