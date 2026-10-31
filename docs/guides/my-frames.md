# Journey: your own telescope frames

**Time:** a minute to index; seconds per stacked frame and 1 to 2 seconds per sub to solve ·
**Written to disk:** under 1 GB · **Needs:** a folder of FITS frames, the
[developer tools](install-dev-tools.md) or the desktop app's SDP Command Prompt, and the database
running (for solving and checking)

This journey plugs your own pictures into SDP. It indexes the folder, works out exactly where on the sky
each frame points (plate solving, with SDP's own solver), and then asks of your frames what the
[JWST journey](jwst-field.md) asks of JWST's: is each Gaia star where its motion puts it on the night you
took the frame?

**SDP only reads your folder.** It never writes, moves or renames anything there. Everything it makes
goes under SDP's data folder (`SDP_DATA`): the index in `<data>\index\<name>`, the sky solutions in
`<data>\solutions\<name>`, and run outputs in the artifact store. The data folder may not be inside your
frames folder.

The examples use Leigh's Vaonis Vespera Pro frames in `D:\astrophotography` (about 20,000 FITS files;
1.65 arcseconds per pixel). Issue [#90](https://github.com/lgriffin/SDP/issues/90) is the same journey as
a checklist. On the developer-tools route, run everything with `uv run` from `D:\code\SDP\python` after
`call D:\SDP\sdp-env.cmd`.

## Step 1: add and index (no database needed)

```bat
sdp local add astro D:\astrophotography --scale 1.65
sdp local index astro
sdp local status astro
```

- `add` names the folder `astro`. `--scale` is your camera's arcseconds per pixel, used when a frame's
  header has no focal length. For another telescope: 206.265 × pixel size in µm ÷ focal length in mm.
- `index` reads only each file's header, never its pixels: a few minutes per 10,000 frames on a USB
  drive. Running it again reads only new or changed files.

**Success:** `status` shows your frames by kind (light, dark, flat, bias, stack), your targets with frame
counts and exposure hours, and no errors, or only a handful.

## Step 2: name your targets, then solve the stacks

```bat
sdp local names astro --fetch
sdp local solve astro --kind stack
sdp local solved astro
```

`names --fetch` looks up each target name your frames use in SIMBAD once and keeps the answers in
`<data>\index\astro\names.yaml`. After that, solving needs no internet, as Gaia is read from your local
copy. A name SIMBAD does not know can be given by hand: `sdp local names astro --centre "M31=10.68,41.27"`
(right ascension and declination in degrees).

Some frames have no target name in their header at all. `sdp local names astro` lists their folders
under `folders_of_frames_without_object`. SDP first tries each folder's name as a target (`raw\IC434`
is looked up as IC434), and records that the centre came from the folder name. When that doesn't
work, give the folder's centre by hand, and it covers every folder inside it:
`sdp local names astro --folder "raw/IC434=85.25,-2.46"`. Frames in folders named `darks`, `biases`
or `flats` are indexed as calibration frames even when the header says LIGHT, so they are never solved.

For each frame SDP takes a rough centre from the target name in its header (from the names file), finds
the stars, and matches the triangles they make against Gaia stars brighter than G = 15 in a 2° circle,
whatever the frame's rotation or mirroring. It accepts a fit with at least 12 matched stars and an error
under 1.5 arcseconds. Each solution is written as `<data>\solutions\astro\<frame>.wcs.json`. A frame
that cannot be solved with confidence is refused, with its reason, in `<frame>.unsolved.json`.

**Success:** `solved` lists most stacks as solved. A refusal says why, for example too few stars
matched, or no target name to start from.

## Step 2a: find your frames on the sky

```bat
sdp local coverage astro
sdp local find astro --at M31 --radius 0.5 --from 2025-09-01 --grade usable --csv D:\SDP\m31-frames.csv
```

`coverage` lists each target with its nights, frames solved, refused and still to do, and hours. `find`
lists the solved frames that cover a place, oldest first, with each frame's night, grade and star width.
`--at` takes a name from the names file or `RA,DEC` in degrees. Both rebuild the catalogue,
`<data>\catalogue\astro\frames.parquet`, which any tool that reads Parquet can open. `sdp plan` now sees
your frames too, so it can plan an investigation over them and show what it would read.

## Step 2b: see how good each night was

```bat
sdp local quality astro --target M31
```

Solving measures every frame: star width, roundness, sky brightness, how much light got through (cloud
or haze), how faint it reaches, and satellite trails. `quality` grades each frame good, usable or poor
against the other frames of the same night's folder, with the reason in words, and lists each night with
its frames by grade, hours and median star width. Poor frames are left out of the checks below.

## Step 3: check a target against Gaia

```bat
sdp investigate --region "icrs;circle(10.6847,41.2687,0.2)" --profile ..\profiles\my-frames.yaml --label m31-frames
```

In the desktop app's SDP Command Prompt the profile is `"%SDP_DATA%\profiles\my-frames.yaml"`. The region
is the core of M31, the Andromeda galaxy, radius 0.2°. SDP cuts a 30-arcminute square around it out of
each solved, dated frame, so keep the radius at 0.25° or less.

