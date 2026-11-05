# Gaia DR4 delta for the seed clusters

Discovery Roadmap capability C5. What does a new Gaia release change for a named set of clusters?
Gaia DR4 is released on 2 December 2026. A note in the first 90 days needs the questions and the
selection fixed beforehand, and the engine tested on a release pair that already exists: DR2 to DR3.

Requirements STL-030 to STL-032 (`requirements/stellar/dr4-delta.ears`). Science in
`sdp_stellar/dr4_delta.py`, settings in `sdp_stellar/dr4_delta.json`, archive access in
`sdp/adapters/archives/gaia_delta.py`, commands in `sdp/cli/delta.py`, PC script
`harness/run-delta.ps1`.

## The seed set and the questions

The 24 clusters accepted on 5 November 2026 (roadmap section 9), named as Hunt & Reffert 2023
(HR23) names them. For each, under the same selection in both releases:

1. member count;
2. mean parallax (inverse-variance weighted) and distance;
3. mean proper motion;
4. earlier members kept, lost (matched but no longer selected), unmatched (no counterpart), and new.

## The selection (frozen 5 November 2026)

A star is a member when it lies within HR23's tidal radius `rt` of HR23's centre, has G <= 18,
parallax_over_error >= 5, a parallax within 3 sigma of HR23's cluster parallax (its error and the
cluster's spread `s_Plx` in quadrature), and a proper-motion chi-squared from HR23's mean of at most
11.83 (errors and spreads in quadrature, 3 sigma for two degrees of freedom). The cluster parameters
stay HR23's in every release, so a change is the release's and not the selection's.

Each release's cone is fetched with a parallax window that provably holds every parallax the cut can
pass (a member has error at most parallax/5, so its parallax solves a quadratic bound), never
narrower than 1 mas or five spreads either side. The local cut alone decides membership.

Leigh confirmed the seed set, this selection and the questions on 5 November 2026, after the DR2 to DR3
dry run. They are frozen: `sdp_stellar.dr4_delta.FROZEN_SHA256` is their hash, a test checks it, and
`sdp delta compare` refuses a DR4 comparison under anything else. The release tables and matching
rules stay outside the freeze, so DR4's table names can be filled in on release day.

NGC 2264 stays in the seed set with a caveat, which the CSV carries in its `caveat` column. In the dry
run only 45% of the stars the rule selected in DR3 were HR23 members (93 to 99.5% for the others): the
cluster is young and spread out, so its deltas include field stars.

## Matching stars across releases

DR2 to DR3 uses Gaia's `gaiadr3.dr2_neighbourhood` (nearest neighbour). DR3 to DR4 uses source_id
until Gaia publishes a DR4 neighbourhood table; on release day the settings name it instead.

## Sanity check

In DR3 the selection is compared with HR23's own member list. On M67 (2026-11-05) the selection
found 971 stars: 99.5% are HR23 members, and they are 52% of HR23's 1,844, because HR23 reaches fainter
than G = 18 and beyond `rt`. The rule is strict, not leaky.

## Commands

```
sdp delta fetch --release dr2      # one cone per cluster, kept as Parquet; reruns skip what is kept
sdp delta fetch --release dr3      # also keeps HR23's DR3 member lists
sdp delta compare --from dr2 --to dr3
```

Each kept cone carries the query that fetched it. A cone fetched under other settings is fetched again,
and `compare` never uses one (the summary lists it). A cluster an archive fails on (an error, a
refused answer, an unknown name) is named, the rest carry on, and the command exits 1; running it
again fetches only what is missing. The summary also lists any DR3 cluster compared without HR23's
member list.

Outputs in `<SDP data>/delta`: `seeds.json` (HR23 rows), `<release>/<cluster>.parquet`,
`hr23-members/`, `matches/dr2-dr3/`, `deltas-dr2-dr3.csv` and `summary-dr2-dr3.json` (EXPLORATORY,
with the settings hash). HR23's tables come from VizieR's classic interface at CDS or the first
official mirror that answers.

## The DR2 to DR3 dry run (5 November 2026)

18 of the 24 clusters were compared; Gaia's archive timed out on the rest. Every cluster's mean
parallax rose, by +0.018 to +0.063 mas, and the nearby clusters rose most. Faint and distant
clusters gained the most members because DR3's errors are smaller (NGC 7789 2,454 to 2,957; Berkeley 39
30 to 97). Nearby clusters changed by a few percent.

## Known effects a DR2 to DR3 dry run shows

DR3 changed the parallax zero point (about +0.02 mas against DR2) and the proper-motion reference
frame (about 0.1 mas/yr at the bright end). A dry-run delta of that size is the release's
calibration, not the clusters. The DR4 note should report deltas next to DR4's own zero-point
correction.
