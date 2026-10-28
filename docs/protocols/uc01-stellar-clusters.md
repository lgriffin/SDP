# Protocol UC-01: open-cluster recovery and candidate search

- Protocol id: `uc01-stellar-clusters`, version 1.0.0
- Module: `sdp_stellar` (architecture spec §10)
- Draft predeclaration: [`experiments/uc01-stellar-clusters/predeclaration.json`](../../experiments/uc01-stellar-clusters/predeclaration.json), packaged as `sdp_stellar/predeclaration.json`

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

`fsp-v1` (`sdp_stellar.features`): gnomonic projection of positions about the declared region centre; the two positions share one robust scale, the two proper motions another, and parallax its own, so the matrix is dimensionless, a rigid rotation of the field only rotates the position pair, and a change of proper-motion units leaves it unchanged. Clustering accepts a `FeatureMatrix` from a registered policy and refuses raw columns, so raw five-axis Euclidean clustering never feeds a gate (STL-002). Parallax-aware whitening from `astrometric_corr` is not in `fsp-v1`; it would be a new policy id.

## Inputs

| Role | Media type | Required | Content |
|---|---|---|---|
| `field:<target_id>`, one per region | Parquet | yes | Curated Gaia DR3 cone in the canonical astrometric schema (§8) |
| `benchmark:hr23`, `benchmark:cga20` | Parquet | no | Cluster table (`name`, `ra_deg`, `dec_deg`, `pm_ra_cosdec_masyr`, `pm_dec_masyr`, `parallax_mas`) whose schema metadata carries `sdp.benchmark.release`, `.licence` and `.citation` |

Benchmarks are optional at definition so that a run without them still happens and STL-G-02 names what is missing.

## Plan

Per region: `prepare` (quality cuts, with the rows each cut removed, then the feature policy), one `search` per grid point (10: HDBSCAN `min_cluster_size` × `min_samples`, DBSCAN `eps` × `min_samples`), `candidates`, `null_fields_per_region` null fields and one injection per richness × distance cell. Then `recovery` across regions and `synthesis`. Null and injection tasks take derived seeds, recorded with their results (STL-006). Only `null_fields_per_region` and `bootstrap_resamples` may be overridden by an experiment.

Services: `clusterers` (the CPU references, from the composition root) and optionally `accelerators` (keyed by the CPU reference each replaces), `gpu_agreement` and `release`. A GPU implementation runs only when its agreement record passed and names that implementation, that CPU reference and this release; otherwise the CPU reference runs and the exclusion is recorded with its reason (STL-004, STL-G-08). Labelling documents record both the configured algorithm and the implementation that ran.

Quality-cut reports give each cut's failures among all input rows (`failed_by_cut`) and the rows each removed in order (`removed_in_order`, summing to the rows dropped). A parallax without a finite, positive uncertainty fails the parallax-over-error cut.

## Candidates

The reference labelling is the first grid point. Each of its clusters is matched to its best-overlapping cluster (Jaccard) in every other labelling: stability is the mean overlap and a star's membership the mean probability. Members are stars at membership ≥ 0.5. A candidate is

- **rejected** when stability < 0.5, it has fewer than 10 members, or its colour–magnitude sequence is not at least 3× tighter than random field draws of the same size;
- **inconclusive** when it has too few members with a colour, or it was cross-matched against fewer than two benchmarks (STL-005);
- **known** when a benchmark cluster lies within 0.1 deg and 1 mas/yr;
- **potentially new** when it passed every check, matches nothing in both benchmarks, and none of its region's null fields produced a surviving candidate (otherwise it is inconclusive, with that count as the reason).

Every dossier carries its region's evidence: null fields run and with survivors, injections run and recovered.

Every candidate, rejected ones included, is retained with its reasons (STL-007) and ranked by a fixed weighting of membership, CMD coherence, stability and novelty. Each dossier opens with "<id> is a candidate open cluster, not a confirmed discovery." and the statements and reasons are linted for discovery language; only the fixed hedge "not a confirmed discovery" exempts a sentence (STL-G-09). The thresholds are module constants for now (deviation D15).

## Null fields and injection

A null field permutes proper motion in RA, proper motion in Dec and parallax independently within 0.5 mag G bins, each with its uncertainty, and runs the whole candidate pipeline; any candidate not rejected is a false positive. An injection plants a cluster of declared richness at a declared distance (3 pc core, the declared proper-motion dispersion, a field star's motion as centre) and scores the best-overlapping found cluster: recovered at completeness and purity ≥ 0.5.

## Synthetic run

`python -m sdp_stellar.synthetic <dir>` writes one field per region with planted clusters (SYN-A in the Pleiades field, in both synthetic benchmarks; SYN-B in the Praesepe field, in neither; SYN-C in the h and chi Persei field, in hr23 only) and the two synthetic benchmark tables. On them, with 3 null fields per region, every gate passes: SYN-A is recovered as known, SYN-B is the one potentially new candidate, SYN-C is found but rejected as unstable across the grid (stability 0.48), and no null field yields a surviving candidate. Injections into the Pleiades field mostly merge into the field's own proper-motion clump under HDBSCAN's excess-of-mass selection; the injection curve reports that rather than hiding it.

## Gates

STL-G-01 to STL-G-09 as in the specification §10, evaluated on these findings: `regions_without_reason`, `benchmarks_incomplete`, `recovery_reported_benchmarks`, `null_false_positive_rate` (absent when no null field ran, so the gate cannot pass), `injection_curve_cells_missing`, `novel_without_two_crossmatches`, `dropped_candidates`, `gpu_results_without_agreement`, `report_claim_violations`.
