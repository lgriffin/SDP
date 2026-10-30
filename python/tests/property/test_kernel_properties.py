"""T2 property tests: invariants that must hold for every input, not just the examples."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra import numpy as hnp

from sdp.kernel.application import codec
from sdp.kernel.domain.canonical import canonical_json, es_number, hash_document
from sdp.kernel.domain.findings import Finding, Findings, OracleStatus
from sdp.kernel.domain.gates import CheckOutcome, Comparator, Gate, Severity, evaluate_gates
from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.domain.predeclaration import Predeclaration
from sdp.kernel.domain.provenance import ProvGraph
from sdp.kernel.domain.verdict import VerdictStatus, decide_verdict
from sdp.kernel.science.plan import ComputePlan, ComputeTask
from sdp.testing.synthetic import SyntheticLinearProtocol

pytestmark = pytest.mark.tier("T2")

finite = st.floats(allow_nan=False, allow_infinity=False)
keys = st.text(min_size=1, max_size=8)
json_values = st.recursive(
    st.none() | st.booleans() | st.integers(-(2**53), 2**53) | finite | st.text(max_size=12),
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(keys, inner, max_size=4),
    max_leaves=20,
)


@given(finite)
def test_es_number_round_trips(x: float) -> None:
    assert float(es_number(x)) == x


@given(json_values)
def test_canonical_json_parses_back_to_the_same_value(value: Any) -> None:
    # JCS writes integral floats without a fraction, so read every number back as a float
    text = canonical_json(value)
    assert json.loads(text, parse_int=float) == as_floats(value)
    assert canonical_json(json.loads(text, parse_int=float)) == text


def as_floats(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, list):
        return [as_floats(v) for v in value]
    return {k: as_floats(v) for k, v in value.items()}


@given(st.dictionaries(keys, json_values, min_size=1, max_size=6), st.randoms())
def test_hash_ignores_key_order(document: dict[str, Any], rnd: Any) -> None:
    items = list(document.items())
    rnd.shuffle(items)
    assert hash_document(dict(items)) == hash_document(document)


arrays = hnp.arrays(
    dtype=st.sampled_from([np.float64, np.float32, np.int64, np.int32, np.bool_]),
    shape=hnp.array_shapes(max_dims=3, max_side=5),
    elements=None,
)


@given(arrays)
def test_codec_round_trips_arrays_bit_for_bit(array: np.ndarray) -> None:
    back = codec.loads(codec.dumps({"a": array}))["a"]
    assert back.dtype == array.dtype and back.shape == array.shape
    assert back.tobytes() == array.tobytes()


#: Plain keys only: hypothesis draws string constants from the code under test, so it finds the codec's
#: reserved "__sdp__" tag, which the codec refuses by design (test_application_records covers that).
plain_keys = keys.filter(lambda key: key != "__sdp__")


@given(
    st.dictionaries(
        plain_keys,
        st.one_of(finite, st.integers(-(2**53), 2**53), st.text(max_size=8), st.tuples(finite, finite)),
        max_size=6,
    )
)
def test_codec_round_trips_plain_values(value: dict[str, Any]) -> None:
    back = codec.loads(codec.dumps(value))
    assert back == value
    assert {k: type(v) for k, v in back.items()} == {k: type(v) for k, v in value.items()}


@given(st.sampled_from([math.inf, -math.inf]))
def test_codec_keeps_non_finite_floats(x: float) -> None:
    assert codec.loads(codec.dumps(x)) == x


comparators = st.sampled_from(list(Comparator))
severities = st.sampled_from(list(Severity))


@st.composite
def gates_and_findings(draw: st.DrawFn) -> tuple[tuple[Gate, ...], Findings]:
    n = draw(st.integers(1, 6))
    gates = []
    findings = []
    oracles = {}
    for i in range(n):
        oracle = draw(st.sampled_from([(), (f"o{i}",)]))
        gates.append(
            Gate(f"PRP-G-{i:02d}", "property gate", f"f{i}", draw(comparators), draw(finite), draw(severities), oracle)
        )
        if draw(st.booleans()):
            findings.append(Finding(f"f{i}", draw(finite)))
        if oracle:
            oracles[oracle[0]] = OracleStatus(oracle[0], draw(st.booleans()), "property oracle")
    return tuple(gates), Findings.of(*findings, oracles=oracles)


@given(gates_and_findings(), st.randoms())
def test_verdict_follows_the_rule_and_ignores_gate_order(case: tuple[tuple[Gate, ...], Findings], rnd: Any) -> None:
    gates, findings = case
    checks = evaluate_gates(gates, findings)
    status, _ = decide_verdict(checks)
    blocking = [c for c in checks if c.severity is Severity.BLOCKING]
    if any(c.outcome is CheckOutcome.FAIL for c in blocking):
        assert status is VerdictStatus.REJECTED
    elif any(c.outcome is not CheckOutcome.PASS for c in blocking):
        assert status is VerdictStatus.INCONCLUSIVE
    elif any(c.outcome is not CheckOutcome.PASS for c in checks):
        assert status is VerdictStatus.CANDIDATE
    else:
        assert status is VerdictStatus.VERIFIED
    shuffled = list(gates)
    rnd.shuffle(shuffled)
    assert decide_verdict(evaluate_gates(tuple(shuffled), findings))[0] is status


@given(gates_and_findings())
def test_unverified_oracles_never_yield_verified(case: tuple[tuple[Gate, ...], Findings]) -> None:
    gates, findings = case
    checks = evaluate_gates(gates, findings)
    if any(c.oracle_ids and not c.oracles_verified for c in checks if c.severity is Severity.BLOCKING):
        assert decide_verdict(checks)[0] is not VerdictStatus.VERIFIED


@st.composite
def dags(draw: st.DrawFn) -> ComputePlan:
    n = draw(st.integers(1, 12))
    policy = NumericalPolicy(rtol=1e-9, atol=0.0)
    tasks = []
    for i in range(n):
        deps = draw(st.sets(st.integers(0, i - 1), max_size=3)) if i else set()
        tasks.append(ComputeTask(f"t{i}", "k", policy, tuple(f"t{d}" for d in sorted(deps))))
    order = draw(st.permutations(tasks))
    return ComputePlan(tuple(order))


@given(dags())
def test_topological_order_respects_dependencies_and_is_deterministic(plan: ComputePlan) -> None:
    order = plan.topological_order()
    position = {t.task_id: i for i, t in enumerate(order)}
    assert sorted(position) == sorted(t.task_id for t in plan.tasks)
    for task in plan.tasks:
        assert all(position[d] < position[task.task_id] for d in task.depends_on)
    assert plan.topological_order() == order


@pytest.mark.req("CTR-006")
@settings(max_examples=30)
@given(st.floats(0.001, 10.0), st.text(min_size=1, max_size=20))
def test_predeclaration_document_round_trips(slope: float, title: str) -> None:
    from dataclasses import replace

    base = SyntheticLinearProtocol().predeclare()
    predeclaration = replace(base, title=title, parameters={**base.parameters, "slope": slope})
    again = Predeclaration.from_document(predeclaration.to_document())
    assert again.to_document() == predeclaration.to_document()
    assert again.compute_hash() == predeclaration.compute_hash()


@given(st.lists(st.tuples(st.sampled_from(["a", "b", "c", "d"]), st.sampled_from(["x", "y"])), max_size=8))
def test_prov_json_round_trips(edges: list[tuple[str, str]]) -> None:
    graph = ProvGraph()
    for entity, activity in edges:
        graph.entity(f"sha256:{entity * 64}"[:71])
        graph.activity(f"run/{activity}")
        graph.relate("wasGeneratedBy", f"sha256:{entity * 64}"[:71], f"run/{activity}")
    document = graph.to_prov_json()
    assert ProvGraph.from_prov_json(document).to_prov_json() == document
