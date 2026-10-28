"""Clean-room reproduction of a released experiment (T9, NFR-004).

    sdp reproduce <bundle.zip | manifest.json> --inputs DIR [--out DIR]

reads the evidence manifest (from an RO-Crate bundle, whose checksums are verified first, or a bare
manifest), finds every input it names in ``DIR`` by the SHA-256 of its bytes, freezes the recorded
predeclaration, and re-runs the recorded protocol with the recorded parameters and seed on the
in-memory kernel of this checkout. The reproduction passes when the protocol version and the
predeclaration hash match, the verdict and every gate outcome are the same, and every finding agrees
within the loosest NumericalPolicy of the recorded tasks. It writes ``reproduction.json`` and the
reproduced manifest to ``--out`` and exits 1 on any difference.

``infrastructure/reproduce.sh`` runs ``sdp reproduce`` inside a fresh container built from the pinned lock files.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdp.kernel.application.plugins import EntryPointProtocolRegistry
from sdp.kernel.application.rocrate import read_crate, verify_crate
from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.domain.predeclaration import Predeclaration
from sdp.kernel.ports.driving import DefineExperimentCommand, FreezePredeclarationCommand
from sdp.kernel.science.protocol import ScientificProtocol
from sdp.testing.synthetic import SyntheticLinearProtocol
from sdp.testing.wiring import build_in_memory_kernel


class ReproductionError(Exception):
    """The bundle cannot be reproduced at all: a broken bundle, an absent input, an unknown protocol."""


@dataclass
class Reproduction:
    experiment_id: str
    protocol: str
    original_verdict: str
    reproduced_verdict: str | None = None
    rtol: float = 0.0
    atol: float = 0.0
    differences: list[str] = field(default_factory=list)
    manifest: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return not self.differences and self.reproduced_verdict == self.original_verdict

    def to_document(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "protocol": self.protocol,
            "ok": self.ok,
            "original_verdict": self.original_verdict,
            "reproduced_verdict": self.reproduced_verdict,
            "tolerance": {"rtol": self.rtol, "atol": self.atol},
            "differences": self.differences,
        }


def load_manifest(path: Path) -> dict[str, Any]:
    """The manifest from a bundle (checksums verified) or from a manifest file."""
    if path.suffix == ".zip":
        data = path.read_bytes()
        problems = verify_crate(data)
        if problems:
            raise ReproductionError(f"bundle {path} fails verification: {'; '.join(problems)}")
        _, files = read_crate(data)
        if "manifest.json" not in files:
            raise ReproductionError(f"bundle {path} has no manifest.json")
        return dict(json.loads(files["manifest.json"]))
    return dict(json.loads(path.read_text("utf-8")))


def index_inputs(directory: Path) -> dict[str, Path]:
    """Every file under ``directory`` by the SHA-256 of its bytes, so names never matter."""
    return {sha256_id(p.read_bytes()): p for p in sorted(directory.rglob("*")) if p.is_file()}


def _tolerance(manifest: Mapping[str, Any]) -> tuple[float, float]:
    policies = [t.get("policy") or {} for t in manifest["execution"].get("tasks", [])]
    rtol = max((float(p.get("rtol", 0.0)) for p in policies), default=0.0)
    atol = max((float(p.get("atol", 0.0)) for p in policies), default=0.0)
    return rtol, atol


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


def compare(original: Mapping[str, Any], reproduced: Mapping[str, Any], rtol: float, atol: float) -> list[str]:
    """What differs between two manifests of the same experiment, beyond the recorded tolerance."""
    differences: list[str] = []
    before = {c["gate_id"]: c["outcome"] for c in original.get("checks", [])}
    after = {c["gate_id"]: c["outcome"] for c in reproduced.get("checks", [])}
    for gate in sorted(before.keys() | after.keys()):
        if before.get(gate) != after.get(gate):
            differences.append(f"gate {gate}: {before.get(gate)} became {after.get(gate)}")
    old = {f["name"]: f for f in original.get("findings", [])}
    new = {f["name"]: f for f in reproduced.get("findings", [])}
    for name in sorted(old.keys() | new.keys()):
        if name not in new or name not in old:
            differences.append(f"finding {name}: {'missing' if name not in new else 'new'} in the reproduction")
            continue
        for part in ("value", "uncertainty", "unit"):
            if not _close(old[name].get(part), new[name].get(part), rtol, atol):
                differences.append(f"finding {name}: {part} {old[name].get(part)!r} became {new[name].get(part)!r}")
    return differences


def _protocol(name: str) -> ScientificProtocol:
    if name == SyntheticLinearProtocol.protocol_id.name:
        return SyntheticLinearProtocol()
    try:
        return EntryPointProtocolRegistry().get(name)
    except Exception as exc:
        raise ReproductionError(f"protocol {name} is not installed in this environment: {exc}") from exc


def reproduce(
    manifest: Mapping[str, Any],
    inputs: Mapping[str, Path],
    services: Mapping[str, Any] | None = None,
    signer: str = "reproduce",
) -> Reproduction:
    """Re-run the experiment ``manifest`` records. ``services`` are the kernel services its tasks
    need (the composition root's clusterers for UC-01, say)."""
    identity = manifest["identity"]
    name, version = identity["protocol_id"], identity["protocol_version"]
    rtol, atol = _tolerance(manifest)
    result = Reproduction(
        identity["experiment_id"], f"{name}@{version}", manifest["verdict"]["status"], rtol=rtol, atol=atol
    )
    protocol = _protocol(name)
    if protocol.protocol_id.version != version:
        result.differences.append(f"protocol version: recorded {version}, installed {protocol.protocol_id.version}")
        return result
    intent = manifest["intent"]
    predeclaration = Predeclaration.from_document(intent["predeclaration"])
    if predeclaration.compute_hash() != intent["predeclaration_hash"]:
        raise ReproductionError("the recorded predeclaration does not hash to the recorded predeclaration_hash")
    missing = [i["artifact_id"] for i in manifest["inputs"] if i["artifact_id"] not in inputs]
    if missing:
        raise ReproductionError(f"inputs not found by checksum: {', '.join(missing)}")

    k = build_in_memory_kernel(protocol, services=services)
    frozen, _ = k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(predeclaration, signer))
    artifacts = {
        i["role"]: k.store.put(inputs[i["artifact_id"]].read_bytes(), i["media_type"]) for i in manifest["inputs"]
    }
    execution = manifest["execution"]
    experiment = k.kernel.define_experiment.execute(
        DefineExperimentCommand(
            name, frozen.hash, artifacts, dict(execution["parameters"]), seed=int(execution["seed"])
        )
    )
    experiment = k.kernel.run_experiment.execute(experiment.experiment_id)
    result.reproduced_verdict = "FAILED"
    if experiment.findings is not None:
        result.reproduced_verdict = k.kernel.validate_experiment.execute(experiment.experiment_id).verdict.status.value
    reproduced = k.kernel.query_evidence.manifest(experiment.experiment_id)
    assert reproduced is not None
    result.manifest = reproduced
    if result.reproduced_verdict != result.original_verdict:
        result.differences.append(f"verdict: {result.original_verdict} became {result.reproduced_verdict}")
    result.differences.extend(compare(manifest, reproduced, rtol, atol))
    return result


def write_outputs(result: Reproduction, out: Path) -> Path:
    """``reproduction.json`` and the reproduced manifest, laid out as the harness reads manifests."""
    out.mkdir(parents=True, exist_ok=True)
    report = out / "reproduction.json"
    report.write_text(json.dumps(result.to_document(), indent=2, sort_keys=True) + "\n")
    if result.manifest is not None:
        path = out / "experiments" / result.protocol.split("@")[0] / result.experiment_id / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.manifest, indent=2, sort_keys=True) + "\n")
    return report
