"""CTR-006: every domain-to-DTO mapper round-trips its type."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sdp.contracts import mappers
from sdp.kernel.ports.driving import AcquireDatasetCommand
from sdp.testing.catalogues import FAKE_GAIA, fixture_catalogue
from sdp.testing.fakes import FakeArchive, InMemoryLedger
from sdp.testing.synthetic import SyntheticLinearProtocol
from sdp.testing.wiring import build_in_memory_kernel

pytestmark = [pytest.mark.tier("T2"), pytest.mark.req("CTR-006")]


@settings(max_examples=25)
@given(st.floats(0.1, 5.0), st.text(min_size=1, max_size=30))
def test_predeclaration_round_trips(slope: float, title: str) -> None:
    from dataclasses import replace

    base = SyntheticLinearProtocol().predeclare()
    value = replace(base, title=title, parameters={**base.parameters, "slope": slope})
    back = mappers.predeclaration_from_dto(mappers.predeclaration_to_dto(value))
    assert back == value
    assert back.compute_hash() == value.compute_hash()


@pytest.mark.parametrize("planted", [2.0, 3.0])
def test_validation_report_round_trips(planted: float) -> None:
    from sdp.kernel.ports.driving import DefineExperimentCommand, FreezePredeclarationCommand
    from sdp.testing.synthetic import make_series

    protocol = SyntheticLinearProtocol(planted_slope=planted)
    k = build_in_memory_kernel(protocol)
    frozen, _ = k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(protocol.predeclare(), "leigh"))
    series = k.store.put(make_series(), "application/json")
    experiment = k.kernel.define_experiment.execute(
        DefineExperimentCommand("synthetic-linear", frozen.hash, {"series": series})
    )
    k.kernel.run_experiment.execute(experiment.experiment_id)
    report = k.kernel.validate_experiment.execute(experiment.experiment_id)
    assert mappers.report_from_dto(mappers.report_to_dto(report)) == report


def test_ledger_entry_round_trips() -> None:
    ledger = InMemoryLedger()
    entry = ledger.append(
        kind="predeclaration", subject_id="p-1", subject_hash="sha256:" + "a" * 64, signer="leigh", payload={"n": 1}
    )
    assert mappers.ledger_entry_from_dto(mappers.ledger_entry_to_dto(entry)) == entry
    assert entry.recorded_at <= datetime.now(UTC).replace(year=2100)


def test_dataset_artifacts_round_trip() -> None:
    k = build_in_memory_kernel(gateways={"fake-gaia": FakeArchive(FAKE_GAIA, fixture_catalogue())})
    from sdp.kernel.domain.archive import CatalogueQuery, Cone

    query = CatalogueQuery(
        "fake-gaia", "gaia:DR3", Cone(56.5, 24.25, 1.0), ("source_id", "ra", "dec"), row_cap=100, chunk_rows=100
    )
    result = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(query))
    for artifact in (result.raw, result.curated):
        assert artifact is not None
        assert mappers.dataset_from_dto(mappers.dataset_to_dto(artifact)) == artifact
