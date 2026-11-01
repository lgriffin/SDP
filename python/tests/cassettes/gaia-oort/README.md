# Recorded Oort sample (Gaia DR3)

`oort-sample.parquet` holds 26,357 Gaia DR3 stars that pass UC-04's shared cuts and its pushed-down read (`sdp_stellar/oort1927.json`: `ruwe < 1.4`, `parallax / parallax_error > 10`, not `duplicated_source`, finite motions, G < 15, parallax above the farthest shell). It keeps only the columns UC-04 reads, under their native `gaia_source` names.

- Leigh exported it on 2026-11-01 from the local bulk copy with `uv run sdp oort1927 export` (source_id divisible by 100), which gave 656,189 stars. A fixed cut to source_id divisible by 10,000 then brought it to this size. The file was not written by hand or edited.
- SHA-256 `7a90abd9bd8a62fe969dccb738a28e4ded3f75e1dc055cc2d50473bc7ec8cf3b`, pinned in `tests/stellar/test_oort_recorded.py`.
- `report.json` and `report.md` are Leigh's full UC-04 run of the same day on the whole bulk copy, unchanged. That run gave 6,638,336 baseline stars, a verdict of `supports_UC04_H1` in 48 of 48 scenarios, and a comparison with Oort that `disagrees_with_oort`.
- Used by the Oort known-answer test (STL-019). It checks that the sample reproduces the full run's baseline within the sample's own errors.

The data come from the European Space Agency mission Gaia (https://www.cosmos.esa.int/gaia), processed by the Gaia Data Processing and Analysis Consortium (DPAC, https://www.cosmos.esa.int/web/gaia/dpac/consortium).
