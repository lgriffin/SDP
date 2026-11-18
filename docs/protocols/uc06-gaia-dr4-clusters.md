# Protocol UC-06: star clusters in Gaia DR4, predeclared before the release

- Protocol id: `uc06-gaia-dr4-clusters`, version 1.0.0 (locked when the tag `uc06-prereg-v1` is pushed and deposited; until then edits are allowed and are listed under "History")
- Modules: `sdp_stellar.dr4_delta` (Part A), `sdp_stellar.companions` (Part B)
- Predeclared Part B settings: [`sdp_stellar/uc06.json`](../../python/packages/sdp-stellar/src/sdp_stellar/uc06.json). The hash of its deciding keys is pinned as `UC06_FROZEN_SHA256` (`f2b7970a858208d08fc2f974fec08756fd3a59d2ccfc9234134f1aa49f097d4c`), a test checks it, and `sdp companions screen --protocol uc06` refuses any other settings. Requirement STL-044.
- Lock date: Monday 24 November 2026. The Gaia archive goes offline on 25 November and Gaia DR4 is released on 2 December 2026.
- Rule of this protocol: it is committed, tagged and deposited publicly before any Gaia DR4 data is read. Anything changed after the lock is a dated, labelled amendment and is reported as such.

- Cluster list (DR3 seeds of every cluster screened): SHA-256 `4e4eb5f32a30e3a633d168a096f61aa5f15f7a02d4e20cb3ec217ec55986ccbd`, from the DR3 dry run of 18 November 2026. The DR4 run must give the same hash.

## Research questions

- **Part A (measurement, sure to give a result):** for the 24 seed clusters frozen on 5 November 2026, how do member counts, mean parallax, distance and mean proper motion change from Gaia DR3 to DR4 under one fixed selection?
- **Part B (test, may give a null):** do star-cluster members hold more unseen (dark) orbital partners than field stars of the same brightness and distance, and is any of them heavier than 1.4 solar masses?

Part A is already frozen (`sdp_stellar.dr4_delta.FROZEN_SHA256`, PR #191; see [docs/dr4-delta.md](../dr4-delta.md)). This protocol adopts it unchanged and adds nothing to it.

## Part B: hypotheses

| Id | Statement |
|---|---|
| UC06-H1 | Cluster members, taken together, hold more Gaia DR4 orbits with a dark partner than field stars of the same G and parallax. |
| UC06-H0 | They hold no more than the field. |
| UC06-H2 | At least one cluster member has a Gaia DR4 orbit whose dark partner's lower mass bound is above 1.4 solar masses and is heavier than any star still alive at the cluster's age. |

H1 is decided by one stacked test. H2 is a search: a hit is a named candidate, not a population claim.

## Part B: clusters and members (fixed from DR3, before DR4)

- **Cluster list:** the clusters of the DR3 whole-sky sweep (`gaia-dr3`, the run of 4 November 2026) with status known, inconclusive or potentially new, and at least 3 seed members at parallax 1 mas or more. This is the 52 clusters screened on 4 November. The 62 clusters whose seeds lie beyond 1 kpc are out of scope, and the screen now says so for each (`out_of_scope`).
- **Scope: 1 kpc** (accepted). The wobble a dark partner causes shrinks with distance, and the 4 November run already peaked at 23.6 GB.
- **Members:** found again in DR4 from each cluster's DR3 seed footprint, motion and parallax, blind to RUWE (STL-022), with the membership settings of `uc06.json` (the same as the DR3 trial's).
- **Large groups:** a group of more than 3,000 members is reported but left out of the stacked test, because the two DR3 near-misses (4,696 and 26,425 members) look like field structure, not clusters. The cap is 3,000 members (accepted).

Every screen writes the cluster list with each cluster's seed ids (`cluster-list.json`) and its SHA-256. The DR3 dry run's hash goes into version 1.0.0 of this protocol, and the DR4 run must give the same hash.

## Part B: the dark-partner test (decides H1)

