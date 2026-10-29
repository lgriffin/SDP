"""An investigation set's evidence dossier as one file, and its reproduction from that file (INV-021, INV-022).

The dossier is an RO-Crate (ADR-009) with everything a reader needs to check the set without SDP's
database or any archive:

- ``manifest.json``: the set manifest (field, hashed profile, seed, releases, multiplicity);
- ``stages/<stage>.json``: what each stage wrote, byte for byte, so each hashes to the id the
  manifest records;
- ``plan.json`` and ``ledger.jsonl``: the acquisition plan, its approval and its signed entry;
- ``investigations.json`` and ``dossiers/<id>.json``: each candidate with its triage history;
- ``images.json`` and ``images/<image>.png|.svg``: each image as a picture, with every Gaia star drawn
  where its motion puts it and the flagged ones named (INV-020);
- ``data/<sha256>.<ext>``: every artifact the stages name (catalogue rows, the FITS products, their
  raw files and envelopes), named by the SHA-256 of their bytes;
- ``README.md``: the same in words.

Reproduction reads only the crate: it verifies every checksum, then re-runs the Test stage over
the bundled products with the recorded profile and seed and compares each result with the
recorded one within the tests' numerical policies. The label stays EXPLORATORY: reproducing an
exploratory result does not make it confirmatory.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from html import escape
from typing import Any

from sdp.kernel.application.field_images import field_images
from sdp.kernel.application.investigate import InvestigatePorts, run_tests, set_path
from sdp.kernel.application.media import CSV, JSON, PARQUET
from sdp.kernel.application.planning import plan_view
from sdp.kernel.application.rocrate import build_crate_metadata, read_crate, verify_crate, zip_crate
from sdp.kernel.domain.canonical import canonical_json, sha256_id
from sdp.kernel.domain.errors import IntegrityError
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.investigation import (
    EXPLORATORY,
    STAGES,
    Investigation,
    InvestigationSet,
    StageState,
    TestProfile,
)
from sdp.kernel.ports.compute import ComputeExecutor
from sdp.kernel.ports.ledger import Ledger
from sdp.kernel.ports.repositories import UnitOfWork
from sdp.kernel.ports.storage import ArtifactStore
from sdp.kernel.science.investigation import InvestigationTestRegistry
from sdp.kernel.science.plan import ComputeTask

DOSSIER_MEDIA_TYPE = "application/zip"
MARKDOWN = "text/markdown"
PNG = "image/png"
SVG = "image/svg+xml"
EXTENSIONS = {
    JSON: ".json",
    PARQUET: ".parquet",
    CSV: ".csv",
    "application/fits": ".fits",
    "image/fits": ".fits",
    "application/x-votable+xml": ".vot",
    "text/plain": ".txt",
}
_ARTIFACT = re.compile(r"sha256:[0-9a-f]{64}")
_DATA = re.compile(r"data/([0-9a-f]{64})(\.[a-z]+)?")


def data_path(artifact_id: str, media_type: str, data: bytes = b"") -> str:
    """Where an artifact sits in the dossier: named by its hash, so the name proves the bytes. A FITS
    file an archive served as plain bytes is still named ``.fits``, so a viewer opens it."""
    extension = EXTENSIONS.get(media_type) or (".fits" if data.startswith(b"SIMPLE  =") else "")
    return f"data/{artifact_id.removeprefix('sha256:')}{extension}"


def image_slug(product_id: str, taken: set[str] | None = None) -> str:
    """A file name for an image's picture: the product's file name, with a number when two match."""
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", product_id.rsplit("/", 1)[-1]).strip("_") or "image"
    slug, n = base, 1
    while taken is not None and slug in taken:
        n += 1
        slug = f"{base}-{n}"
    if taken is not None:
        taken.add(slug)
    return slug


def _pretty(doc: Any) -> bytes:
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()


def overlay_svg(image: Mapping[str, Any], picture: str) -> str:
    """The image with its Gaia stars marked, as the evidence UI draws them (INV-020): a circle where a
    star is found, dashed when the match is ambiguous, a diamond off its place, a square missing, and
    the flagged stars named. FITS pixel (x, y) is drawn at (x + 0.5, height - 0.5 - y), pixel y upward."""
    w, h = image["width"], image["height"]
    r = max(w, h) / 70
    colour = {"matched": "#7fd49b", "ambiguous": "#f2c14e", "displaced": "#ff7a6b", "missing": "#ff7a6b"}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
        f'<image href="{escape(picture)}" x="0" y="0" width="{w}" height="{h}" preserveAspectRatio="none"/>',
    ]
    for star in image["stars"]:
        status = star_status(star)
        x, y = star["predicted_px"][0] + 0.5, h - 0.5 - star["predicted_px"][1]
        style = f'fill="none" stroke="{colour[status]}" stroke-width="{3 if star["flagged"] else 1.5}"'
        if status == "ambiguous":
            style += ' stroke-dasharray="4 3"'
        key = escape(str(star["object_key"]))  # a key is archive text: never markup in the picture
        parts.append(f"<g><title>{key}: {status}, p = {star['p_value']:.3g}</title>")
        if status == "missing":
            parts.append(f'<rect x="{x - r:.2f}" y="{y - r:.2f}" width="{2 * r:.2f}" height="{2 * r:.2f}" {style}/>')
        elif status == "displaced":
            d = r * 1.25
            parts.append(
                f'<path d="M{x:.2f},{y - d:.2f}L{x + d:.2f},{y:.2f}L{x:.2f},{y + d:.2f}L{x - d:.2f},{y:.2f}Z" {style}/>'
            )
        else:
            parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" {style}/>')
        if star["flagged"]:
            parts.append(
                f'<text x="{x + 1.6 * r:.2f}" y="{y - 1.6 * r:.2f}" fill="{colour[status]}" '
                f'font-family="sans-serif" font-size="{1.3 * r:.2f}" stroke="#000" stroke-width="{0.25 * r:.2f}" '
                f'paint-order="stroke">{key}</text>'
            )
        parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def star_status(star: Mapping[str, Any]) -> str:
    """missing, displaced (flagged with a counterpart), ambiguous or matched, as the UI reads it."""
    if star["counterpart_px"] is None:
        return "missing"
    if star["flagged"]:
        return "displaced"
    return "ambiguous" if star["ambiguous"] else "matched"


def _close(a: Any, b: Any, rtol: float, atol: float) -> bool:
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return bool(a == b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(a, b, rel_tol=rtol, abs_tol=atol)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y, rtol, atol) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], rtol, atol) for k in a)
    return bool(a == b)


Tolerance = tuple[float, float]


def _grouped(docs: Sequence[Mapping[str, Any]], *, null: bool) -> dict[str, list[Mapping[str, Any]]]:
    """Results by test and subject (null results by test), every record kept: a test may give one
    subject more than one result, and each must be there in both runs."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for d in docs:
        key = f"{d['test_name']}@{d['test_version']}"
        key = f"null {key}" if null else f"{key} {canonical_json(d['subject'])}"
        groups.setdefault(key, []).append(d)
    for records in groups.values():
        records.sort(key=canonical_json)
    return groups


