# Releases, and checking results

Two kinds of checking happen in SDP. This guide covers both:

- **checking a release:** that a numbered version of SDP is what it says it is;
- **checking a result:** that a run, an experiment or a dossier really came out as recorded, which anyone
  can do with the file alone.

## How a release is made

1. Every change reaches `main` through a pull request whose checks all pass (the single `ci-ok` check).
2. A bot (release-please) keeps an open pull request called **chore(main): release X.Y.Z**. It lists every
   `feat:` and `fix:` change since the last release, which becomes the changelog. On 29 October 2026 that
   was [#53](https://github.com/lgriffin/SDP/pull/53), for 0.3.0.
3. **Merging that pull request is the release.** It tags the version and publishes a release page, and
   the release workflow then runs on the tagged code:
   - every CI stage again;
   - the mutation scores (how many deliberately planted bugs the tests catch; [docs/testing.md](../testing.md));
   - **T9**: a predeclared experiment re-run from its bundle in a fresh container with no network;
   - the container images, pushed to the GitHub registry and pinned by digest;
   - the SBOMs and the release report, below.

Only the person who owns the repository merges the release pull request.

## What a release page holds

<https://github.com/lgriffin/SDP/releases>

| File | What it is |
|---|---|
| `SDP-Setup-<version>.exe` | The Windows installer ([install guide](install-desktop.md)) |
| `release-report.md`, `release-report.json` | The specification harness's report: which requirements have passing tests, the mutation scores, T9's result, and the conformance level claimed |
| `sbom-*.cdx.json` | Software bills of materials (CycloneDX): every package, at its exact version, inside the locked code and each image |
| `quadlet-pinned.tar.gz` | The server's service files, with each image named by its exact digest |

v0.2.0 (29 October 2026) has none of these attached yet; the desktop installer comes from CI until a
release attaches it.

## Checking a release

- **Read `release-report.md`.** It should claim Level 1, list every requirement with a passing test, and
  show T9 as passed. A release whose report is missing or failing is not one to rely on.
- **Check an image is the released one.** Each service file in `quadlet-pinned.tar.gz` names its image as
  `ghcr.io/lgriffin/sdp-kernel@sha256:…`. Podman or Docker refuses to run an image whose bytes do not
  match that digest, so pulling by digest is itself the check:
  ```sh
  podman pull ghcr.io/lgriffin/sdp-kernel@sha256:<digest from the file>
  ```
- **The installer is not yet code-signed**, so Windows cannot vouch for it. Take it only from the
  repository's own release page or CI run. Once the repository holds a signing certificate, the
  installer is signed and SmartScreen stops warning.
- **Which code ran.** Every run records the code revision, platform and Python version in its manifest
  (`environment`). On the developer-tools route, keep `SDP_REPO_ROOT` pointing at the copy you actually
  run, or the recorded revision will be wrong.

## Checking a result

Every check below runs on the file alone and exits with code **0** when everything matches and **1** when
anything does not, naming what differs. That makes them usable in scripts too.

### An exploration: `sdp investigate reproduce`

```bat
sdp investigate bundle invset-… --out field.zip
sdp investigate reproduce field.zip
```

`bundle` writes the set's evidence dossier (an RO-Crate zip). `reproduce` re-runs the test stage from that
file alone, with no database and no archive, and compares every result within the tests' numerical
tolerance. **Success:** `"ok": true`, the number of `results_compared`, and an empty `differences` list. It
refuses a file whose checksums do not match. See the [JWST journey](jwst-field.md).

### A predeclared experiment: `sdp reproduce`

```bat
sdp bundle dos-… --out experiment.zip
sdp reproduce experiment.zip --inputs <folder with the input files>
```

`--inputs` is a folder holding the experiment's input files (for the Einstein study, `bundle.json` and
`independent-fit.json`); they are found by checksum, so their names do not matter. **Success** (checked on
the Einstein study on 29 October 2026, about 12 seconds):

```json
{
  "differences": [],
  "ok": true,
  "original_verdict": "INCONCLUSIVE",
  "reproduced_verdict": "INCONCLUSIVE",
  "tolerance": {"atol": 1e-12, "rtol": 1e-09}
}
```

`just reproduce experiment.zip inputs/` does the same inside a fresh container with no network, which
is how the release checks it (T9). It needs Podman or Docker
([infrastructure/README.md](../../infrastructure/README.md#reproducing-a-release)).

### The record of who did what: `sdp ledger verify`

```bat
sdp ledger verify
```

It checks every ledger entry's chain link and signature. **Success:** `"problems": []` and the number of
entries. Any edited, removed or wrongly signed entry is named. Entries made with `SDP_ALLOW_UNSIGNED=1`
show `"method": "unsigned"`: honest, but not proof of who made them. Set up a
[signing key](install-dev-tools.md#a-signing-key-only-for-signed-steps) before recording anything you
will rely on.

## From lead to claim

1. Explore: any journey's candidates are EXPLORATORY leads.
2. Write the lead into a predeclaration: the region, the cuts, the tests and the pass marks.
3. Freeze it (`sdp predeclaration freeze`), so it cannot be tuned afterwards.
4. Define, run and validate the experiment; the gates give the verdict.
5. Assemble the dossier with its limitations stated, and approve it (`sdp approve <experiment> --reviewer
   <you>`, signed with your key when one is set). One person's approval is enough for a single-user installation.
6. Bundle it, and let others `sdp reproduce` it. Publishing it with a DOI (`sdp publish`) needs a Zenodo
   token and goes to Zenodo's sandbox first ([docs/implementation-status.md](../implementation-status.md)).

The [Einstein journey](einstein-1919.md#part-b-the-recorded-run-with-the-database) walks steps 3 to 6
with real commands.
