# Recorded sweep tiles (T8R)

Whole tiles of the discovery sweep on real Gaia DR3 stars, each exported from Leigh's PC with
`uv run python tools/realdata_slice.py sweep-cluster "<name>" <out>` (TST-011). A tile folder holds the exact stars the
sweep handed the tile (`stars.parquet`), the stored known-cluster catalogues (`benchmark-hr23.parquet`,
`benchmark-cga20.parquet`), the tiles' settings guard (`sweep-settings.json`), the sweep's own result (`tile.json`, the
known answer) and `manifest.json` with every SHA-256. `.gitattributes` keeps them byte-identical (`* -text`).
`tests/stellar/test_sweep_recorded.py` sweeps each again on every pull request and requires the same answer.

| Tile | Cluster | Swept | Exported | Stars | Answer recorded |
|---|---|---|---|---|---|
| `near-b003-t008` | IC 2391 | 2026-10-31, main 7575489 (#170 run) | 2026-11-25; re-swept identically at 449b777 | 29,646 (8,103 owned) | no candidate at all, though about 180 stars sit at hr23's IC 2391 position, motion and parallax: one of #170's 23 known clusters with no candidate |

When #170 is fixed the answer changes and the test goes red on purpose: re-export the tile from the fixed sweep
and record the new answer.

The data come from the European Space Agency mission Gaia (https://www.cosmos.esa.int/gaia), processed by the Gaia
Data Processing and Analysis Consortium (DPAC). Licence CC BY-SA 3.0 IGO. Known-cluster catalogues: Hunt & Reffert
2023 (hr23) and Cantat-Gaudin et al. 2020 (cga20), as stored by `sdp benchmark fetch` with their own citations in
the Parquet metadata.
