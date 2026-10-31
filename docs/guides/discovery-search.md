# Journey: hunt for star clusters

**Time:** minutes for one field; a day or more for the whole sky · **Download:** a few MB for one
field; 200 to 350 GB for all of Gaia · **Needs:** the [first look](first-look.md) done; for parts 2
to 5, the [developer tools](install-dev-tools.md) or the desktop app's SDP Command Prompt, and
400 GB free

Stars born together keep moving together: same direction across the sky, same distance. A group of them
is an **open cluster** or **moving group**. This journey looks for such groups, first in one field you
choose, then across the whole sky from your own copy of Gaia, and finally in the predeclared experiment
(UC-01) that can turn a lead into a result.

| Part | What | Where it runs | Time |
|---|---|---|---|
| 1 | Search one field with the moving-group-search profile | Any install | Minutes |
| 2 | Copy all of Gaia DR3 to your disk | Your PC, unattended | Hours |
| 3 | Fetch the catalogues of known clusters | Your PC | Seconds |
| 4 | Sweep the whole sky for clusters | Your PC, unattended | Hours |
| 5 | Run the predeclared cluster experiment | Your PC | 10 minutes to an hour |

On the developer-tools route, put `uv run` in front of each `sdp` command and run it from the `python`
folder after `call D:\SDP\sdp-env.cmd`.

## Part 1: search one field

**Praesepe** (the Beehive cluster, in Cancer) is a real cluster, so it is a good test that the search
finds what it should. A radius of 0.9° gives 4,099 stars, just under the profile's 5,000-star cap.

