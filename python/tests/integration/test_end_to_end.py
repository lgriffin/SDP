"""T5 end to end: the production composition root on PostgreSQL, a NAS directory and a signed ledger.

The synthetic protocol is injected rather than discovered, so the run exercises every real adapter
without depending on a science module being installed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdp.adapters.bootstrap import Application, NotConfigured, Overrides, build_application
from sdp.adapters.settings import Settings
from sdp.kernel.domain.experiment import ExperimentState
from sdp.kernel.domain.verdict import VerdictStatus
from sdp.kernel.ports.driving import ApproveVerdictCommand, DefineExperimentCommand, FreezePredeclarationCommand
from sdp.testing.fakes import InMemoryProtocolRegistry
from sdp.testing.synthetic import SyntheticLinearProtocol, make_series

from .keys import write_signing_key

pytestmark = [pytest.mark.tier("T5"), pytest.mark.integration]


@pytest.fixture
def app(database_url: str, tmp_path: Path) -> Application:
    key, _ = write_signing_key(tmp_path, "leigh")
    settings = Settings.from_env(
        {
            "SDP_DATABASE_URL": database_url,
            "SDP_ARTIFACT_ROOT": str(tmp_path / "nas"),
            "SDP_LEDGER_DIR": str(tmp_path / "ledger"),
            "SDP_REPO_ROOT": str(tmp_path),
            "SDP_SIGNING_KEY": str(key),
            "SDP_ALLOWED_SIGNERS": str(tmp_path / "allowed_signers"),
            "SDP_METRICS_DIR": str(tmp_path / "metrics"),
            "SDP_MIN_FREE_BYTES": "0",
        }
    )
    overrides = Overrides(protocols=InMemoryProtocolRegistry.of(SyntheticLinearProtocol()))
    return build_application(settings, overrides, logging=False)


@pytest.mark.req("KRN-010", "TST-003")
def test_predeclare_run_validate_and_approve_on_real_adapters(app: Application, tmp_path: Path) -> None:
    kernel = app.kernel
    protocol = SyntheticLinearProtocol()
    frozen, entry = kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(protocol.predeclare(), "leigh"))
    assert entry.signature_method == "ssh"

    series = app.store.put(make_series(), "application/json")
    defined = kernel.define_experiment.execute(
        DefineExperimentCommand(protocol.protocol_id.name, frozen.hash, {"series": series}, seed=42)
    )
    ran = kernel.run_experiment.execute(defined.experiment_id)
    assert ran.state is ExperimentState.COMPLETED
    report = kernel.validate_experiment.execute(defined.experiment_id)
    assert report.verdict.status is VerdictStatus.VERIFIED

    approval = kernel.publish_bundle.approve(ApproveVerdictCommand(defined.experiment_id, "leigh"))
    assert approval.kind == "approval"
    assert app.ledger.verify() == []

    manifest = kernel.query_evidence.manifest(defined.experiment_id)
    assert manifest is not None and manifest["verdict"]["status"] == "VERIFIED"
    assert manifest["environment"]["backend"] == "cpu"

    names = [stored.event.name for stored in kernel.query_evidence.events()]
    for expected in ("PredeclarationFrozen", "ExperimentDefined", "ExperimentCompleted", "VerdictIssued"):
        assert expected in names

    # A second application on the same storage sees the same evidence: nothing lived only in memory.
    again = build_application(app.settings, Overrides(protocols=InMemoryProtocolRegistry.of(protocol)), logging=False)
    stored = again.kernel.query_evidence.experiment(defined.experiment_id)
    assert stored is not None and stored.state is ExperimentState.APPROVED
    assert json.loads(app.store.get(series)) == json.loads(make_series())
    assert list((tmp_path / "metrics").glob("*.prom"))


def test_unconfigured_ports_refuse_instead_of_pretending(app: Application) -> None:
    with pytest.raises(NotConfigured):
        app.kernel.ports.publisher.publish(b"", "bundle.zip", {})
    with pytest.raises(NotConfigured):
        app.kernel.ports.curator.dialect_for("gaia", "DR3")
