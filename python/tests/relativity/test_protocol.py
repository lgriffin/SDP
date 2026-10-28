"""T8: UC-02 end to end on the in-memory kernel, and the plugin contract.

Synthetic bundles cannot verify the 1920 published estimates or the 2020 reanalysis, so the
designed verdict is INCONCLUSIVE with those two gates oracle_unverified; every other gate is
exercised by planting the fault it guards against.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.domain.verdict import VerdictStatus
from sdp.kernel.ports.driving import AssembleDossierCommand
from sdp.kernel.science.io import ResolvedInput, ResolvedInputs
from sdp.testing.contracts.scientific_protocol import ScientificProtocolContract
from sdp_relativity.dossier import limitations
from sdp_relativity.protocol import Einstein1919Protocol
from sdp_relativity.synthetic import dumps, make_bundle

from .conftest import RunFactory, independent_fit

pytestmark = pytest.mark.tier("T8")

GATES = [f"REL-G-0{i}" for i in range(1, 10)]


@pytest.mark.req("REL-005")
@pytest.mark.req("REL-006")
@pytest.mark.gate("REL-G-03")
@pytest.mark.gate("REL-G-04")
@pytest.mark.gate("REL-G-06")
@pytest.mark.gate("REL-G-07")
def test_a_clean_synthetic_run_is_inconclusive_only_for_want_of_verified_oracles(run_uc02: RunFactory) -> None:
    run = run_uc02()
    assert run.report.verdict.status is VerdictStatus.INCONCLUSIVE
    outcomes = {g: run.outcome(g) for g in GATES}
    assert {g for g, o in outcomes.items() if o != "pass"} == {"REL-G-04", "REL-G-07"}
    assert outcomes["REL-G-04"] == outcomes["REL-G-07"] == "oracle_unverified"
    assert run.finding("hypotheses_missing_count") == 0
    assert run.finding("sensitivity_scenarios_missing") == 0
    assert run.finding("alpha_sobral_4in_arcsec") == pytest.approx(1.75, abs=0.6)


@pytest.mark.req("REL-001")
@pytest.mark.gate("REL-G-01")
def test_a_star_row_without_a_locator_fails_the_link_gate(run_uc02: RunFactory) -> None:
    run = run_uc02(make_bundle(locators=False))
    assert run.outcome("REL-G-01") == "fail"
    assert run.report.verdict.status is VerdictStatus.REJECTED


@pytest.mark.gate("REL-G-02")
def test_rows_without_a_second_reader_fail_the_verification_gate(run_uc02: RunFactory) -> None:
    run = run_uc02(make_bundle(verified=False))
    assert run.outcome("REL-G-02") == "fail"
    assert run.finding("unverified_measurement_count") == sum(d for d in (49, 80, 10))


@pytest.mark.req("REL-004")
@pytest.mark.gate("REL-G-05")
@pytest.mark.gate("REL-G-06")
def test_a_missing_instrument_counts_as_missing_hypotheses(run_uc02: RunFactory) -> None:
    bundle = make_bundle()
    bundle["instrument_sets"] = [s for s in bundle["instrument_sets"] if s["instrument"] != "principe-astro"]
    run = run_uc02(bundle)
    assert run.outcome("REL-G-05") == "fail"
    assert run.outcome("REL-G-06") == "fail"


@pytest.mark.req("REL-007")
@pytest.mark.gate("REL-G-08")
def test_an_independent_fitter_that_disagrees_fails_the_agreement_gate(run_uc02: RunFactory) -> None:
    bundle = make_bundle()
    assert run_uc02(bundle, independent_fit(bundle, perturb=1e-7)).outcome("REL-G-08") == "pass"
    assert run_uc02(bundle, independent_fit(bundle, perturb=1e-4)).outcome("REL-G-08") == "fail"


@pytest.mark.req("REL-007")
def test_an_independent_fit_that_skips_scenarios_cannot_pass(run_uc02: RunFactory) -> None:
    bundle = make_bundle()
    partial = independent_fit(bundle)
    partial["fits"] = partial["fits"][:5]
    assert run_uc02(bundle, partial).outcome("REL-G-08") == "missing"


@pytest.mark.req("REL-008")
@pytest.mark.gate("REL-G-09")
def test_the_manifest_carries_the_limitations(run_uc02: RunFactory) -> None:
    run = run_uc02()
    assert run.experiment.findings is not None
    narrative = run.experiment.findings.narrative
    assert any("not a plate remeasurement" in text for text in narrative["limitations"])
    assert narrative["claim_violations"] == []
    assert run.outcome("REL-G-09") == "pass"
    dossier = run.k.kernel.assemble_dossier.execute(
        AssembleDossierCommand(run.experiment.experiment_id, tuple(narrative["limitations"]))
    )
    assert dossier.limitations == limitations()


def test_only_the_bootstrap_count_may_be_overridden() -> None:
    protocol = Einstein1919Protocol()
    with pytest.raises(ValueError, match="predeclared and cannot be overridden"):
        protocol.plan(_inputs(make_bundle()), {"student_t_nu": 30}, 1)


def _inputs(bundle: dict[str, Any]) -> ResolvedInputs:
    source = SourceRef(SourceKind.SYNTHETIC, "sdp_relativity.synthetic", "CC0-1.0", "synthetic recovery fixture")
    fit = json.dumps(independent_fit(bundle)).encode()
    data = dumps(bundle)
    return ResolvedInputs(
        {
            "observations": ResolvedInput(
                "observations", ArtifactId.of_bytes(data), "application/json", source, bundle
            ),
            "independent_fit": ResolvedInput(
                "independent_fit", ArtifactId.of_bytes(fit), "application/json", source, json.loads(fit)
            ),
        }
    )


class TestEinstein1919Protocol(ScientificProtocolContract):
    @pytest.fixture
    def protocol_case(self) -> tuple[Any, ...]:
        return (Einstein1919Protocol(), _inputs(make_bundle()), {"bootstrap_resamples": 20}, 1919)
