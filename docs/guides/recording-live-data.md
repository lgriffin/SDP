# Journey: record live archive data

**Time:** a few minutes · **Needs:** a GitHub account with access to the repository; for option 2, the
[developer tools](install-dev-tools.md) and permission to push

SDP's automatic tests never call a real archive: they would be slow, and they would fail whenever an
archive is down. Instead they replay **cassettes**, recordings of real archive answers kept in
`python/tests/cassettes/`. A cassette is only ever written by a live run, never by hand. This journey
refreshes them, which also tells you whether any archive changed its tables.

The full reference is [docs/live-data.md](../live-data.md). This page is the short version.

## Option 1: from GitHub (nothing to install)

1. Open <https://github.com/lgriffin/SDP/actions/workflows/archive-smoke.yml>.
2. Click **Run workflow**, keep `main`, and click the green **Run workflow** button.
3. Wait for the run to finish (a few minutes).

**Success:** the run is green, and a new branch `archive-cassettes/run-<number>` holds the recordings.
Open a pull request from it; its checks replay the new recordings. If one archive was down, the run still
pushes what did record, and the summary names the archive that failed.

The same workflow also runs by itself every Monday, then only reporting changes, not pushing.

## Option 2: from your own computer

Use this when GitHub's machines cannot reach an archive (CDS has blocked them before), or to watch it run.

```bat
cd /d D:\code\SDP
git switch main
git pull
cd python
uv sync --frozen
uv run python tools\record_live_data.py
```

It runs four steps and prints a summary:

1. **Live smoke:** asks every archive (Gaia, PS1, 2MASS, MAST, DASCH, SIMBAD and the registry) for its
   table columns and a small cone of Pleiades stars, saving each answer. A column that appeared,
   vanished or changed type is named.
2. **X-Match check:** sends the cross-match test cases to CDS X-Match and keeps its answers.
3. **Replay:** runs the replay tests on just these recordings.
4. **Branch:** commits only `python/tests/cassettes/` to a new branch `archive-cassettes/local-<date>`
   and pushes it. Open a pull request from that branch.

Add `--no-push` to record and check without committing anything. While CDS is down, add `--skip-xmatch`
to leave out step 2; everything else still records, and a later run fills in X-Match. **Success:** exit code 0 and a summary
listing what passed. Exit code 1 names the failing archive or step.

## What is still missing (29 October 2026)

| Recording | Why | Unlocks |
|---|---|---|
| A 2MASS cone at the Pleiades | IRSA timed out on an earlier run | The 2MASS replay tests, which skip until then |
| CDS X-Match answers | CDS was unreachable from GitHub and from the PC | Requirement DSI-009: X-Match agreeing with SDP's own cross-match |

One successful run of option 2 while CDS is up fills both.

## Related tools

- `sdp integration record` records one real JWST product and MAST's description as a reference product;
  with `--max-bytes` it pulls a full-size product to your own disk instead
  ([live-data.md](../live-data.md#full-size-jwst-products-on-your-own-disk)).
- `python/tools/stress_pull.py` runs a list of fields one after another and writes a CSV of rows, bytes,
  seconds and memory ([live-data.md](../live-data.md#measuring-a-pull); issue
  [#67](https://github.com/lgriffin/SDP/issues/67) is the stress run).
- `sdp artifact usage` says how big the artifact store is and how much free space is left.
