"""Stage A in the Investigate Retrieve stage (T4): a JWST product is fetched, registered raw with the
adapter and product type it came through, normalised, validated and stored with its envelope; curation
only follows a passing report and is refused when it would lose an array (INT-006 to INT-008, INT-016).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pytest
from astropy.io import fits

from sdp.adapters.products import EnvelopeValidator, JwstDialect
from sdp.contracts import validate
from sdp.kernel.application.investigate import ProductEnvelopes
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.dataset import ProductUse
from sdp.kernel.domain.investigation import FetchedProduct, ObservationRecord, ProductEvidence, ProductRef
from sdp.kernel.ports.integration import ProductRows
from sdp.kernel.ports.investigate import CuratedProduct, InvestigateFieldCommand
from sdp.testing.fakes.integration import StaticProductInventory
from sdp.testing.fakes.investigate import FakeObservationArchive, PlacedRecord
from sdp.testing.investigate import (
    PLANTED,
    ProperMotionExcessTest,
    gaia_archive,
    investigate_ports,
    synthetic_catalogue,
    synthetic_field,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

from .synthetic_products import image, raw_product

pytestmark = pytest.mark.tier("T4")

NAME = "jw99999-o001_t001_nircam_clear-f200w_i2d.fits"
ADAPTER = "mast-observations"


@dataclass
class Jwst:
    k: InMemoryKernel
    inventory: StaticProductInventory
    archive: FakeObservationArchive
    products: list[ProductEvidence] = field(default_factory=list)


def _archive(data: bytes) -> tuple[FakeObservationArchive, ProductRef]:
    star = next(r for r in synthetic_catalogue().to_pylist() if r["source_key"] in PLANTED)
    ref = ProductRef("fake-plates", "jw99999-o001_t001_nircam_clear-f200w", f"https://example.invalid/{NAME}")
    record = ObservationRecord(ref, "JWST", "NIRCAM", "image", 60000.0, len(data), "public domain", "MAST")
    archive = FakeObservationArchive(
        "fake-plates", "mast:caom", (PlacedRecord(star["ra_deg"], star["dec_deg"], record),), {ref.product_id: data}
    )
    return archive, ref


def run(data: bytes, normalizer: JwstDialect | None = None) -> Jwst:
    archive, ref = _archive(data)
    template = raw_product(data, NAME)
    inventory = StaticProductInventory()
    inventory.prime(ref, ProductRows(template.inventory_row, template.obscore_row, (), "inventory for " + NAME))
    catalogue = synthetic_catalogue()
    test = ProperMotionExcessTest(needs_products=("image",))
    ports = investigate_ports(test, catalogue=catalogue, observations=archive)
    ports.product_envelopes = {
        "fake-plates": ProductEnvelopes(ADAPTER, normalizer or JwstDialect(), EnvelopeValidator(), inventory)
    }
    k = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=ports,
    )
    assert k.kernel.investigate_field is not None
    result = k.kernel.investigate_field.execute(
        InvestigateFieldCommand(synthetic_field(), synthetic_profile(needs_products=("image",)), seed=11)
    )
    world = Jwst(k, inventory, archive)
    world.products = [p for b in result.bundles for p in b.products]
    return world


def envelope(world: Jwst, product: ProductEvidence) -> dict:  # type: ignore[type-arg]
    assert product.envelope_artifact_id is not None
    doc = decode_artifact(world.k.store.get(product.envelope_artifact_id), JSON)
    assert validate("scientific-data-product", doc) == []
    return doc  # type: ignore[no-any-return]


@pytest.mark.req("INT-006", "INT-016", "INT-019")
def test_a_jwst_image_is_registered_with_its_adapter_and_its_envelope_is_stored() -> None:
    world = run(image())
    (product,) = world.products
    raw = world.k.db.state.datasets.get(product.artifact_id)
    assert raw is not None and raw.product == ProductUse(ADAPTER, "image")
    doc = envelope(world, product)
    assert doc["raw_artifact_id"] == product.artifact_id.value
    assert doc["metadata"]["mission"] == "JWST" and doc["metadata"]["calibration_level"] == 3
    assert doc["validation"]["passed"] is True
    assert doc["curated_artifact_id"] is None, "the JWST dialect keeps the FITS product as it is"
    assert f"sdp:{product.artifact_id.value}" in world.k.db.state.graph.ancestors(product.envelope_artifact_id.value)  # type: ignore[union-attr]


@pytest.mark.req("INT-006", "INT-019")
def test_a_failing_envelope_is_kept_with_its_report_and_nothing_is_curated() -> None:
    world = run(image(bunit="furlongs per fortnight squiggle"))
    (product,) = world.products
    doc = envelope(world, product)
    assert doc["validation"]["passed"] is False
    assert "units" in {c["field"] for c in doc["validation"]["checks"] if not c["passed"]}
    assert product.raw_artifact_id is None and doc["curated_artifact_id"] is None


def _drop_err(data: bytes) -> bytes:
    with fits.open(io.BytesIO(data)) as hdul:
        kept = [h.copy() for h in hdul if h.name != "ERR"]
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(header=kept[0].header), *kept[1:]]).writeto(buf)
    return buf.getvalue()


class LossyDialect(JwstDialect):
    """Curates by dropping the error array: the validator must refuse it."""

    def curate(self, product_type: str, raw: FetchedProduct) -> CuratedProduct | None:
        return CuratedProduct(_drop_err(raw.data), "application/fits")


class FaithfulDialect(JwstDialect):
    """Curates by rewriting the file with every array kept and one keyword added."""

    def curate(self, product_type: str, raw: FetchedProduct) -> CuratedProduct | None:
        with fits.open(io.BytesIO(raw.data)) as hdul:
            hdus = [h.copy() for h in hdul]
        hdus[0].header["SDPCURAT"] = "faithful"
        buf = io.BytesIO()
        fits.HDUList(hdus).writeto(buf)
        return CuratedProduct(buf.getvalue(), "application/fits")


@pytest.mark.req("INT-007", "INT-019")
def test_curation_that_loses_the_error_array_is_refused_in_retrieve() -> None:
    world = run(image(), LossyDialect())
    (product,) = world.products
    assert product.raw_artifact_id is None, "the raw product is what the test reads"
    assert envelope(world, product)["curated_artifact_id"] is None
    assert world.k.db.state.datasets.children(product.artifact_id) == []


@pytest.mark.req("INT-007")
def test_curation_that_keeps_every_array_is_registered_and_named_in_the_envelope() -> None:
    world = run(image(), FaithfulDialect())
    (product,) = world.products
    assert product.raw_artifact_id is not None
    assert envelope(world, product)["curated_artifact_id"] == product.artifact_id.value
