# Recorded M13 cone (Gaia DR3)

`m13-cone.parquet` holds the 11,552 Gaia DR3 stars within 0.6° of M13 (RA 250.42°, Dec +36.46°) that pass UC-03's predeclared cuts (`sdp_relativity/einstein1921.json`: `ruwe < 1.4`, `astrometric_params_solved` in {31, 95}, not `duplicated_source`, `phot_g_mean_mag < 19`, finite astrometry). Only the columns UC-03 reads are kept, in their native `gaia_source` names.

- Exported by Leigh on 2026-10-31 from the local bulk copy of Gaia DR3 with `uv run sdp einstein1921 export` (REL-020). It was not written by hand or edited.
- SHA-256 `43e7a3d4db03eb6433134ea1add1b95c99702b3d8fd63b883a9fe72d7e852282`, pinned in `tests/relativity/test_m13_recorded.py`.
- Used by the M13 known-answer test (REL-021). That test checks the file reproduces Leigh's full UC-03 and UC-03b runs, and that the bright-star mass is within 20% of the published one.

The data come from the European Space Agency mission Gaia (https://www.cosmos.esa.int/gaia), processed by the Gaia Data Processing and Analysis Consortium (DPAC, https://www.cosmos.esa.int/web/gaia/dpac/consortium).