- **Dark partner:** a member's Gaia DR4 two-body orbit judged by STL-020's rule at the cluster's parallax: no main-sequence flux ratio fits the partner (or Gaia's AMRF class III), so the partner is dark. Members with a neutron star or black hole candidate are counted with it and also reported on their own.
- **Field null:** each member's chance of having one is the field share of stars with a flagged orbit in its (G, parallax) bin, from the whole DR4 orbit table judged the same way.
- **Statistic:** members with a dark partner over all clusters in the test, against the total the field predicts. One-sided Poisson tail.
- **Verdict rule:** H1 if p < 0.001 **and** at least 3 members have a dark partner. H0 if p > 0.05. Anything between is inconclusive.
- **Per-cluster results** are reported with Holm-corrected p-values. They do not change the verdict.

## Part B: the wobble excess (reported, never decides)

- **Wobbler:** a star whose log RUWE is more than 3 robust sigma above the field median at its G (STL-021, z_min 3.0), the baseline recomputed from DR4 itself.
- **Field null:** matched on G, parallax and **crowding**: the number of Gaia sources, of any brightness or parallax, in the star's HEALPix level-11 pixel (about 3 square arcminutes, read from the source_id), in 4 groups split at the field's own quartiles. Cluster cores are crowded, and crowding alone can raise RUWE.
- It is summed and tested as above and reported with its DR3 baseline. It does not decide, because the DR3 dry run already showed it (below), and an excess of wobblers can come from ordinary binaries, leftover crowding or field stars among the members, not only dark partners.
- **Epoch astrometry:** if DR4 publishes per-transit astrometry for members, a residual-based wobble statistic is reported too. It never decides; its noise model is unknown until release.

## The DR3 dry run (18 November 2026)

Run on Leigh's PC with these settings on Gaia DR3 (commit 5489ff7, 1 h 11 min). Results: branch `claude/uc06-dr3-dryrun-data`, `data/uc06-dr3/`.

- Gaia BH1 flagged as a neutron star or black hole (dark partner 12.81 solar masses, lower bound 10.49).
- 114 clusters listed, 62 beyond 1 kpc, 52 screened (679,351 members), 32 of them over 3,000 members, so **20 clusters in the test**.
- 30 members have a DR3 orbit; **0 dark partners** (about 0.3 expected).
- Wobble excess: 1,419 wobblers against 1,265.1 expected (+12.2%, p = 1.1 × 10⁻⁵). Under the earlier draft this was H1 on DR3 itself, which is why the wobble no longer decides.

## Part B: dark partners (H2)

- DR4's two-body orbit table, judged by STL-020's rule: a partner is a neutron star or black hole candidate only when no main-sequence flux ratio fits it and its lower mass bound is above 1.4 solar masses.
- The member's mass and the cluster's age come from the MIST isochrone fit (STL-036). A partner is reported as heavier than any living star when its lower bound exceeds the turn-off mass at that age.
- The known-answer check is Gaia BH1. If the field run does not flag BH1, the run fails and no verdict is written.

## What would be reported whatever the outcome

The verdict, the counts and expected counts, the per-cluster table, every flagged orbit, the settings SHA-256s, and the list of clusters and stars dropped with the reason. A null is written up exactly as a positive would be.

## Known weaknesses, declared now

- The Gaia collaboration will publish its own DR4 black hole searches. This protocol's claim is its predeclared cluster-against-field comparison, not being first to any one object.
- DR4 column and table names are not public before release. Mapping them is allowed after the lock and is not an amendment, provided no selection value changes.
- Gaia DR3 gave about 0.3 expected dark partners in these clusters; DR4 should publish many more orbits, but if it does not, the test may end inconclusive for lack of power. That would be reported as such.
- 32 of the 52 screened groups exceed the 3,000-member cap, so the test rests on 20 clusters.
- Membership is found from DR3 seeds, so clusters DR3 missed cannot be tested.

## Outputs and timeline

1. Lock: this file at 1.0.0, frozen settings files, git tag `uc06-prereg-v1`, Zenodo deposit for a DOI (Leigh's account). By 24 November 2026.
2. Part A report: by mid December 2026.
3. Part B verdict: as a Research Note of the AAS by end of January 2027, citing the deposit.

## History

- 0.1.0, 17 November 2026: first draft.
- 0.2.0, 17 November 2026: Leigh accepted the 1 kpc scope, the 3,000-member cap, and epoch astrometry as reported only.
- 1.0.0, 18 November 2026: after the DR3 dry run already gave a wobble excess, Leigh chose dark partners to decide; the wobble excess is reported only. Cluster list hash added.
