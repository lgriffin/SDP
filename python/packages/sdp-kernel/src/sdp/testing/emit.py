"""Run the synthetic protocol end to end on fakes and write what the harness reads (T6/T7 driver).

    python -m sdp.testing.emit <out-dir>

writes ``<out>/experiments/<protocol>/<experiment>/manifest.json`` for a VERIFIED, a REJECTED, a
CANDIDATE and a FAILED run, and ``<out>/ledger/ledger.jsonl`` with the signed entries. The harness
then checks every manifest conforms and re-derives every verdict (TST-007).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

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


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    for path in emit(Path(argv[0])):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