The **moving-group-search** profile asks four questions: do some stars share a motion and distance
(clustering with HDBSCAN), does each star's distance stand out, do they lie on a tight colour–brightness
sequence, and do 2MASS infrared colours agree with Gaia's? The
[profiles guide](../../profiles/README.md#tests) explains each one.

In the app: **Start**, then **Explore a field** with RA `130.1`, Dec `19.67`, radius `0.9`, tests
`moving-group-search`, then **Plan the downloads**, **Approve** and **Download now**.

In a terminal:

```bat
sdp plan draft --region "icrs;circle(130.1,19.67,0.9)" --profile "%SDP_DATA%\profiles\moving-group-search.yaml" --label "Praesepe 0.9" --actor leigh
sdp plan approve <plan_id> --actor leigh
sdp plan execute <plan_id> --actor leigh
sdp investigate sets
sdp investigate list <set_id>
```

**Success:**

- `plan draft` lists a Gaia item and a 2MASS item, `gaps` is empty, and `total_bytes` is about 4 MB.
- `execute` ends with every stage `completed` and about 4,099 objects examined.
- Praesepe comes out as a strong **group** candidate.

The 2MASS cross-check pairs each Gaia star with its 2MASS counterpart on your own machine, so it needs
no service beyond Gaia and IRSA. Setting `SDP_CROSS_MATCH=cds` sends that step to CDS X-Match instead.

## Part 2: all of Gaia on your own disk

Asking Gaia's archive for big areas is slow and impolite. ESA publishes the whole catalogue as about
3,400 compressed files, each one patch of sky, and asks people who want everything to copy those.
`sdp archive bulk` does that: it checks each file against ESA's published checksum, keeps the 27 columns
SDP's tests read, and stores each as one Parquet table in the artifact store. It needs no database.

Check you have room: at least **400 GB free** on the drive that holds `SDP_ARTIFACT_ROOT`.

**Step 1, one file (a minute):**

```bat
sdp archive bulk --count 1
```

**Success:** a line like `1/1 GaiaSource_000000-003111.csv.gz stored 528,333 rows 63.8 MB 6.4 s`, then a
JSON summary with `"done": 1` and `"files"` around 3,386. On 29 October 2026 that one file pointed to
about 216 GB and 1½ to 3 hours for the whole release at four downloads at once.

**Step 2, the rest (leave it running; overnight is fine):**

```bat
sdp archive bulk --workers 4
```

- Each file prints one line when it is stored. Lower `--workers` if your connection struggles; raise it
  (up to 16) if it has headroom.
- **Ctrl+C is safe.** Run the same command again to carry on: stored files are skipped.
- A `failed` line is one bad download. The rest carry on, the command ends with exit code 1, and the next
  run retries it.
- It stops by itself before a file if free space would drop below `SDP_MIN_FREE_BYTES`.

**Step 3, check it:**

```bat
sdp archive bulk --status
```

**Success:** `"done"` equals `"files"`, and `"rows"` is about 1,811,709,771, every star in Gaia DR3.

Once the copy is complete, every run reads Gaia fields from it instead of the archive, with the same rows
in the same order and no waiting. `SDP_GAIA_SOURCE` chooses: `auto` (the default: the copy once it is
complete), `tap` (always the archive) or `bulk` (always the copy). The details are in
[docs/live-data.md](../live-data.md#all-of-gaia-dr3-on-your-own-disk) and issue
[#76](https://github.com/lgriffin/SDP/issues/76).

## Part 3: the catalogues of known clusters

A group is only *potentially new* if it matches nothing in two catalogues of known clusters: Hunt &
Reffert 2023 (`hr23`) and Cantat-Gaudin et al. 2020 (`cga20`). Both come from VizieR, at CDS.

```bat
sdp benchmark fetch
sdp benchmark list
```

**Success:** `fetch` takes seconds, stores a few hundred kB per catalogue, and prints the
`--input benchmark:<id>=sha256:…` options the experiment uses; `list` shows both with a stored copy (not
`null`).

**When CDS is down:** `fetch` tries VizieR's own mirrors in turn (CDS's classic VizieR, then Harvard,
India, China, South Africa and Japan) and says which one served each catalogue. Set
`SDP_ARCHIVE_MIRRORS=0` to use only CDS. If none answers, open the catalogue on any VizieR mirror in a
browser (for example `https://vizier.cfa.harvard.edu/viz-bin/VizieR?-source=J/A+A/673/A114/clusters`
and `...?-source=J/A+A/640/A1/table1`), choose all columns, unlimited rows and the **VOTable** output, save it,
and read the saved files instead:

```bat
sdp benchmark fetch --file hr23=C:\Users\you\Downloads\hr23.vot --file cga20=C:\Users\you\Downloads\cga20.vot
```

Columns are still chosen by what each one measures and its unit, so a file with a column in the wrong unit is
refused, never guessed, and so is a file whose table is not the catalogue named before the `=` ([#107](https://github.com/lgriffin/SDP/issues/107)).

## Part 4: sweep the whole sky

The sweep cuts the sky into tiles about 2° across, searches each one for clusters (with a half-degree rim
so a cluster on an edge is seen whole), checks every find against the pass marks and both catalogues,
and runs each tile once more on shuffled motions to measure false alarms. It reads only your local copy.

```bat
sdp discover prepare --count 2
sdp discover prepare
sdp discover sweep --bands 57
sdp discover report
```

- `prepare --count 2` reads two bulk files into the sweep's cut catalogue as a quick test. `prepare` on
  its own then does the rest: several hours for all files at `--workers 4`, writing 10 to 20 GB under
  `%SDP_DATA%\sweep\gaia-dr3`. It keeps only stars that pass the quality cuts, about one in twenty.
- `sweep --bands 57` searches one 2° strip of sky. Band numbers count up from the south pole: band *n*
  covers declination −90 + 2*n* to −88 + 2*n*, so band 57 (Dec +24° to +26°) holds the Pleiades, a
  known cluster the sweep should find and label as known. Give a range such as `--bands 40:49`, or leave
  `--bands` out for the whole sky. Until Part 3 has worked, add `--without-benchmarks` (then nothing can
  be called new).
- `report` gathers every tile done so far.
- Each candidate also gets a significance in sigma, the `significance_sigma` column of
  `candidates.csv`: how much more tightly its members crowd together than the stars around them. Below
  3 sigma it is rejected. A potentially new candidate must also be more significant than every false
  candidate the tile's shuffled null field produced, so one noisy tile no longer hides a real cluster.
- The sweep judges each candidate's colour-magnitude sequence with every member placed at the cluster's
  own distance (the mean of its members' parallaxes), and compares it with field stars at that same
  distance. A member's own parallax error no longer blurs the sequence, so faint real clusters stop
  failing the check. Tiles swept before this change were made with other settings, and `sweep` refuses to
  mix them. Keep the old results by renaming `%SDP_DATA%\sweep\gaia-dr3\tiles` to `tiles-before`
  (and its `candidates.csv` to `candidates-before.csv`), then sweep again: the prepared catalogue is
  reused, so nothing needs preparing twice.

Stop any step with Ctrl+C and run it again to carry on. **Success:** `report` prints a line like
"N tiles searched (… stars). Candidates: … Null fields with a false candidate: …%" and writes
`%SDP_DATA%\sweep\gaia-dr3\candidates.csv`, potentially new candidates first. Open it in Excel.

Every sweep result is EXPLORATORY, because the sweep was not predeclared region by region. A
potentially new candidate is a lead to write into a predeclaration and confirm in Part 5, never a result.

## Part 5: the predeclared experiment (UC-01)

This is the run that can issue a verdict. Its regions, quality cuts, search settings and pass marks are
written in [experiments/uc01-stellar-clusters/predeclaration.json](../../experiments/uc01-stellar-clusters/predeclaration.json)
and explained in [docs/protocols/uc01-stellar-clusters.md](../protocols/uc01-stellar-clusters.md).

**Before you run it, read the predeclaration.** The first run **freezes** it into the ledger, and a frozen
predeclaration can never change. That is the point: nobody can tune the plan after seeing the answer.

```bat
sdp discover run --signer leigh
```

It reads the four predeclared regions from Gaia (your local copy if complete, else the archive), adds
both catalogues from Part 3, runs every search, null field and planted-cluster test, and checks the gates.
Expect 10 minutes to an hour. For a quicker look first, add `--null-fields 3 --bootstrap-resamples 100`;
that run is recorded too, but as a different run from the predeclared one.

**Success:** a verdict (PASS, FAIL or INCONCLUSIVE) with each gate STL-G-01 to STL-G-09 and its value.
INCONCLUSIVE with a gate naming missing benchmarks means Part 3 has not worked yet.

With the developer setup's `SDP_ALLOW_UNSIGNED=1`, `--signer` is recorded as a name. For a result you
will share, make a [signing key](install-dev-tools.md#a-signing-key-only-for-signed-steps) first.

## If it goes wrong

- **`row_cap_reached` in a run's output.** The field holds more stars than the profile reads (5,000
  here). Pick a smaller radius.
- **`sdp discover sweep` says a folder holds tiles from other settings or an older SDP.** A results
  folder belongs to one set of settings. Give a new `--name`, or delete that folder.
