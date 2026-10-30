"""Gaia DataLink products in the Investigate Retrieve stage (T4): a real epoch-photometry VOTable,
recorded from the Gaia archive, is enveloped and validated exactly as a JWST FITS file is, stamped
with its adapter as a time series, curated to the canonical light curve, and read by the variability
test (INT-006, INT-016, INT-022).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sdp.adapters.archives.gaia_datalink import GaiaDataLinkInventory
from sdp.adapters.archives.transport import CassetteTransport, HttpRequest
from sdp.adapters.gaia_dr3 import CITATION, LICENCE, data_url, product_id
from sdp.adapters.products import EnvelopeValidator, GaiaDataLinkDialect
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

pytestmark = [pytest.mark.tier("T4"), pytest.mark.req("INT-006", "INT-016", "INT-022")]

CASSETTES = Path(__file__).resolve().parents[1] / "cassettes" / "gaia-datalink"
SOURCE = 64971933089093760


def test_a_gaia_light_curve_is_enveloped_curated_and_read_by_the_variability_test() -> None:
    transport = CassetteTransport(CASSETTES, "replay")
    ref = ProductRef("gaia-datalink", product_id(SOURCE, "EPOCH_PHOTOMETRY"), data_url(SOURCE, "EPOCH_PHOTOMETRY"))
    data = transport.send(HttpRequest("GET", ref.uri)).body
    inventory = StaticProductInventory()
    inventory.prime(ref, GaiaDataLinkInventory(transport).rows(ref))

    catalogue = synthetic_catalogue()
    star = next(r for r in catalogue.to_pylist() if r["source_key"] == PLANTED[0])
    record = ObservationRecord(ref, "GAIA", "Astro, BP, RP", "lightcurve", 56863.0, None, LICENCE, CITATION)
    archive = FakeObservationArchive(
        "gaia-datalink", "gaia:DR3", (PlacedRecord(star["ra_deg"], star["dec_deg"], record),), {ref.product_id: data}
    )
    ports = investigate_ports(Variability(), catalogue=catalogue)
    ports.observation_gateways = {"gaia-datalink": archive}
    ports.product_envelopes = {
        "gaia-datalink": ProductEnvelopes(
            "gaia-datalink", "1.0.0", GaiaDataLinkDialect(), EnvelopeValidator(), inventory
        )
    }
    profile = replace(
        synthetic_profile(),
        archives=(ArchiveSelection("gaia-datalink", ArchiveRole.OBSERVATIONS, "gaia:DR3", match_radius_arcsec=1.0),),
        tests=(TestSelection("variability", "1.0.0", {"min_points": 5}, needs_products=("lightcurve",)),),
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
    assert raw is not None and raw.product == ProductUse("gaia-datalink", "1.0.0", "timeseries")

    doc = decode_artifact(k.store.get(product.envelope_artifact_id), JSON)
    assert validate("scientific-data-product", doc) == []
    assert doc["validation"]["passed"] is True
    assert doc["metadata"]["product_type"] == "timeseries" and doc["metadata"]["time"]["scale"] == "TCB"
    assert doc["curated_artifact_id"] == product.artifact_id.value

    curve = parse_lightcurve(k.store.get(product.artifact_id))
    assert len(curve.mjd) == 27

    (candidate,) = result.investigations
    (scored,) = candidate.test_results
    assert (scored.test_name, scored.subject.object_key) == ("variability", PLANTED[0])
    assert scored.p_value is not None and scored.p_value < 1e-6, "a Gaia VARIABLE star varies"
    assert scored.details["points"] == 27
