# harness: run scripts for the PC

Scripts that run SDP's long jobs on a Windows PC one after another, with nothing to type in between,
and keep a log of each run. They call the `sdp` command line from this checkout; they hold no science of
their own. (The TypeScript *spec* harness, `pnpm harness`, is a different thing: it lives in
`typescript/packages/spec-harness`.)

Every script assumes the [developer tools](../docs/guides/install-dev-tools.md) install: this checkout,
`uv`, and an `sdp-env.cmd` that sets `SDP_DATA`, the database and the other settings the SDP Command
Prompt uses. Run a script from an ordinary Command Prompt:

```bat
powershell -ExecutionPolicy Bypass -File harness\run-idle-pc.ps1
```

`-ExecutionPolicy Bypass` applies to that one run only; it changes no setting on the PC.

| Script | What it runs | Time | Log |
| --- | --- | --- | --- |
| `run-idle-pc.ps1` | Runs `-Branch` (main by default) in its own worktree; Praesepe 3x3 with `clusterer: fast-hdbscan` (#153), and 4x4 if the 3x3 took under 15 minutes; the Aquila stress stage 2 rerun (#147); `sdp local quality astro`, then `sdp local lightcurve astro` | 2 to 6 hours | `<SDP data>\reports\idle-pc\run-<date>_<time>.log` |
| `run-epoch-change.ps1` | Runs `-Branch` (main by default) in its own worktree; drafts the plan for `-Region` (a 29-arcsecond patch of the Pillars of Creation by default) under `profiles/image-changes.yaml`, stops if it is over `-MaxMB` (1500), else approves and executes it; prints each candidate's verdict, the images compared with their FWHM, zero point and alignment, the images left out and the null-field rate (INT-025) | 10 to 30 minutes, mostly MAST | `<SDP data>\reports\epoch-change\run-<date>_<time>.log` |
| `run-sweep-rerun.ps1` | Runs `-Branch` (main by default) in its own worktree; renames the last sweep's `tiles` and `candidates.csv` with the date; band 57, then `sdp discover sweep --workers 12` and `sdp discover report`; prints candidates by status beside `candidates-before.csv` (#159) | about 70 minutes | `<SDP data>\reports\sweep\run-<date>_<time>.log` |
| `run-companions.ps1` | Runs `-Branch` (main by default) in its own worktree; `sdp companions fetch` (every Gaia DR3 orbit with Gaia's masses, skipped when the table is already there, `-Refetch` to download again); `sdp companions field` (says whether Gaia BH1 is flagged, which it must be); `sdp companions screen --sweep gaia-dr3` over the bulk copy; prints members with a dark partner and the clusters with the most wobblers against the field | fetch about 10 minutes, resumes after a timeout; the screen is two passes over the bulk copy, not yet timed on the PC | `<SDP data>\reports\companions\run-<date>_<time>.log`, results in `<SDP data>\companions` |
| `run-claim-audit.ps1` | Runs `-Branch` (main by default) in its own worktree; fetches hr23, cga20 and cwnu23 unless stored; `sdp discover audit --limit 20` as a timed trial, then the whole audit and `sdp discover audit-report` (STL-024). Resumable: run it again to carry on | several hours (the trial prints seconds per claim) | `<SDP data>\reports\audit\run-<date>_<time>.log`, results in `<SDP data>\audit\claims\audit.csv` |
| `run-publication.ps1` | Runs `-Branch` (main by default) in its own worktree; takes the NASA ADS token from `-AdsToken`, `ADS_API_TOKEN` in the environment or your checkout's gitignored `.env`, and stops at once saying which line to add when there is none (`-AskToken` asks for it instead, hidden, and saves it in `.env`; never logged); `sdp publication programs` (PUB-004): every JWST program at MAST joined to the ADS papers that name it. Resumable across ADS's daily allowance | 1 to 2 hours for about 850 programs | `<SDP data>\reports\publication\run-<date>_<time>.log`, results in `<SDP data>\publication\jwst\unpublished.csv` |
| `run-delta.ps1` | Runs `-Branch` (main by default) in its own worktree; `sdp delta fetch` for `-From` and `-To` (dr2 and dr3 by default; dr3 and dr4 from 2 Dec 2026), then `sdp delta compare` (STL-030..032). Resumable: fetched cones are kept | minutes per release for the 24 clusters | `<SDP data>\reports\delta\run-<date>_<time>.log`, results in `<SDP data>\delta\deltas-<from>-<to>.csv` |
| `run-pillars-plan.ps1` | Runs `-Branch` (main by default) in its own worktree; `sdp integration register`; drafts the Pillars of Creation plan for `-Region` (the 2014 Hubble framing by default, see docs/pillars.md) under `profiles/pillars-of-creation.yaml`; saves the plan and prints what it keeps, what it leaves out and why, and the total against the 2 GB cap. It reads FITS headers only and approves nothing | a few minutes, mostly MAST | `<SDP data>\reports\pillars\run-<date>_<time>.log`, plan in `plan-<date>_<time>.json` |
| `run-contradictions.ps1` | Runs `-Branch` (main by default) in its own worktree and registers the archive descriptors, stopping if either fails; runs `profiles/survey-contradictions.yaml` on M67 (`-CatalogueRegion`) and `profiles/image-contradictions.yaml` on a 29-arcsecond patch of the Pillars of Creation (`-ImageRegion`, stops before fetching if over `-MaxMB` 1500, `-SkipImages` to leave it out); prints each run's null-field rate, every candidate's pattern and each comparison that disagreed with its difference and z, and the images left out (INV-026 to INV-029) | a few minutes for M67; 10 to 30 minutes for M16, mostly MAST | `<SDP data>\reports\contradictions\run-<date>_<time>.log` |
| `run-survey-copies.ps1` | Runs `-Branch` (main by default) in its own worktree; `sdp archive bulk --source 2mass` (IRSA's 92 PSC files) then `--source allwise` (IRSA's Parquet by byte range, AWS when IRSA does not answer), our columns only; prints how much of each copy is stored. `-Only 2mass` or `allwise`; rerun to resume. Once a copy is complete, runs read it instead of IRSA (DSI-024) | not timed on the PC; from the cloud sandbox one 2MASS file took 87 s and one AllWISE file about 5 s, so a few hours each | `<SDP data>\reports\survey-copies\run-<date>_<time>.log`, copies in the artifact store under `bulk\2mass` and `bulk\allwise` |

## Where a run happens

A script never switches the branch of your checkout (#196). It fetches `origin/<-Branch>` and runs in a
**worktree** of it beside your checkout, `<checkout>-runs\<branch>` (for `D:\code\SDP` on main,
`D:\code\SDP-runs\main`), then runs *that* worktree's copy of the script with the same parameters. So:

- your checkout stays on whatever branch you had, with your edits untouched;
- a script that exists only on a PR branch is found: start it from main's copy with `-Branch <the PR branch>`;
- the log starts with the branch, the commit and the worktree that ran, and `SDP_REPO_ROOT` (and
  `SDP_PROFILES_DIR`, when the env file points it into your checkout) are set to the worktree;
- runs of different branches can go at the same time; two runs of the same branch share its worktree.

The first run of a branch makes its worktree and its `python\.venv` (`uv sync`, a few minutes). Later runs
move the worktree to the newest commit, discarding any edit made inside it (untracked files such as the
`.venv` stay). Remove a worktree you no longer need with `git worktree remove D:\code\SDP-runs\<branch>`.
`SDP_RUN_WORKTREES` keeps them in another folder. Files your checkout ignores, such as `.env`, are not in a
worktree: `run-publication.ps1` hands the ADS token on through the environment.

`harness\tests\run-checkout.tests.ps1` checks this on Windows PowerShell in CI.

## How the scripts behave

- **One job at a time**, so each job's timings are its own.
- **A failed job is logged and the next one runs.** The summary at the end of the log gives each job's
  result and time.
- **Nothing is deleted.** `run-idle-pc.ps1` writes one new profile, `<SDP data>\profiles\stress-groups-fast.yaml`
  (a copy of `stress-groups.yaml` with a new `profile_id` and the fast clusterer), and only when it is not
  there yet. It never touches `<SDP data>\sweep`. `run-sweep-rerun.ps1` renames the previous `tiles` folder and `candidates.csv` rather than deleting them.
- **Options:** `-EnvFile PATH` when `sdp-env.cmd` is not at `D:\SDP\sdp-env.cmd`, and `-Code PATH` to run
  another checkout (by default, the one the script sits in).

## Adding a script

Name it `run-<what>.ps1`, give it the same `Step` and `Sdp` helpers so its log reads the same way, add a
row to the table above, and say in the PR how long it took on the PC.