def compare_tests(
    original: Mapping[str, Any], reproduced: Mapping[str, Any], tolerances: Mapping[str, Tolerance]
) -> list[str]:
    """What differs between two Test stage documents, each result within its own test's numerical
    policy (``tolerances`` by ``name@version``; a test without one must match exactly)."""
    differences: list[str] = []
    for part, null in (("results", False), ("null_results", True)):
        before, after = _grouped(original[part], null=null), _grouped(reproduced[part], null=null)
        for key in sorted(before.keys() | after.keys()):
            if key not in after or key not in before:
                differences.append(f"{key}: {'missing from' if key not in after else 'new in'} the reproduction")
                continue
            if len(before[key]) != len(after[key]):
                differences.append(f"{key}: {len(before[key])} result(s) became {len(after[key])}")
                continue
            for old, new in zip(before[key], after[key], strict=True):
                rtol, atol = tolerances.get(f"{old['test_name']}@{old['test_version']}", (0.0, 0.0))
                for name in sorted(old.keys() | new.keys()):
                    if not _close(old.get(name), new.get(name), rtol, atol):
                        differences.append(f"{key}: {name} {old.get(name)!r} became {new.get(name)!r}")
    return differences


@dataclass
class SetReproduction:
    set_id: str
    seed: int
    results: int = 0
    tolerances: dict[str, Tolerance] = field(default_factory=dict)
    differences: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.differences

    def to_document(self) -> dict[str, Any]:
        return {
            "set_id": self.set_id,
            "ok": self.ok,
            "seed": self.seed,
            "results_compared": self.results,
            "tolerances": {k: {"rtol": r, "atol": a} for k, (r, a) in sorted(self.tolerances.items())},
            "differences": self.differences,
            "label": EXPLORATORY,
        }


