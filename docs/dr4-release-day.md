# Gaia DR4 release day

Gaia DR4 is released on 2 December 2026. Protocol UC-06 ([protocol](protocols/uc06-gaia-dr4-clusters.md),
locked on 18 November 2026 as `uc06-prereg-v1`, doi:10.5281/zenodo.24354087) is run on it. This page
says what SDP does with a new release, what has to happen on the day, and what can go wrong.

## One file names every release

[`gaia_releases.yaml`](../python/packages/sdp-adapters/src/sdp/adapters/archives/gaia_releases.yaml)
holds, for DR2, DR3 and DR4: the TAP service, the tables SDP reads (`gaia_source`,
`nss_two_body_orbit`, `binary_masses`), the folder of the bulk files, any column the release renamed,
and the citation. Every command that reads Gaia asks this file:

| Command | What it reads from the file |
| --- | --- |
| `sdp archive bulk --source gaia --release dr4` | the bulk folder; the copy is stored beside DR3's (`bulk/gaia/gaia_DR4`) |
| `sdp companions fetch --release dr4` | the orbit, source and mass tables; writes `gaia-dr4-orbits.parquet` |
| `sdp companions field --protocol uc06` | the DR4 orbit table (the release UC-06 declares) |
| `sdp companions screen --protocol uc06` | the DR4 bulk copy for the field and the members, DR3's for the seeds |
| `sdp delta fetch --release dr4` | the `gaia_source` table; cones run as async jobs (#192) |

DR4's entry says `confirmed: false`, and every one of those commands refuses DR4 until it says
`confirmed: true`. Nothing is fetched under a guessed name.

**This is not an amendment to UC-06.** The protocol's "Known weaknesses" says DR4's table and
column names are mapped after the lock and that this is not an amendment as long as no selection
value changes. No frozen settings hash covers `gaia_releases.yaml`: `UC06_FROZEN_SHA256` covers
`uc06.json`, and `FROZEN_SHA256` covers the seed set, selection and questions in `dr4_delta.json`.
Both tests still pass unchanged.

## How the UC-06 screen reads two releases

The protocol fixes each cluster's footprint from its DR3 seed members before DR4 is read, and finds
the members again in DR4. The screen therefore reads the seeds from the DR3 bulk copy (pass 1b) and
the field, the crowding and the members from the DR4 copy. Both copies must be complete. With
`--stars dr3`, the screen is the DR3 dry run again, with every pass on DR3 as before.

## On the day

1. **Check the names.** From the cloud or the PC:
   `uv run sdp archive gaia-check --release dr4`. It asks the archive's `TAP_SCHEMA` for every table
   and column SDP reads and counts the files in the bulk folder's `_MD5SUM.txt`. It exits 1 and names
   each one that is missing. (Against DR3 it passes: 152 `gaia_source`, 77 `nss_two_body_orbit` and
   13 `binary_masses` columns, checked live on 25 November 2026.)
2. **Correct what it names** in `gaia_releases.yaml`: a table under another name goes in `tables`; a
   renamed column goes in `columns` as `sdp_name: dr4_name`. If Gaia publishes a DR3-to-DR4
   neighbourhood table, it goes in `dr4_delta.json` under `matches` (outside the frozen hash) in place
   of `identity`. Write any change down in the PR as a mapping.
3. **Confirm**: set `confirmed: true` and `confirmed_on: "2026-12-0X"`, complete the citation, and run
   the check again until it passes. Merge that PR.
4. **Run** `harness\run-gaia-dr4.ps1` on the PC. It checks the names again, then runs Part A (the
   24-cluster delta), fetches every DR4 orbit and checks Gaia BH1, copies the DR4 bulk files, and
   runs the Part B screen. Each step resumes. `-SkipCopy` leaves the copy and the screen for later.

## What can still go wrong

- **The bulk copy is big.** DR4 has about 2.0 billion sources against DR3's 1.8 billion, so expect
  a download and a stored copy about 10% larger than the DR3 copy on `D:` (not measured; ESA's file
  list on the day gives the count). `sdp archive bulk` stops when free space falls under
  `SDP_MIN_FREE_BYTES` (50 GB by default) and resumes where it stopped.
- **Gaia BH1's source_id may change in DR4.** The known-answer check looks for DR3 source_id
  4373465352415301632. If DR4 gives BH1 another id, the check fails and the verdict does not count.
  Fixing that would change a locked check, so it would be a dated, labelled amendment.
- **Columns may be renamed or dropped.** A rename is a mapping (step 2). A dropped column that a
  selection uses (`ruwe`, `parallax_over_error` inputs, the Thiele-Innes elements, `binary_masses`'
  flux ratios) cannot be mapped, and the protocol would need an amendment.
- **The archive will be busy.** Cones and orbit parts are async jobs with retries; each command keeps
  what it fetched, so running it again fetches only what is missing.
- **The DR3 seed copy must stay.** Do not delete or replace the DR3 bulk copy before Part B has run.
