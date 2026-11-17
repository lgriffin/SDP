# Protocol UC-06: star clusters in Gaia DR4, predeclared before the release

- Protocol id: `uc06-gaia-dr4-clusters`, version 0.1.0 (DRAFT for Leigh's reading; becomes 1.0.0 when signed)
- Modules: `sdp_stellar.dr4_delta` (Part A), `sdp_stellar.companions` (Part B)
- Lock date: Monday 24 November 2026. The Gaia archive goes offline on 25 November and Gaia DR4 is released on 2 December 2026.
- Rule of this protocol: it is committed, tagged and deposited publicly before any Gaia DR4 data is read. Anything changed after the lock is a dated, labelled amendment and is reported as such.

Status of this draft: every choice marked **[choose]** is still open. Every other choice is proposed and stays unless Leigh changes it before the lock.

## Research questions

- **Part A (measurement, sure to give a result):** for the 24 seed clusters frozen on 5 November 2026, how do member counts, mean parallax, distance and mean proper motion change from Gaia DR3 to DR4 under one fixed selection?
- **Part B (test, may give a null):** do star-cluster members show more astrometric wobble, and more dark partners heavier than 1.4 solar masses, than field stars of the same brightness, distance and crowding?

Part A is already frozen (`sdp_stellar.dr4_delta.FROZEN_SHA256`, PR #191; see [docs/dr4-delta.md](../dr4-delta.md)). This protocol adopts it unchanged and adds nothing to it.

## Part B: hypotheses

| Id | Statement |
|---|---|
| UC06-H1 | Cluster members, taken together, hold more wobblers than matched field stars. |
| UC06-H0 | They hold no more wobblers than matched field stars. |
| UC06-H2 | At least one cluster member has a Gaia DR4 orbit whose dark partner's lower mass bound is above 1.4 solar masses and is heavier than any star still alive at the cluster's age. |

H1 is decided by one stacked test. H2 is a search: a hit is a named candidate, not a population claim.

## Part B: clusters and members (fixed from DR3, before DR4)

- **Cluster list:** the clusters of the DR3 whole-sky sweep (`gaia-dr3`, the run of 4 November 2026) with status known, inconclusive or potentially new, and at least 3 seed members at parallax 1 mas or more. This is the 52 clusters screened on 4 November. The 62 clusters whose seeds lie beyond 1 kpc are out of scope, and the screen now says so for each (`out_of_scope`).
- **[choose] Scope:** keep 1 kpc (proposed: the wobble a dark partner causes shrinks with distance, and the 4 November run already peaked at 23.6 GB), or lower the floor to 0.5 mas (2 kpc) for more clusters and more PC time.
- **Members:** found again in DR4 from each cluster's DR3 seed footprint, motion and parallax, blind to RUWE (STL-022), with the membership settings of `companions.json` unchanged.
- **Large groups:** a group of more than 3,000 members is reported but left out of the stacked test, because the two DR3 near-misses (4,696 and 26,425 members) look like field structure, not clusters. **[choose]** the cap; 3,000 is proposed.

The cluster list, seed ids and membership settings are written to a frozen file with its SHA-256 before the lock, as Part A's are.

## Part B: the wobble test (decides H1)

- **Wobbler:** a star whose log RUWE in DR4 is more than 3 robust sigma above the DR4 field median at its G (STL-021, z_min 3.0). The baseline is recomputed from DR4 itself.
- **Field null:** each member's chance of being a wobbler is the field share in its (G, parallax) bin, as in DR3, **plus a crowding bin**: the number of Gaia sources within 1 arcminute, in 4 bins. Crowding is added because cluster cores are crowded, and crowding alone can raise RUWE.
- **Statistic:** total wobblers over all clusters in scope, against the total the field predicts. One-sided Poisson tail.
- **Verdict rule:** H1 if p < 0.001 **and** the excess is at least 10% of the expected count. H0 if p > 0.05. Anything between is inconclusive.
- **Per-cluster results** are reported with p-values corrected for the number of clusters (Holm). They do not change the verdict.
- **[choose] Epoch astrometry:** if DR4 publishes per-transit astrometry for cluster members, add the residual-based wobble statistic as a second, reported test (proposed: reported only, not deciding, because its noise model is unknown until release).

## Part B: dark partners (H2)

- DR4's two-body orbit table, judged by STL-020's rule: a partner is a neutron star or black hole candidate only when no main-sequence flux ratio fits it and its lower mass bound is above 1.4 solar masses.
- The member's mass and the cluster's age come from the MIST isochrone fit (STL-036). A partner is reported as heavier than any living star when its lower bound exceeds the turn-off mass at that age.
- The known-answer check is Gaia BH1. If the field run does not flag BH1, the run fails and no verdict is written.

## What would be reported whatever the outcome

The verdict, the counts and expected counts, the per-cluster table, every flagged orbit, the settings SHA-256s, and the list of clusters and stars dropped with the reason. A null is written up exactly as a positive would be.

## Known weaknesses, declared now

- The Gaia collaboration will publish its own DR4 black hole searches. This protocol's claim is its predeclared cluster-against-field comparison, not being first to any one object.
- DR4 column and table names are not public before release. Mapping them is allowed after the lock and is not an amendment, provided no selection value changes.
- Crowding bins are coarse; a residual crowding effect would push towards H1. The 10% minimum excess is there for that reason.
- Membership is found from DR3 seeds, so clusters DR3 missed cannot be tested.

## Outputs and timeline

1. Lock: this file at 1.0.0, frozen settings files, git tag `uc06-prereg-v1`, Zenodo deposit for a DOI (Leigh's account). By 24 November 2026.
2. Part A report: by mid December 2026.
3. Part B verdict: as a Research Note of the AAS by end of January 2027, citing the deposit.
