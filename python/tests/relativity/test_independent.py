"""T8: the fixture the TypeScript fitter is checked against is the reference fitter's current output (REL-007).

typescript/packages/relativity-fit/test/fit.test.ts asserts the independent implementation agrees
with python-fits-seed7.json; this asserts that file is still what the reference fitter produces,
so neither side can drift without one of the two tests failing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdp_relativity.fitter import fit_set
from sdp_relativity.observations import parse_bundle
from sdp_relativity.reduction import FitScenario
from sdp_relativity.synthetic import make_bundle

pytestmark = pytest.mark.tier("T8")

FIXTURES = Path(__file__).resolve().parents[3] / "typescript" / "packages" / "relativity-fit" / "test" / "fixtures"


@pytest.mark.req("REL-007")
def test_the_shared_bundle_is_the_seeded_synthetic_bundle() -> None:
    assert json.loads((FIXTURES / "bundle-seed7.json").read_text()) == make_bundle(seed=7)


@pytest.mark.req("REL-007")
def test_the_reference_fits_match_a_fresh_run() -> None:
    bundle = parse_bundle(json.loads((FIXTURES / "bundle-seed7.json").read_text()))
    reference = json.loads((FIXTURES / "python-fits-seed7.json").read_text())
    assert len(reference["fits"]) == 3 * 24
    for row in reference["fits"]:
        result = fit_set(bundle.instrument(row["instrument"]), FitScenario(**row["scenario"]))
        assert result.alpha == pytest.approx(row["alpha_arcsec"], rel=1e-10, abs=1e-12)
        assert result.sigma_alpha == pytest.approx(row["sigma_alpha_arcsec"], rel=1e-10)
        assert result.loglik == pytest.approx(row["loglik"], rel=1e-10)
