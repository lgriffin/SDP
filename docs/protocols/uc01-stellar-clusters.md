# Protocol UC-01: open-cluster recovery and candidate search

- Protocol id: `uc01-stellar-clusters`, version 1.0.0
- Module: `sdp_stellar` (architecture spec §10)
- Draft predeclaration: [`experiments/uc01-stellar-clusters/predeclaration.json`](../../experiments/uc01-stellar-clusters/predeclaration.json)

## Hypotheses

UC01-H1 to H3 stand as in SDD 0.2.0 §10.2: recovery of known clusters, potentially new candidates surviving every check, and comparison against the published Hunt and Reffert HDBSCAN configuration.

## Release pinning

The benchmark is predeclared against `gaia:DR3`. Gaia DR4 (scheduled for 2 December 2026) runs as a separate experiment under this same predeclaration, which turns the release change into a cross-release reproducibility result (decision 6, §19).

## Predeclared regions (draft)

| Region | Centre (ICRS) | Radius | Why |
|---|---|---|---|
| pleiades | 56.75, +24.12 | 2.0 deg | Rich nearby benchmark; recovery sanity check |
| praesepe | 130.10, +19.67 | 2.0 deg | Second nearby benchmark, different proper-motion signature |
| h-chi-persei | 34.75, +57.13 | 1.5 deg | Crowded plane field with a distant double cluster: contamination and completeness |
| ngp-control | 192.86, +27.13 | 2.0 deg | High-latitude control near the North Galactic Pole: empirical null |

These regions, the quality cuts and the null-field tolerance are proposals for Leigh to confirm before the predeclaration is frozen; STL-G-01 requires them frozen before the first Gaia query.

## Feature space

`fsp-v1`: tangent-plane projection about the region centre, proper-motion scaling, optional parallax-aware whitening from the astrometric correlation matrix. Raw five-axis Euclidean clustering never feeds a gate (STL-002).

## Gates

STL-G-01 to STL-G-09 as in the specification §10.
