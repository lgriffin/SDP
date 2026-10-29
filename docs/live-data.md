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

## Full-size JWST products on your own disk

`sdp integration record` refuses a product over 35 MB, the most a committed reference product may be.
For a local pull, give `--max-bytes` (`500MB`, `2GB`; 1 MB is 1024² bytes) and an `--out` outside
`fixtures/`; a larger limit inside `fixtures/` is refused, so a big file is never committed by accident.
The product is streamed to disk a megabyte at a time and is not kept in a cassette; the output gives its
bytes, seconds and SHA-256.

```
cd python
uv run sdp integration record --obs-id jw01538-o069_t001_nircam_clear-f277w \
  --filename jw01538-o069_t001_nircam_clear-f277w_i2d.fits --name p177d-f277w-full \
  --out ../reports/local-products --cassettes ../reports/cassettes --max-bytes 400MB
```

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
