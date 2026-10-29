# Getting real data into SDP

Tests and CI never call a live archive. They replay **cassettes**, which are recordings of real
archive answers kept in `python/tests/cassettes/`. A cassette is only ever written by a live run.
Nobody types one in by hand. This page covers the recordings still missing and the things only a
person can do.

## What is still missing

| What | Why it is missing | What it unlocks |
|---|---|---|
| 2MASS cone at the Pleiades | IRSA timed out on the second smoke run. It answers in seconds now, so a rerun should record it | The 2MASS replay tests, which skip until then |
| CDS X-Match answers | The smoke never called X-Match | DSI-009: the remote cross-match agreeing with the CPU reference on the contract fixture |

Gaia DR3, PS1 DR2 and the 2MASS schema are already recorded. One run records everything again, so
the missing pieces arrive with fresh copies of the rest.

## Option 1: from GitHub (no setup)

1. Open the repository's **Actions** tab and pick **Archive live smoke**.
2. Click **Run workflow** on `main`.
3. When it finishes, a new branch `archive-cassettes/run-<number>` holds the recordings. Open a pull
   request from it. The PR's CI replays them, and the 2MASS and X-Match tests run instead of skipping.

If one archive is down, the run still pushes what did record, so you can merge that much and run it
again later for the rest.

## Option 2: from your own machine

Use this when GitHub's runners can't reach an archive, or when you want to watch it run. You need
`git`, [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and push access to the
repository. It works the same in a Linux or macOS terminal, in PowerShell on Windows, or in WSL.

```
git switch main
git pull
cd python
uv sync --frozen
uv run python tools/record_live_data.py
```

It takes a few minutes and runs these steps:

1. **Live smoke.** It asks every archive for its table columns and a small cone of stars in the
   Pleiades: Gaia, PS1, 2MASS, and the MAST, DASCH, SIMBAD and registry services. Each answer is
   saved. A column that appeared, vanished or changed type is reported by name.
2. **X-Match check.** It sends the cross-match test cases to CDS X-Match and keeps its answers. It
   fails if X-Match disagrees with the expected matches.
3. **Replay.** It runs the replay tests on this run's recordings alone. The
   summary at the end says what passed and which tests still skip.
4. **Branch.** It commits only `python/tests/cassettes/` on a new branch,
   `archive-cassettes/local-<date>`, and pushes it. Open a pull request from that branch.

Add `--no-push` to record and check without committing anything. The script exits with status 1 if
any step failed, and the output names the failing archive.

## Measuring a pull

Every pull now says what it cost:

- `sdp archive acquire` prints `rows`, `raw_bytes`, `curated_bytes`, `stored_bytes` (everything it
  added to the store, its parts included), `requests` (archive calls), `seconds` and `truncated`.
  A repeated read reuses what is stored: `requests` and `stored_bytes` are then 0.
- `sdp investigate`, `sdp investigate sets` and `sdp plan execute` print a `timing` block with the
  seconds and `stored_bytes` of each stage (identify, retrieve, test, candidates).
- `sdp artifact usage` prints the store's objects and bytes, per media type, and the free space
  against `SDP_MIN_FREE_BYTES`, the floor below which acquisition is refused. It needs only
  `SDP_ARTIFACT_ROOT`.

To run a list of fields one after another and get a CSV back:

```
cd python
uv run python tools/stress_pull.py fields.csv               # each field once
uv run python tools/stress_pull.py fields.csv --repeat 2    # the second pass times reuse
uv run python tools/stress_pull.py fields.csv --parallel 3  # three at once
```

`fields.csv` has a header row. A row with a `profile` is a run (`label,region,profile`); a row with a
`source` is an archive read (`label,source,region,row_cap,columns`, cones only, blank `columns` reads
every declared column). The report lands in `reports/stress/<date>-<time>.csv` with rows, bytes,
seconds, peak memory (when `psutil` is installed) and the error text of any run that failed.

## Things only a person can do

None of these can be scripted, because each one rests on human judgement or on your own account.

- **The 1919 measurements (UC-02).** The eclipse numbers have to be copied from the 1920 paper
  (Dyson, Eddington and Davidson, *Phil. Trans. R. Soc. A* 220), one printed row at a time, each with
  its page and table. Then go over every row a second time against the paper and record that check.
  One person is enough: SDP is single-user, so your own second pass counts. The second pass exists
  because a single typo would change the verdict, and nobody could tell afterwards. Until both steps
  are done, UC-02 reports INCONCLUSIVE on purpose, never a made-up value.
- **Signing off the experiment plans.** Both predeclarations (`experiments/*/predeclaration.json`)
  are drafts. Someone confirms the regions, quality cuts and tolerances, then freezes them with
  `sdp predeclaration freeze`. After that they can't change, which is the point.
- **The UC-01 benchmark catalogues.** Hunt and Reffert 2023 and Cantat-Gaudin et al. 2020 need their
  release, licence and citation confirmed from the publications before they become inputs.
- **Publishing a DOI.** This needs your Zenodo token. Publish once to the Zenodo sandbox first; the
  settings are in `docs/implementation-status.md`.
- **Mutation baselines.** Run the **Mutation** workflow once with `update` ticked, then review the
  branch it pushes.
- **The DGX Spark spike.** It runs on that machine; the steps are in `docs/spike/phase0-dgx-spark.md`.

## All of Gaia DR3 on your own disk

`sdp archive bulk` copies the whole of Gaia DR3 `gaia_source` from ESA's bulk files (about 3,400
gzipped files listed in ESA's `_MD5SUM.txt`), not through TAP, as ESA asks for whole-catalogue copies.
Each file is checked against its published MD5, cut down to the 27 columns SDP reads, and stored as
one Parquet artifact under `bulk/gaia/gaia_DR3/` in the artifact store, with a JSON record of its URL,
MD5 and row count beside it. It needs only `SDP_ARTIFACT_ROOT` (and `SDP_MIN_FREE_BYTES`), not the
database.

```
sdp archive bulk --count 1        # one file first: about 500,000 stars
sdp archive bulk --workers 4      # then the rest; Ctrl+C any time
sdp archive bulk --status         # files done, rows and bytes stored, free space
```

Run the same command again to resume: stored files are skipped and a file that failed is tried again.
The sweep stops cleanly before a file when free space falls below `SDP_MIN_FREE_BYTES`.

Once every file in ESA's list is stored, SDP reads Gaia fields from the copy instead of the archive:
runs, investigations and `sdp archive acquire` get the same rows, in the same order, with no TAP call.
Only the files whose sky box overlaps the field are opened. `SDP_GAIA_SOURCE` chooses:

| Value | Gaia fields come from |
|---|---|
| `auto` (default) | the copy once it is complete, the archive until then |
| `tap` | always the archive |
| `bulk` | always the copy; a run is refused while any file is missing |

A dataset read from the copy records its query as `BULK SELECT ...`, so it never passes for an archive
read and is not reused for one.
