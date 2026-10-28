"""ScientificProtocol contract: the plugin port is a versioned public API (ADR-001, KRN-011)."""

from __future__ import annotations

from typing import Any

import pytest

from sdp.kernel.domain.findings import Findings
from sdp.kernel.domain.verdict import VerdictStatus
from sdp.kernel.science.io import ComputeResults, ResolvedInputs
from sdp.kernel.science.plan import validate_plan
from sdp.kernel.science.protocol import ScientificProtocol
from sdp.testing.contracts.compute import RecordingSink
from sdp.testing.fakes.compute import SynchronousExecutor


class ScientificProtocolContract:
    """Run with ``protocol_case``: (protocol, resolved inputs, params, seed)."""

    def test_implements_the_port(
        self, protocol_case: tuple[ScientificProtocol, ResolvedInputs, dict[str, Any], int]
    ) -> None:
        protocol, *_ = protocol_case
        assert isinstance(protocol, ScientificProtocol)

    def test_predeclaration_is_pure_and_hashable(
        self, protocol_case: tuple[ScientificProtocol, ResolvedInputs, dict[str, Any], int]
    ) -> None:
        protocol, *_ = protocol_case
        a, b = protocol.predeclare(), protocol.predeclare()
        assert a.compute_hash() == b.compute_hash()
        assert a.protocol_id == protocol.protocol_id.name and a.protocol_version == protocol.protocol_id.version

    @pytest.mark.req("KRN-012")
    def test_every_planned_task_kind_has_a_cpu_reference(
        self, protocol_case: tuple[ScientificProtocol, ResolvedInputs, dict[str, Any], int]
    ) -> None:
        protocol, inputs, params, seed = protocol_case
        validate_plan(protocol.plan(inputs, params, seed), protocol.task_kinds())

    def test_plan_is_deterministic(
        self, protocol_case: tuple[ScientificProtocol, ResolvedInputs, dict[str, Any], int]
    ) -> None:
        protocol, inputs, params, seed = protocol_case
        assert protocol.plan(inputs, params, seed) == protocol.plan(inputs, params, seed)

    @pytest.mark.req("KRN-008")
    def test_validate_evaluates_every_predeclared_gate(
        self, protocol_case: tuple[ScientificProtocol, ResolvedInputs, dict[str, Any], int]
    ) -> None:
        protocol, inputs, params, seed = protocol_case
        plan = protocol.plan(inputs, params, seed)
        values = SynchronousExecutor().execute(
            plan, protocol.task_kinds(), seed=seed, sink=RecordingSink(), services={"inputs": inputs}
        )
        wanted = plan.outputs or tuple(t.task_id for t in plan.tasks)
        findings = protocol.interpret(ComputeResults({k: values[k] for k in wanted}))
        assert isinstance(findings, Findings)
        predeclaration = protocol.predeclare()
        report = protocol.validate(findings, predeclaration, experiment_id="exp-contract")
        assert [c.gate_id for c in report.checks] == [g.gate_id for g in predeclaration.gates]
        assert report.verdict.status in set(VerdictStatus)
        assert report.predeclaration_hash == predeclaration.compute_hash()
