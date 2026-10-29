"""Draft and decide acquisition plans on fakes and write what the harness re-derives (INT-013, INT-014).

    python -m sdp.testing.emit_plans <out-dir>

writes ``<out>/<name>.json`` for an automatically approved plan, one approved above the threshold
by a signed ledger entry, a refused one, a drafted one with gaps, and the JWST vertical slice's
plan (v0.4.0 §7) with the image test installed under the ``sdp.investigation_tests`` entry point. ``spec-harness rederive-plan``
then recomputes each plan's id, its minimum product set and gaps from the candidates it records,
its cost and its approval, and fails on any disagreement.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sdp.kernel.application.investigate import load_investigation_tests
from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.domain.integration import AcquisitionBudget, AcquisitionPlan
from sdp.kernel.ports.integration import ApprovePlanCommand, PlanInvestigationCommand
from sdp.testing.integration import gaia_descriptor, mast_descriptor
from sdp.testing.investigate import (
    ProperMotionExcessTest,
    gaia_archive,
    investigate_ports,
    observation_archive,
    planning_descriptors,
    planning_discovery,
    synthetic_catalogue,
    synthetic_field,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.jwst import GAIA, MAST, jwst_archive, jwst_catalogue, jwst_field, jwst_profile
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

LIGHTCURVES = ("lightcurve",)


def _kernel(*descriptors: object) -> InMemoryKernel:
    catalogue = synthetic_catalogue()
    return build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=investigate_ports(
            ProperMotionExcessTest(needs_products=LIGHTCURVES),
            catalogue=catalogue,
            observations=observation_archive(catalogue),
        ),
        discovery=planning_discovery(*descriptors),  # type: ignore[arg-type]
    )


def _plan(k: InMemoryKernel, threshold: int = 50_000_000) -> AcquisitionPlan:
    budget = AcquisitionBudget(1_000_000_000, 50, 3600.0, 0, threshold)
    assert k.kernel.plan_investigation is not None
    return k.kernel.plan_investigation.execute(
        PlanInvestigationCommand(synthetic_field(), synthetic_profile(needs_products=LIGHTCURVES), budget, seed=11)
    )


def _decide(k: InMemoryKernel, plan: AcquisitionPlan, **kw: object) -> AcquisitionPlan:
    assert k.kernel.approve_plan is not None
    return k.kernel.approve_plan.execute(ApprovePlanCommand(plan.plan_id, "leigh", **kw))  # type: ignore[arg-type]


def _jwst_slice() -> AcquisitionPlan:
    catalogue = jwst_catalogue()
    ports = investigate_ports(load_investigation_tests().get("image-counterpart", "1.0.0"), catalogue=catalogue)
    ports.observation_gateways = {MAST: jwst_archive(catalogue)}
    k = build_in_memory_kernel(
        gateways={GAIA: gaia_archive(catalogue)},
        investigate=ports,
        discovery=planning_discovery(mast_descriptor(), gaia_descriptor()),
    )
    assert k.kernel.plan_investigation is not None
    plan = k.kernel.plan_investigation.execute(
        PlanInvestigationCommand(jwst_field(), jwst_profile(), AcquisitionBudget(500_000_000, 10, 600.0, 0, 50_000_000))
    )
    return _decide(k, plan)


def emit(out: Path) -> list[Path]:
    plans: dict[str, AcquisitionPlan] = {}
    k = _kernel()
    plans["auto-approved"] = _decide(k, _plan(k))
    k = _kernel()
    big = _plan(k, threshold=1000)
    k.ledger.append(kind="approval", subject_id="plan", subject_hash=big.plan_id, signer="leigh", payload={})
    plans["signed-approval"] = _decide(k, big)
    k = _kernel()
    plans["refused"] = _decide(k, _plan(k), refuse_reason="not this field")
    gaia, _, _ = planning_descriptors()
    plans["drafted-with-gaps"] = _plan(_kernel(gaia))
    plans["jwst-slice"] = _jwst_slice()
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, plan in plans.items():
        path = out / f"{name}.json"
        path.write_text(canonical_json(plan.to_document()) + "\n")
        written.append(path)
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
