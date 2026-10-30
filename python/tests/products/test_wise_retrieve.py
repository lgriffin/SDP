"""WISE light curves in the Investigate Retrieve stage (T4): a real NEOWISE per-exposure VOTable,
recorded from IRSA, is enveloped and validated exactly as a Gaia or JWST product is, stamped with its
adapter as a time series, curated to the canonical W1 light curve, and read by the variability test
(INT-006, INT-016, INT-023).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sdp.adapters.archives.irsa_wise import IrsaWiseInventory
from sdp.adapters.archives.transport import CassetteTransport, HttpRequest
from sdp.adapters.products import EnvelopeValidator, WiseLightCurveDialect
from sdp.adapters.wise import CITATION, LICENCE, WISE_TABLES, lightcurve_url, product_id
from sdp.contracts import validate
from sdp.kernel.application.investigate import ProductEnvelopes
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.dataset import ProductUse
from sdp.kernel.domain.investigation import (
    ArchiveRole,
    ArchiveSelection,
    ObservationRecord,
    ProductRef,
    TestSelection,
)
from sdp.kernel.ports.investigate import InvestigateFieldCommand
from sdp.kernel.science.products import parse_lightcurve
from sdp.testing.fakes.integration import StaticProductInventory
from sdp.testing.fakes.investigate import FakeObservationArchive, PlacedRecord
from sdp.testing.investigate import (
    PLANTED,
    gaia_archive,
    investigate_ports,
    synthetic_catalogue,
    synthetic_field,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.wiring import build_in_memory_kernel
from sdp_investigate.photometry import Variability

pytestmark = [pytest.mark.tier("T4"), pytest.mark.req("INT-006", "INT-016", "INT-023")]

CASSETTES = Path(__file__).resolve().parents[1] / "cassettes" / "irsa-wise"
SOURCE = ("J034608.37+232050.6", 56.5349135, 23.3473901)


def test_a_neowise_light_curve_is_enveloped_curated_and_read_by_the_variability_test() -> None:
    transport = CassetteTransport(CASSETTES, "replay")
    ref = ProductRef("irsa-wise", product_id(*SOURCE, "NEOWISE"), lightcurve_url(WISE_TABLES["NEOWISE"], *SOURCE[1:]))
    data = transport.send(HttpRequest("GET", ref.uri)).body
    inventory = StaticProductInventory()
    inventory.prime(ref, IrsaWiseInventory(transport).rows(ref))

    catalogue = synthetic_catalogue()
    star = next(r for r in catalogue.to_pylist() if r["source_key"] == PLANTED[0])
    record = ObservationRecord(ref, "NEOWISE", "WISE", "lightcurve", 56639.0, None, LICENCE, CITATION, 60523.0)
    archive = FakeObservationArchive(
        "irsa-wise",
        "irsa:allwise-neowise",
        (PlacedRecord(star["ra_deg"], star["dec_deg"], record),),
        {ref.product_id: data},
    )
    ports = investigate_ports(Variability(), catalogue=catalogue)
    ports.observation_gateways = {"irsa-wise": archive}
    ports.product_envelopes = {
        "irsa-wise": ProductEnvelopes("irsa-wise", "1.0.0", WiseLightCurveDialect(), EnvelopeValidator(), inventory)
    }
    profile = replace(
        synthetic_profile(),
        archives=(
            ArchiveSelection("irsa-wise", ArchiveRole.OBSERVATIONS, "irsa:allwise-neowise", match_radius_arcsec=2.0),
        ),
        tests=(TestSelection("variability", "1.0.0", {"min_points": 20}, needs_products=("lightcurve",)),),
    )
    k = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=ports,
    )
    assert k.kernel.investigate_field is not None
    result = k.kernel.investigate_field.execute(InvestigateFieldCommand(synthetic_field(), profile, seed=3))

    (product,) = [p for b in result.bundles for p in b.products]
    assert archive.fetches == [ref.product_id]
    assert product.raw_artifact_id is not None and product.envelope_artifact_id is not None
    raw = k.db.state.datasets.get(product.raw_artifact_id)
    assert raw is not None and raw.product == ProductUse("irsa-wise", "1.0.0", "timeseries")

    doc = decode_artifact(k.store.get(product.envelope_artifact_id), JSON)
    assert validate("scientific-data-product", doc) == []
    assert doc["validation"]["passed"] is True
    assert doc["metadata"]["product_type"] == "timeseries" and doc["metadata"]["time"]["scale"] == "UTC"
    assert doc["curated_artifact_id"] == product.artifact_id.value

    curve = parse_lightcurve(k.store.get(product.artifact_id))
    assert len(curve.mjd) == 219

    (candidate,) = result.investigations
    (scored,) = candidate.test_results
    assert (scored.test_name, scored.subject.object_key) == ("variability", PLANTED[0])
    assert scored.details["points"] == 219 and scored.details["dof"] == 218
    # 1.39 times the scatter the quoted errors allow: about what every star in this field shows in
    # NEOWISE (1.3 to 1.8, measured live on 2026-10-29), which is why the profile calibrates the errors
    assert scored.statistic / 218 == pytest.approx(1.39, abs=0.01)
    assert scored.details["baseline_days"] == pytest.approx(60338.46936532 - 56704.19245361)