class _NoCheckpoints:
    """A reproduction keeps nothing: every task runs, and its value is only compared."""

    def completed(self, task_id: str) -> tuple[bool, Any]:
        return False, None

    def write(self, task: ComputeTask, value: Any, duration_s: float, resources: Mapping[str, float]) -> None:
        return None


@dataclass
class SetDossierUseCase:
    uow: Callable[[], UnitOfWork]
    store: ArtifactStore
    ledger: Ledger
    executor: ComputeExecutor
    ports: InvestigatePorts
    licence: str = "CC-BY-4.0"

    # -- build ---------------------------------------------------------------------------------

    def build(self, set_id: str) -> bytes:
        """The set's evidence dossier (INV-021). ``KeyError`` when there is no such set."""
        with self.uow() as uow:
            s = uow.investigations.get_set(set_id)
            if s is None:
                raise KeyError(set_id)
            investigations = list(uow.investigations.for_set(set_id))
            record = uow.plans.get(s.plan_artifact_id) if s.plan_artifact_id else None
            uow.commit()
        files: dict[str, tuple[bytes, str, str]] = {}
        named: set[str] = set()

        def stored(path: str, artifact: ArtifactId | None, description: str) -> bytes:
            # A dossier with a hole in it is not evidence: refuse rather than save part of the set.
            if artifact is None or not self.store.exists(artifact):
                raise IntegrityError(f"{set_id}'s {path} is not in the artifact store; the dossier would be incomplete")
            data = self.store.get(artifact)
            files[path] = (data, JSON, description)
            named.add(artifact.value)
            return data

        manifest = self.store.resolve(set_path(set_id, "manifest.json")) or s.manifest_id
        texts = [stored("manifest.json", manifest, "Investigation-set manifest: field, profile, seed, multiplicity")]
        for stage in STAGES:
            done = s.stages[stage]
            if done.state is StageState.COMPLETED:
                texts.append(
                    stored(f"stages/{stage.value}.json", done.artifact_id, f"What the {stage.value} stage wrote")
                )
        for i in investigations:
            if "dossier_id" in i.links:
                texts.append(
                    stored(
                        f"dossiers/{i.investigation_id}.json",
                        ArtifactId(i.links["dossier_id"]),
                        f"Candidate dossier for {i.candidate.key}",
                    )
                )
        files["investigations.json"] = (
            _pretty([i.to_document() for i in investigations]),
            JSON,
            "Every candidate with its state and triage history",
        )
        if record is not None:
            files["plan.json"] = (
                _pretty(plan_view(record)),
                JSON,
                "The acquisition plan, its approval and its execution",
            )
            entries = [e for e in self.ledger.entries() if e.subject_id == record.plan.plan_id]
            if entries:
                lines = "".join(canonical_json(e.to_document()) + "\n" for e in entries).encode()
                files["ledger.jsonl"] = (lines, "application/x-ndjson", "Signed ledger entries for the plan's approval")
        for found in sorted({a for text in texts if text for a in _ARTIFACT.findall(text.decode("utf-8", "replace"))}):
            artifact = ArtifactId(found)
            if found in named or not self.store.exists(artifact):
                continue
            media, data = self.store.media_type(artifact), self.store.get(artifact)
            files[data_path(found, media, data)] = (data, media, f"Artifact {found}")
        images = field_images(self.store, s, investigations)
        slugs: set[str] = set()
        for image in images:
            if self.ports.picture is None or not image["width"]:
                continue
            slug = image_slug(image["product_id"], slugs)
            try:
                picture = self.ports.picture(self.store.get(ArtifactId(image["artifact_id"])))
            except ValueError:
                continue
            image["picture"] = slug
            files[f"images/{slug}.png"] = (picture, PNG, f"{image['product_id']}, pixel y upward")
            files[f"images/{slug}.svg"] = (
                overlay_svg(image, f"{slug}.png").encode(),
                SVG,
                f"{image['product_id']} with its Gaia stars marked",
            )
        files["images.json"] = (_pretty(images), JSON, "Each image with its envelope checks and the Gaia stars on it")
        files["README.md"] = (readme(s, investigations, images, files).encode(), MARKDOWN, "What is in this dossier")
        authors = sorted(
            {n.author for i in investigations for n in i.notes}
            | ({record.plan.approval.actor} if record and record.plan.approval else set())
        )
        metadata = build_crate_metadata(
            name=f"Investigation {set_id} of {s.field.field_id}",
            description=(
                f"Evidence dossier for investigation set {set_id}: {len(investigations)} candidate(s) from "
                f"{len(s.objects_examined)} object(s) under profile {s.profile.profile_id} v{s.profile.version}. {EXPLORATORY}."
            ),
            licence=self.licence,
            files=files,
            authors=authors,
            date_published=s.started_at.date().isoformat(),
            citations=sorted(s.archive_releases),
        )
        return zip_crate(metadata, files)

    # -- reproduce -----------------------------------------------------------------------------

    def reproduce(self, bundle: bytes) -> SetReproduction:
        """Re-run the Test stage from the dossier alone and compare (INV-022)."""
        return reproduce_dossier(bundle, self.executor, self.ports.tests, self.ports.services)


