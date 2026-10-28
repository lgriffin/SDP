"""Run protocols end to end on fakes and write what the harness reads (T6/T7 driver).

    python -m sdp.testing.emit <out-dir>

writes ``<out>/experiments/<protocol>/<experiment>/manifest.json`` for a VERIFIED, a REJECTED, a
CANDIDATE and a FAILED run, and ``<out>/ledger/ledger.jsonl`` with the signed entries. The harness
then checks every manifest conforms and re-derives every verdict (TST-007).

    python -m sdp.testing.emit <out-dir> --protocol NAME --input ROLE=FILE ... [--param KEY=JSON ...]

runs one installed protocol (found through the sdp.protocols entry points, as production does) on
the given input files and writes its manifest the same way, printing the path and the verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sdp.kernel.application.plugins import EntryPointProtocolRegistry
from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.ports.driving import (
    ApproveVerdictCommand,
    AssembleDossierCommand,
    DefineExperimentCommand,
    FreezePredeclarationCommand,
)
from sdp.testing.synthetic import SyntheticLinearProtocol, make_series
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel


def _run(k: InMemoryKernel, protocol: SyntheticLinearProtocol, *, noise: float = 0.1, fail: str | None = None) -> str:
    frozen, _ = k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(protocol.predeclare(), "leigh"))
    series = k.store.put(make_series(noise=noise), "application/json")
    params: dict[str, Any] = {"inject_failure": fail} if fail else {}
    experiment = k.kernel.define_experiment.execute(
        DefineExperimentCommand("synthetic-linear", frozen.hash, {"series": series}, params, seed=42)
    )
    experiment = k.kernel.run_experiment.execute(experiment.experiment_id)
    if fail is None:
        k.kernel.validate_experiment.execute(experiment.experiment_id)
    return experiment.experiment_id


def emit(out: Path) -> list[Path]:
    written: list[Path] = []
    for name, protocol, noise, fail, approve in (
        ("verified", SyntheticLinearProtocol(), 0.1, None, True),
        ("rejected", SyntheticLinearProtocol(planted_slope=3.0), 0.1, None, False),
        ("candidate", SyntheticLinearProtocol(), 0.8, None, False),
        ("failed", SyntheticLinearProtocol(), 0.1, "residuals", False),
    ):
        k = build_in_memory_kernel(protocol)
        experiment_id = _run(k, protocol, noise=noise, fail=fail)
        if approve:
            k.kernel.assemble_dossier.execute(AssembleDossierCommand(experiment_id, ("Synthetic data only",)))
            k.kernel.publish_bundle.approve(ApproveVerdictCommand(experiment_id, "reviewer"))
        manifest = k.kernel.query_evidence.manifest(experiment_id)
        assert manifest is not None
        path = out / "experiments" / "synthetic-linear" / name / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        written.append(path)
        ledger = out / "ledger" / f"{name}.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("".join(canonical_json(e.to_document()) + "\n" for e in k.ledger.entries()))
    return written


def _media_type(path: Path) -> str:
    return "application/vnd.apache.parquet" if path.suffix == ".parquet" else "application/json"


def emit_protocol(
    out: Path, protocol_name: str, inputs: dict[str, Path], params: dict[str, Any], seed: int = 1919
) -> tuple[Path, str]:
    protocol = EntryPointProtocolRegistry().get(protocol_name)
    k = build_in_memory_kernel(protocol)
    frozen, _ = k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(protocol.predeclare(), "ci"))
    artifacts = {role: k.store.put(path.read_bytes(), _media_type(path)) for role, path in inputs.items()}
    experiment = k.kernel.define_experiment.execute(
        DefineExperimentCommand(protocol_name, frozen.hash, artifacts, params, seed=seed)
    )
    experiment = k.kernel.run_experiment.execute(experiment.experiment_id)
    verdict = "FAILED"
    if experiment.findings is not None:
        verdict = k.kernel.validate_experiment.execute(experiment.experiment_id).verdict.status.value
    manifest = k.kernel.query_evidence.manifest(experiment.experiment_id)
    assert manifest is not None
    path = out / "experiments" / protocol_name / experiment.experiment_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return path, verdict


def _pairs(values: list[str], what: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for value in values:
        key, sep, rest = value.partition("=")
        if not sep:
            raise SystemExit(f"--{what} expects KEY=VALUE, got {value!r}")
        out[key] = rest
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m sdp.testing.emit", description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--protocol")
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--param", action="append", default=[])
    parser.add_argument("--seed", type=int, default=1919)
    args = parser.parse_args(argv)
    if args.protocol is None:
        for path in emit(args.out):
            print(path)
        return 0
    inputs = {role: Path(p) for role, p in _pairs(args.input, "input").items()}
    params = {key: json.loads(v) for key, v in _pairs(args.param, "param").items()}
    path, verdict = emit_protocol(args.out, args.protocol, inputs, params, args.seed)
    print(path)
    print(verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
