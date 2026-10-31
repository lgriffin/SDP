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
| `run-idle-pc.ps1` | Updates the checkout to main; Praesepe 3x3 with `clusterer: fast-hdbscan` (#153), and 4x4 if the 3x3 took under 15 minutes; the Aquila stress stage 2 rerun (#147); `sdp local quality astro`, then `sdp local lightcurve astro` | 2 to 6 hours | `<SDP data>\reports\idle-pc\run-<date>_<time>.log` |
| `run-sweep-rerun.ps1` | Updates the checkout (`-Branch`, main by default); renames the last sweep's `tiles` and `candidates.csv` with the date; band 57, then `sdp discover sweep --workers 12` and `sdp discover report`; prints candidates by status beside `candidates-before.csv` (#159) | about 70 minutes | `<SDP data>\reports\sweep\run-<date>_<time>.log` |

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
