# Reference products (T5s)

Each subdirectory is one recorded archive product that the semantic conformance tier (T5s, Integration v0.4.0 §6) compares a dialect against. `python/tests/products/test_product_model.py` picks up every directory here; until the first recording run the directory is empty and that test is skipped.

A directory holds:

| File | What it is |
|---|---|
| the raw product | The bytes exactly as MAST served them, named as `inventory.json`'s `productFilename` |
| `inventory.json` | The MAST product-inventory row (`productFilename`, `productType`, `calib_level`, `dataRights`, `dataURI`), plus `rendered_query` |
| `obscore.json` | The product's ObsCore row from MAST's ObsTAP view |
| `datalink.json` | Optional: the DataLink rows (`semantics`, `access_url` or `ID`, `description`) |
| `reference.json` | What the product is, written by a person from the mission documentation, not from the dialect's output |

`reference.json` fields: `product_id`, `product_type`, `mission`, `instrument`, `detector`, `filter_or_grating`, `frame`, `time_scale`, `time_start_mjd`, `time_end_mjd`, `units`, `calibration_level`, `extensions` (name to role), `has_wcs`, `progenitors`, `documentation` (the JDox page or pipeline document the values come from), `status` and `verified_by`.

## Status

- `to_verify`: recorded, not yet checked. The comparison runs and reports, but cannot admit an adapter.
- `verified`: two different people have checked the record against the documentation (`verified_by` names both). Only verified records admit an adapter for a product type (INT-016).
- `synthetic`: built in code for tests. Never admits anything. The synthetic JWST-layout products live in `python/tests/products/synthetic_products.py`, not here.

## Recording

The sandbox that builds SDP cannot reach MAST, so the files are recorded by the manual **Integration recordings** workflow (Actions, *Run workflow*, with an `obs_id`, a file name and a directory name), which runs `sdp integration record` through the archive `Transport` and pushes the result to a `reference-products/run-<id>` branch: the raw file, `inventory.json`, `obscore.json`, a `reference.template.json`, and the cassettes for every exchange plus MAST's capability descriptor.

A person then fills the template from the mission documentation (not from the dialect's output), saves it as `reference.json` with `status: to_verify`, and opens a pull request; the test picks it up from then on. A second person checks it and the record moves to `verified` with both names in `verified_by`.