**Success:** every stage `completed`, and the `timing` block shows the retrieve stage reading your
stacks. Very few stars, ideally none, should be flagged. A flag is a lead, not a claim: it is often a
satellite trail, the frame's edge, or a star lost in M31's bright core. Open the candidate to see why.

The profile's settings (stars between G = 9 and 14, the 3-pixel star width, the 4-arcsecond search
radius) assume a Vespera Pro. For another telescope, copy `my-frames.yaml` and change them, as the
[profiles guide](../../profiles/README.md#my-frames-your-own-frames) explains.

## Step 4 (optional, long): solve the subs

```bat
sdp local solve astro --target M31 --limit 50
sdp local solved astro
```

Try 50 first and look at `frames_per_minute` in the result, then drop `--limit` (and `--target`) for the
rest; `sdp local solve astro` on its own does every stack, then every sub, overnight. Each sub starts from
the solution of the sub next to it in its folder, which skips most of the work, and `--workers 4` (the
default) solves four runs of a folder at once; on a USB drive, reading the files is likely to be the
limit. Ctrl+C is safe: running it again skips every frame already solved or refused.
`--retry-refused` tries the refused ones again; `--max-rms` and `--min-matches` loosen or tighten the fit.
Change the profile's `missions` from `[stack]` to `[light]` to check every solved sub instead of the stacks.

## Step 5 (optional): open the solutions in other software

Every solved frame gets a `.wcs` file beside its solution in `<data>\solutions\astro\`, which astropy and
other tools that accept an external WCS header can read. For software that reads only a frame's own
header, export solved copies; the pixels are copied byte for byte and your originals are not touched:

```bat
sdp local sidecars astro
sdp local export astro --kind stack
```

The copies go to `<data>\exports\astro\` at the same relative paths. A sub is about 28 MB, so export
stacks, or one `--target`, rather than everything.

## Step 6: check your originals are intact

```bat
sdp local verify astro
sdp local manifest astro
```

Solving records a fingerprint (SHA-256) of every frame it reads. `verify` reads every file again and
lists any that are missing, changed, or whose bytes no longer match although the size and date are the
same, which is what a failing drive does. It reads all 575 GB, so expect about an hour; `--quick` checks
sizes and dates only, in seconds. `manifest` writes a CSV of every frame's path, size and fingerprint
that a backup tool can check a copy against.

**Success:** `verify` ends with `"intact": true`. It only reads; a fingerprint changes only if you run it
with `--accept`.

## Step 7 (optional): light curves from your subs

Once the subs of a target are solved and graded (Step 4, then `sdp local quality astro`):

```bat
sdp local lightcurve astro
sdp investigate --region "icrs;circle(10.6847,41.2687,0.3)" --profile ..\profiles\my-frames-variability.yaml --label m31-variables
```

`sdp local lightcurve` picks the target whose subs cover the most nights (or give `--target M31`),
fetches the Gaia stars of G 9 to 15 over those subs, and measures each star on every solved sub not
graded poor. It uses the green pixels of the raw frame, the colour closest to Gaia's G band, and places
each star from the frame's solution, so no star finding is needed. Each frame is calibrated against the
field's steadiest stars, so haze and the target's height in the sky cancel out. The curves go to
`<data>\lightcurves\astro\M31.parquet`, with a nightly average and a summary beside it.

**Success:** the result lists `precision_by_g`, how much the steady stars scatter at each brightness.
Expect about 0.01 to 0.02 mag at G 10 to 12 on a clear night. `candidates` lists the stars that scatter at
least three times more than stars of their brightness. The investigate run then tests every star the
same way it tests WISE's light curves, against the scatter its neighbours show. A flag is a lead: check
that the star is not at the edge of the frames or next to a brighter star.

## Step 8 (optional): certify your frames for the planner

`sdp plan` uses your frames either way. Until they are certified, a plan marks them as exploratory
evidence, the same as any archive SDP has not yet admitted. Certifying runs the four checks SDP asks of
every archive's adapter, on your own drive:

```bat
sdp local certify astro --template
notepad %SDP_DATA%\compatibility\astro\reference.yaml
sdp local certify astro
```

`--template` writes a reference file naming three solved frames, one per target and night where it
can. For each one, write down its `kind` (light or stack), `target`, `date_obs` (UTC, to the minute),
and `exposure_s`. Take these from your capture log or session notes, not from SDP's output. `filter`,
and the `ra_deg`/`dec_deg` you aimed at, are optional. Then go through the file again as a separate
check. Once every value matches your notes, set `status: verified` and `verified_by: Leigh`. If one of
the frames is a sub of the target you measured in Step 7, the light curves are certified too.

`sdp local certify` then checks four things. Contract: searches and fetches answer as the planner
expects. Operational: the bytes served are the bytes that were solved, and nothing outside the folder
is served. Semantic: SDP reads each frame as you wrote it down. Scientific: each solution fits Gaia
within the solver's limits, and astropy puts the corners where SDP does. It writes one record per
product type in `<data>\compatibility\astro\`.

**Success:** `"admitted": true` for `image` (and `timeseries` once you have light curves), and the
command exits 0. A failed check says what SDP read and what you wrote. If a reference frame later
changes or is graded poor, its admission is withdrawn until you certify again.

More detail on indexing, solving and the cutouts is in
[docs/live-data.md](../live-data.md#your-own-frames).
