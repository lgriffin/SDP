"""Stage A: MAST's product inventory and ObsCore row for a fetched product, over the Transport.

MAST is a primed fake here (invented values in MAST's response shapes); the live answer is recorded
by the reference-product recording run and replays through the same requests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdp.adapters.archives.mast import file_name
from sdp.adapters.archives.mast_inventory import InventoryMissing, MastProductInventory
from sdp.adapters.archives.transport import CassetteTransport, TransportError
from sdp.kernel.domain.investigation import ProductRef

from .vo_fakes import PrimedService

pytestmark = pytest.mark.tier("T3")

NAME = "jw99999-o001_t001_nircam_clear-f200w_i2d.fits"
PRODUCT = ProductRef(
    "mast", "jw99999-o001_t001_nircam_clear-f200w", f"https://mast.stsci.edu/portal/Download/file/JWST/product/{NAME}"
)


def complete(rows: list[dict[str, object]]) -> bytes:
    return json.dumps({"status": "COMPLETE", "msg": "", "data": rows, "fields": []}).encode()


def primed(
    fake: PrimedService, inventory: MastProductInventory, *, products: list[dict[str, object]] | None = None
) -> None:
    fake.table(
        inventory.obscore_request(PRODUCT),
        {
            "obs_id": [PRODUCT.product_id],
            "obs_collection": ["JWST"],
            "instrument_name": ["NIRCAM/IMAGE"],
            "dataproduct_type": ["image"],
            "calib_level": [3],
            "data_rights": ["PUBLIC"],
            "s_ra": [53.16],
            "s_dec": [-27.78],
            "access_url": [PRODUCT.uri],
        },
    )
    fake.raw(
        inventory.observation_request(PRODUCT.product_id),
        complete([{"obsid": "87654321", "obs_id": PRODUCT.product_id}]),
        content_type="application/json",
    )
    fake.raw(
        inventory.products_request("87654321"),
        complete(
            products
            if products is not None
            else [
                {"productFilename": NAME.replace(".fits", ".jpg"), "productType": "PREVIEW"},
                {
                    "productFilename": NAME,
                    "productType": "SCIENCE",
                    "calib_level": 3,
                    "dataRights": "PUBLIC",
                    "size": 123456,
                    "dataURI": f"mast:JWST/product/{NAME}",
                    "productSubGroupDescription": "I2D",
                },
            ]
        ),
        content_type="application/json",
    )


@pytest.mark.req("INT-006")
def test_the_inventory_row_and_obscore_row_are_found_for_the_fetched_file() -> None:
    fake = PrimedService()
    inventory = MastProductInventory(fake)
    primed(fake, inventory)
    rows = inventory.rows(PRODUCT)
    assert rows.inventory_row["productType"] == "SCIENCE" and rows.inventory_row["calib_level"] == 3
    assert rows.obscore_row["calib_level"] == 3 and rows.obscore_row["obs_collection"] == "JWST"
    assert "Mast.Caom.Products" in rows.rendered_query and "ivoa.obscore" in rows.rendered_query


def test_a_product_matches_by_data_uri_when_the_file_name_column_is_absent() -> None:
    fake = PrimedService()
    inventory = MastProductInventory(fake)
    primed(fake, inventory, products=[{"dataURI": f"mast:JWST/product/{NAME}", "productType": "SCIENCE"}])
    assert inventory.rows(PRODUCT).inventory_row["dataURI"].endswith(NAME)


def test_the_inventory_pages_until_the_file_is_found() -> None:
    fake = PrimedService()
    inventory = MastProductInventory(fake, page_size=2)
    primed(fake, inventory, products=[{"productFilename": "a.fits"}, {"productFilename": "b.fits"}])
    fake.raw(
        inventory.products_request("87654321", 2),
        complete([{"productFilename": NAME, "productType": "SCIENCE"}]),
        content_type="application/json",
    )
    rows = inventory.rows(PRODUCT)
    assert rows.inventory_row["productFilename"] == NAME
    assert rows.rendered_query.count("Mast.Caom.Products") == 2


def test_a_short_page_ends_the_search() -> None:
    fake = PrimedService()
    inventory = MastProductInventory(fake, page_size=2)
    primed(fake, inventory, products=[{"productFilename": "a.fits"}])
    with pytest.raises(InventoryMissing, match=NAME):
        inventory.rows(PRODUCT)


def test_a_product_mast_does_not_list_is_an_error_not_a_guess() -> None:
    fake = PrimedService()
    inventory = MastProductInventory(fake)
    primed(fake, inventory, products=[{"productFilename": "something_else.fits"}])
    with pytest.raises(InventoryMissing, match=NAME):
        inventory.rows(PRODUCT)


def test_an_incomplete_mast_answer_is_refused() -> None:
    fake = PrimedService()
    inventory = MastProductInventory(fake)
    primed(fake, inventory)
    fake.raw(
        inventory.observation_request(PRODUCT.product_id),
        json.dumps({"status": "EXECUTING", "data": []}).encode(),
        content_type="application/json",
    )
    with pytest.raises(TransportError, match="EXECUTING"):
        inventory.rows(PRODUCT)


@pytest.mark.parametrize(
    ("uri", "name"),
    [
        (f"https://mast.stsci.edu/portal/Download/file/JWST/product/{NAME}", NAME),
        (f"https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:JWST/product/{NAME}", NAME),
        (f"mast:JWST/product/{NAME}", NAME),
    ],
)
def test_file_names_come_from_urls_and_data_uris(uri: str, name: str) -> None:
    assert file_name(uri) == name


@pytest.mark.tier("T5")
def test_the_inventory_replays_from_cassettes(tmp_path: Path) -> None:
    fake = PrimedService()
    recorder = MastProductInventory(CassetteTransport(tmp_path, "record", inner=fake))
    primed(fake, recorder)
    recorded = recorder.rows(PRODUCT)
    assert MastProductInventory(CassetteTransport(tmp_path, "replay")).rows(PRODUCT) == recorded