def reproduce_dossier(
    bundle: bytes, executor: ComputeExecutor, tests: InvestigationTestRegistry, services: Mapping[str, Any]
) -> SetReproduction:
    """Re-run a dossier's Test stage from the dossier alone and compare (INV-022): no database, no
    archive, only the tests and the services they use. ``IntegrityError`` when the dossier is broken,
    its stage files are not the ones its manifest names, or it holds no completed Test stage."""
    try:
        problems = verify_crate(bundle)
        _, files = read_crate(bundle)
    except Exception as exc:  # a zip that is not one, or a crate without its metadata
        raise IntegrityError(f"not an evidence dossier: {exc}") from exc
    for path, data in files.items():
        named = _DATA.fullmatch(path)
        if named and sha256_id(data) != f"sha256:{named.group(1)}":
            problems.append(f"{path} does not hash to its name")
    if problems:
        raise IntegrityError(f"the dossier fails verification: {'; '.join(problems)}")
    for needed in ("manifest.json", "stages/retrieve.json", "stages/test.json"):
        if needed not in files:
            raise IntegrityError(f"the dossier has no {needed}; only a set whose Test stage completed is reproduced")
    manifest = json.loads(files["manifest.json"])
    profile = TestProfile.from_document(manifest["profile"])
    if profile.profile_hash != manifest["profile_hash"]:
        raise IntegrityError("the recorded profile does not hash to the recorded profile_hash")
    # The crate's own checksums can be rewritten; the manifest names each stage's output by its hash.
    recorded_ids = {st["stage"]: st.get("artifact_id") for st in manifest.get("stages", [])}
    for stage in ("retrieve", "test"):
        if sha256_id(files[f"stages/{stage}.json"]) != recorded_ids.get(stage):
            raise IntegrityError(f"stages/{stage}.json is not the {stage} output the manifest records")
    retrieved = json.loads(files["stages/retrieve.json"])
    recorded = json.loads(files["stages/test.json"])
    by_id = {f"sha256:{m.group(1)}": data for path, data in files.items() if (m := _DATA.fullmatch(path))}
    wanted = {p["artifact_id"] for b in retrieved["bundles"] for p in b["products"]}
    missing = sorted(wanted - by_id.keys())
    if missing:
        raise IntegrityError(f"the dossier lacks the products the tests read: {', '.join(missing)}")
    result = SetReproduction(
        manifest["set_id"],
        int(manifest["seed"]),
        tolerances={
            f"{t['name']}@{t['version']}": (
                float((t.get("policy") or {}).get("rtol", 0.0)),
                float((t.get("policy") or {}).get("atol", 0.0)),
            )
            for t in manifest.get("tests_applied", [])
        },
    )
    results, nulls = run_tests(
        executor,
        tests,
        profile,
        retrieved["bundles"],
        {a: by_id[a] for a in sorted(wanted)},
        seed=result.seed,
        sink=_NoCheckpoints(),
        services=services,
    )
    reproduced = {"results": [r.to_document() for r in results], "null_results": [n.to_document() for n in nulls]}
    result.results = len(results)
    result.differences = compare_tests(recorded, json.loads(canonical_json(reproduced)), result.tolerances)
    return result


