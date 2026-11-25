# Gaia DR3 orbits and the PC field list (T8R)

Two real-data recordings for the hidden-companion screen (STL-020, STL-023), checked by
`tests/stellar/test_companions_recorded.py` (TST-011).

- `orbits-slice.parquet`: 15,226 of the 169,227 Gaia DR3 orbits (`nss_two_body_orbit` joined to `gaia_source` and
  `binary_masses`, `a_thiele_innes` not null) that `sdp companions fetch` pulls. Cut by rule, not by hand:
  `uv run python tools/realdata_slice.py nss-orbits <gaia-dr3-orbits.parquet> <out>` keeps every flagged orbit, every
  orbit within 20% of one of the screen's cuts, and every orbit whose source_id is a multiple of 50, in the columns the
  screen reads. The same input gives the same bytes. SHA-256 `22f265db...ec79f96`. Source table: the project share's
  `companions/gaia-dr3-orbits.parquet`, SHA-256 `a114c6b4...dc38c60`.
- `field-dr3.csv`: the field list `sdp companions field` wrote on Leigh's PC on 2026-11-04 (main 51d3cae with PR #178):
  136 neutron star or black hole, 5,098 dark partners, Gaia BH1 flagged. SHA-256 `9645ec45...4f9eee5c`.

The full table is checked too when it is present (`-m realdata`, `SDP_REALDATA` pointing at the folder that holds
`companions/gaia-dr3-orbits.parquet`; on the PC that is the SDP data folder).

The data come from the European Space Agency mission Gaia (https://www.cosmos.esa.int/gaia), processed by the Gaia
Data Processing and Analysis Consortium (DPAC, https://www.cosmos.esa.int/web/gaia/dpac/consortium). Licence CC BY-SA
3.0 IGO. Gaia Collaboration, Arenou et al. 2023, A&A 674, A34.
