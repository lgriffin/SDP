"""T6, Python side: every v0.4.0 domain document the kernel writes conforms to its contract schema,
and the descriptor and compatibility documents round-trip (v0.4.0 §9, §12 T6)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sdp import contracts
from sdp.kernel.domain.integration import (
    AdapterCompatibilityRecord,
    ScientificDataProduct,
    SemanticCheck,
    SemanticValidationReport,
    SourceCapabilityDescriptor,
)
from sdp.testing.fakes.integration import PlantedAssociations
from sdp.testing.integration import (
    SHA,
    association_policy,
    compatibility,
    dasch_descriptor,
    image_metadata,
    mast_descriptor,
    planted_field,
    sample_plan,
)

pytestmark = [pytest.mark.tier("T6"), pytest.mark.req("CTR-003")]


def _ok(name: str, doc: object) -> None:
    errors = contracts.validate(name, doc)
    assert errors == [], errors
    contracts.load(name, doc)


def test_descriptors_conform_and_round_trip() -> None:
    for d in (mast_descriptor(), dasch_descriptor()):
        _ok("capability-descriptor", d.to_document())
        assert SourceCapabilityDescriptor.from_document(d.to_document()) == d


def test_products_conform() -> None:
    m = image_metadata()
    rep = SemanticValidationReport(m.product_id, "v", "1.0.0", (SemanticCheck("frame", True),))
    _ok("scientific-data-product", ScientificDataProduct(SHA, m).validated(rep).to_document())


def test_association_graphs_conform() -> None:
    gaia, dets = planted_field()
    graph = PlantedAssociations({"wise-b": (("obj-b1", 0.6), ("obj-b2", 0.4))}).associate(
        [*gaia, *dets], association_policy()
    )
    _ok("association-graph", graph.to_document())


def test_plans_conform_drafted_and_approved() -> None:
    _ok("acquisition-plan", sample_plan().to_document())
    _ok("acquisition-plan", sample_plan(image_bytes=50_000_000).decide(actor="l", ledger_entry_id="e").to_document())


def test_compatibility_records_conform_and_round_trip() -> None:
    rec = compatibility()
    _ok("adapter-compatibility", rec.to_document())
    assert AdapterCompatibilityRecord.from_document(rec.to_document()) == rec


@settings(max_examples=40)
@given(st.integers(0, 900_000_000), st.integers(0, 900_000_000))
def test_plan_hashing_is_canonical(image_bytes: int, threshold: int) -> None:
    """T2: the plan id depends only on content, and approving never changes it."""
    threshold = min(threshold, 1_000_000_000)
    plan = sample_plan(image_bytes=image_bytes, threshold=threshold)
    assert plan.plan_id == sample_plan(image_bytes=image_bytes, threshold=threshold).plan_id
    decided = plan.decide(actor="l", ledger_entry_id="e")
    assert decided.plan_id == plan.plan_id
    _ok("acquisition-plan", decided.to_document())