def readme(
    s: InvestigationSet,
    investigations: Sequence[Investigation],
    images: Sequence[Mapping[str, Any]],
    files: Mapping[str, tuple[bytes, str, str]],
) -> str:
    region = s.field.region.to_document()
    lines = [
        f"# Investigation {s.set_id}",
        "",
        f"Field **{s.field.field_id}** ({canonical_json(region)}), profile **{s.profile.profile_id}** "
        f"v{s.profile.version} (hash `{s.profile_hash}`), seed {s.seed}, started {s.started_at.isoformat()}.",
        "",
        f"Every result here is **{EXPLORATORY}**: it names objects worth a closer look, not findings. "
        f"{len(s.objects_examined)} object(s) were examined with {len(s.profile.tests)} test(s); "
        f"{len(investigations)} candidate(s) were opened.",
        "",
        "## Candidates",
        "",
    ]
    if not investigations:
        lines.append("None stood out at the profile's threshold.")
    for i in investigations:
        lines.append(
            f"- `{i.candidate.key}` ({i.investigation_id}, {i.state.value}): score "
            f"{i.rank.score:.3g}, dossier `dossiers/{i.investigation_id}.json`"
        )
    if images:
        lines += ["", "## Images", ""]
        for image in images:
            flagged = sum(1 for star in image["stars"] if star["flagged"])
            ambiguous = sum(1 for star in image["stars"] if star["ambiguous"])
            checks = (
                "no envelope" if image["passed"] is None else ("checks passed" if image["passed"] else "checks FAILED")
            )
            drawn = (
                f" (`images/{image_slug(image['product_id'])}.svg`)"
                if f"images/{image_slug(image['product_id'])}.svg" in files
                else ""
            )
            lines.append(
                f"- `{image['product_id']}`{drawn}: {len(image['stars'])} Gaia star(s), {flagged} flagged, "
                f"{ambiguous} ambiguous; {checks}"
            )
    lines += [
        "",
        "## Reproduce",
        "",
        "The Test stage re-runs from this file alone, with no database or archive:",
        "",
        "    sdp investigate reproduce <this file>.zip",
        "",
        "It checks every file's SHA-256, runs the recorded tests with the recorded profile and seed over",
        "the products under `data/`, and reports any result that differs beyond the tests' tolerance.",
        "",
        "## Contents",
        "",
        *(f"- `{path}`: {desc}" for path, (_d, _m, desc) in sorted(files.items()) if not path.startswith("data/")),
        f"- `data/`: {sum(1 for p in files if p.startswith('data/'))} artifact(s), each named by the SHA-256 of its bytes",
        "",
    ]
    return "\n".join(lines)
