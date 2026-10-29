"""T1: gates, findings, verdicts, dossiers, quantities, pooling and historical records as stored and
refused. Each test pins a detail mutation testing showed nothing else checked (docs/testing.md,
"Killing a survivor")."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import astropy.units as u
import pytest

from sdp.kernel.domain.dossier import Dossier, Figure
from sdp.kernel.domain.errors import (
    InvalidTransition,
    InvariantViolation,
    PoolingNotJustified,
    ProbableErrorNotConverted,
)
from sdp.kernel.domain.findings import Finding, Findings, OracleStatus
from sdp.kernel.domain.gates import Check, CheckOutcome, Comparator, Gate, Severity, evaluate_gate
from sdp.kernel.domain.historical import HistoricalDocument, TranscribedRecord, VerificationRecord, is_verified
from sdp.kernel.domain.ids import ArtifactId, ProtocolId, semver_major
from sdp.kernel.domain.measurement import Measurement, UncertaintyConvention, least_squares_weight
from sdp.kernel.domain.pooling import PoolingJustification, require_pooling_justification
from sdp.kernel.domain.prediction import Prediction
from sdp.kernel.domain.quantity import PhysicalQuantity, quantity_document, quantity_from_document, require_quantity
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.domain.verdict import ValidationReport, Verdict, VerdictStatus
from sdp.testing.contracts.metadata import sample_experiment

pytestmark = pytest.mark.tier("T1")

AT = datetime(2026, 10, 28, 12, tzinfo=UTC)
A = ArtifactId("sha256:" + "a" * 64)
B = ArtifactId("sha256:" + "b" * 64)
PLATES = SourceRef(
    SourceKind.DOCUMENT, "dyson-1920", "public domain", "Dyson et al. 1920", doi="10.1098/rsta.1920.0009"
)


def exactly(error: type[Exception], message: str) -> Any:
    return pytest.raises(error, match="^" + re.escape(message) + "$")


def violation(message: str) -> Any:
    return exactly(InvariantViolation, message)


# -- Gates and checks -------------------------------------------------------------------------------


def test_gate_refusals_name_the_gate() -> None:
    with violation("gate id must look like REL-G-01, got 'gate-1'"):
        Gate("gate-1", "d", "f", Comparator.LE, 1.0)
    with violation("REL-G-01: a boolean threshold only supports eq/ne"):
        Gate("REL-G-01", "d", "f", Comparator.LE, True)
    bare = {
        "gate_id": "REL-G-01",
        "description": "d",
        "finding": "f",
        "comparator": "le",
        "threshold": 1.0,
        "severity": "warning",
    }
    assert Gate.from_document(bare) == Gate("REL-G-01", "d", "f", Comparator.LE, 1.0, Severity.WARNING)


def test_a_check_keeps_the_finding_value_and_unit() -> None:
    gate = Gate("REL-G-01", "deflection", "deflection", Comparator.LE, 2.0)
    check = evaluate_gate(gate, Findings.of(Finding("deflection", 1.75, 0.1, "arcsec")))
    assert (check.value, check.unit, check.outcome) == (1.75, "arcsec", CheckOutcome.PASS)
    assert Check.from_document(check.to_document()) == check
    doc = check.to_document()
    del doc["unit"]
    assert Check.from_document(doc).unit is None
    as_int = evaluate_gate(gate, Findings.of(Finding("deflection", 3)))
    assert as_int.value == 3.0 and isinstance(as_int.value, float) and as_int.outcome is CheckOutcome.FAIL


def test_a_non_scalar_finding_is_missing() -> None:
    gate = Gate("REL-G-01", "deflection", "deflection", Comparator.LE, 2.0)
    for value in ("1.75", [1.75], {"v": 1.75}):
        check = evaluate_gate(gate, Findings.of(Finding("deflection", value, unit="arcsec")))
        assert (check.outcome, check.value, check.unit) == (CheckOutcome.MISSING, None, "arcsec")
    absent = evaluate_gate(gate, Findings())
    assert (absent.outcome, absent.value, absent.unit) == (CheckOutcome.MISSING, None, None)


def test_semver_major_and_protocol_names() -> None:
    assert semver_major("2.5.1") == 2
    with violation("protocol name must be a lowercase slug: 'UC02'"):
        ProtocolId("UC02", "1.0.0")


# -- Findings and verdicts --------------------------------------------------------------------------


def test_finding_refusals_and_document() -> None:
    with violation("a finding needs a name"):
        Finding("", 1.0)
    with violation("finding 'chi2' is not finite"):
        Finding("chi2", float("inf"))
    full = Finding("deflection", 1.75, 0.1, "arcsec")
    assert Finding.from_document(full.to_document()) == full
    assert Finding.from_document({"name": "n", "value": 2}) == Finding("n", 2)


def test_findings_refuse_duplicates_and_keep_oracles_and_narrative() -> None:
    with violation("duplicate finding 'chi2'"):
        Findings.of(Finding("chi2", 1.0), Finding("chi2", 2.0))
    findings = Findings.of(Finding("chi2", 1.0), oracles={"o": OracleStatus("o", True)}, narrative={"summary": "fine"})
    assert "chi2" in findings and "rms" not in findings
    assert findings.oracle_verified("o") and findings.narrative == {"summary": "fine"}


def _check(gate_id: str, outcome: CheckOutcome, severity: Severity = Severity.BLOCKING) -> Check:
    return Check(gate_id, "f", 1.0, None, Comparator.LE, 2.0, severity, (), True, outcome)


def test_every_verdict_gives_its_reasons() -> None:
    def verdict(*checks: Check) -> tuple[VerdictStatus, tuple[str, ...]]:
        v = ValidationReport.from_checks(
            experiment_id="e", protocol_id="p", protocol_version="1.0.0", predeclaration_hash="h", checks=checks
        ).verdict
        return v.status, v.reasons

    assert verdict() == (VerdictStatus.INCONCLUSIVE, ("no gates were evaluated",))
    assert verdict(_check("REL-G-01", CheckOutcome.PASS)) == (VerdictStatus.VERIFIED, ("all gates passed",))
    assert verdict(_check("REL-G-01", CheckOutcome.MISSING), _check("REL-G-02", CheckOutcome.ORACLE_UNVERIFIED)) == (
        VerdictStatus.INCONCLUSIVE,
        ("blocking gate REL-G-01 has no finding", "blocking gate REL-G-02 uses an unverified oracle"),
    )


def test_a_report_must_agree_with_its_verdict() -> None:
    report = ValidationReport.from_checks(
        experiment_id="e", protocol_id="p", protocol_version="1.0.0", predeclaration_hash="h", checks=()
    )
    assert report.protocol_id == "p"
    assert report.to_document()["schema_version"] == "1.0.0"
    with violation("verdict VERIFIED does not follow from the checks (expected INCONCLUSIVE)"):
        replace(report, verdict=Verdict(VerdictStatus.VERIFIED, "1.0.0", ()))
    with violation("verdict and report disagree on the protocol version"):
        replace(report, verdict=Verdict(VerdictStatus.INCONCLUSIVE, "2.0.0", ()))
    with pytest.raises(KeyError, match="'REL-G-09'"):
        report.check("REL-G-09")


# -- Dossiers ---------------------------------------------------------------------------------------


def _validated() -> Any:
    from sdp.kernel.domain.gates import evaluate_gates
    from sdp.testing.synthetic import SyntheticLinearProtocol

    exp = sample_experiment()
    exp.start(ledger_entry_id="led-1", at=AT)
    findings = Findings.of(Finding("slope_abs_error", 0.001), Finding("residual_rms", 0.1))
    exp.complete(findings, {}, AT)
    report = ValidationReport.from_checks(
        experiment_id=exp.experiment_id,
        protocol_id="synthetic-linear",
        protocol_version="1.0.0",
        predeclaration_hash=exp.definition.predeclaration_hash,
        checks=evaluate_gates(SyntheticLinearProtocol().predeclare().gates, findings),
    )
    exp.validate(report, AT)
    exp.manifest_id = A
    return exp


def test_a_dossier_carries_the_experiment_record() -> None:
    exp = _validated()
    exp.approve(ledger_entry_id="led-2", reviewer="rev", at=AT)
    figure = Figure("residuals", B, "Residuals of the fit", "image/png")
    dossier = Dossier.assemble(
        dossier_id="dos-1", experiment=exp, limitations=("synthetic",), figures=(figure,), sections={"method": "OLS"}
    )
    doc = dossier.to_document()
    assert doc["dossier_id"] == "dos-1" and doc["verdict"] == exp.report.verdict.status.value
    assert doc["figures"] == [
        {"name": "residuals", "artifact_id": B.value, "caption": "Residuals of the fit", "media_type": "image/png"}
    ]
    assert doc["sections"] == {"method": "OLS"}
    assert doc["approvals"] == [{"ledger_entry_id": "led-2", "reviewer": "rev", "recorded_at": AT.isoformat()}]
    plain = Dossier.assemble(dossier_id="dos-2", experiment=exp, limitations=())
    assert plain.sections == {} and plain.figures == ()


def test_a_dossier_needs_a_validated_experiment_with_its_record() -> None:
    running = sample_experiment()
    running.start(ledger_entry_id="led-1", at=AT)
    with exactly(InvalidTransition, "a dossier needs a validated experiment, exp-contract is running"):
        Dossier.assemble(dossier_id="d", experiment=running, limitations=())
    for missing in ("manifest_id", "report", "findings"):
        exp = _validated()
        setattr(exp, missing, None)
        with exactly(InvalidTransition, "a dossier needs the experiment's manifest, findings and report"):
            Dossier.assemble(dossier_id="d", experiment=exp, limitations=())


# -- Quantities and predictions ---------------------------------------------------------------------


def test_quantity_refusals_and_documents() -> None:
    with violation("uncertainty must be an astropy Quantity, got float"):
        require_quantity(1.0, "uncertainty")
    with violation("value must be an astropy Quantity, got int"):
        require_quantity(1)
    with violation("a dimensionless quantity must be declared dimensionless=True"):
        PhysicalQuantity(1.0 * u.dimensionless_unscaled)
    with violation("declared dimensionless but carries unit arcsec"):
        PhysicalQuantity(1.0 * u.arcsec, dimensionless=True)
    q = PhysicalQuantity(1.75 * u.arcsec)
    assert q.to(u.deg).value == pytest.approx(1.75 / 3600)
    assert q.to_document() == {"value": 1.75, "unit": "arcsec"}
    back = quantity_from_document(quantity_document(1.75 * u.arcsec))
    assert back.unit == u.arcsec and back.value == 1.75


def test_prediction_document_round_trips_and_defaults() -> None:
    p = Prediction("gr", "1.0.0", {"gamma": 1.0}, 1.75 * u.arcsec, ("thin lens",))
    again = Prediction.from_document(p.to_document())
    assert again.predicted is not None and again.predicted.unit == u.arcsec and again.predicted.value == 1.75
    assert (again.parameters, again.geometry_assumptions) == ({"gamma": 1.0}, ("thin lens",))
    bare = Prediction.from_document({"model_id": "gr", "model_version": "1.0.0"})
    assert (bare.parameters, bare.predicted, bare.geometry_assumptions) == ({}, None, ())
    assert Prediction("gr", "1.0.0").to_document()["predicted"] is None


# -- Pooling ----------------------------------------------------------------------------------------


def test_pooling_refusals_say_why() -> None:
    with violation("pooling needs at least two observation sets"):
        PoolingJustification(("sobral",), "same plates", True)
    with violation("a pooling justification needs a reason"):
        PoolingJustification(("sobral", "principe"), " ", True)
    both = PoolingJustification(("sobral", "principe"), "same instrument", True)
    assert both.covers(("sobral", "principe")) and both.covers(("sobral",))
    assert require_pooling_justification(["principe", "sobral"], both) is both
    with exactly(PoolingNotJustified, "pooling ['principe', 'sobral', 'x'] needs a PoolingJustification"):
        require_pooling_justification(["sobral", "x", "principe"], both)
    with exactly(PoolingNotJustified, "pooling ['a', 'b'] needs a PoolingJustification"):
        require_pooling_justification(["b", "a"], None)
    with exactly(PoolingNotJustified, "a pooling justification must be predeclared"):
        require_pooling_justification(["sobral"], replace(both, predeclared=False))


# -- Historical records and measurements ------------------------------------------------------------


def test_historical_records_need_a_document_and_a_locator() -> None:
    HistoricalDocument("dyson-1920", PLATES, "A determination of the deflection of light")
    archive = SourceRef(SourceKind.ARCHIVE, "gaia", "x", "y", release_label="gaia:DR3")
    with violation("historical documents have a document SourceRef"):
        HistoricalDocument("gaia", archive, "Gaia")
    with violation("record r-1 needs a page or table locator (REL-001)"):
        TranscribedRecord("r-1", "dyson-1920", " ", {}, "leigh", AT)


def test_only_the_latest_pass_of_this_record_counts() -> None:
    record = TranscribedRecord("r-1", "dyson-1920", "p. 291, table 3", {"dx": 1.0}, "leigh", AT)

    def at(hour: int) -> datetime:
        return datetime(2026, 10, 28, hour, tzinfo=UTC)

    def passes(*marks: tuple[str, int, bool]) -> tuple[VerificationRecord, ...]:
        return tuple(VerificationRecord(rid, "leigh", at(h), ok) for rid, h, ok in marks)

    assert not is_verified(record, ())
    assert is_verified(record, passes(("r-1", 9, True)))
    assert is_verified(record, passes(("r-1", 11, True), ("r-1", 9, False)))  # by time, not by order
    assert not is_verified(record, passes(("r-1", 9, True), ("r-1", 11, False)))
    assert is_verified(record, passes(("r-1", 9, False), ("r-1", 10, False), ("r-1", 11, True)))
    assert not is_verified(record, passes(("r-1", 9, True), ("r-1", 10, True), ("r-1", 11, False)))
    assert not is_verified(record, passes(("r-2", 9, True)))
    assert is_verified(record, passes(("r-1", 9, True), ("r-2", 11, False)))


def _measurement(**changes: Any) -> Measurement:
    fields: dict[str, Any] = {
        "value": 1.98 * u.arcsec,
        "uncertainty": 0.12 * u.arcsec,
        "convention": UncertaintyConvention.STANDARD,
        "source": PLATES,
        "locator": "p. 291, table 3",
    }
    fields.update(changes)
    return Measurement(**fields)


def test_measurement_refusals_say_why() -> None:
    with violation("value must be an astropy Quantity, got float"):
        _measurement(value=1.98)
    with violation("uncertainty must be an astropy Quantity, got float"):
        _measurement(uncertainty=0.12)
    with violation("value and uncertainty must share a physical dimension"):
        _measurement(uncertainty=0.12 * u.m)
    with violation("uncertainty must be non-negative"):
        _measurement(uncertainty=-0.1 * u.arcsec)
    with violation("every measurement needs a page or table locator (REL-001)"):
        _measurement(locator=" ")
    exact = _measurement(uncertainty=0.0 * u.arcsec)  # an exact value is allowed, but cannot weight a fit
    with violation("zero uncertainty at 'p. 291, table 3' cannot weight a fit"):
        least_squares_weight(exact, u.arcsec)
    probable = _measurement(convention=UncertaintyConvention.PROBABLE_ERROR)
    with exactly(
        ProbableErrorNotConverted, "measurement at 'p. 291, table 3' carries a probable error; call to_standard_error()"
    ):
        least_squares_weight(probable, u.arcsec)
    assert least_squares_weight(_measurement(), u.arcsec) == pytest.approx(1 / 0.12**2)
