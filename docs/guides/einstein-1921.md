# Journey: rerun Einstein's 1921 globular-cluster calculation

**Time:** about 1 minute for the arithmetic; 5 to 15 minutes for the full run · **Download:** nothing if your bulk Gaia copy is complete · **Needs:** the [developer tools](install-dev-tools.md) and, for the full run, the bulk copy from [Hunt for star clusters](discovery-search.md)

In 1921 Einstein wrote a three-page paper applying Newton's gravity to the globular cluster in Hercules (M13). He used the virial theorem: in a settled cluster, the stars' kinetic energy is half the size of their gravitational energy. Nobody could measure how the cluster's stars moved, so he borrowed 26 km/s from bright stars near the Sun. He guessed 2,000 stars of 15 suns each. The cluster came out at under a light-year across, far too small, so he multiplied the number of stars by 100 and concluded that the invisible mass is "not of a higher order of magnitude" than the visible mass. He ended by saying better results needed better star masses and speeds.

Gaia measured those speeds. This journey checks Einstein's arithmetic, then reruns his calculation on Gaia's stars. The method was fixed before any M13 data was read, in [docs/protocols/uc03-einstein-1921.md](../protocols/uc03-einstein-1921.md).

## Step 1: the 1921 arithmetic (no data)

From the `python` folder of your copy:

```
uv run sdp einstein1921 arithmetic
```

You should see JSON. Look at `A_printed` (0.6, Einstein's number) beside `A_plummer` (0.2945). For the density law Einstein wrote down, the right factor is 3π/32. His 0.6 is twice that, as if every pair of stars were counted twice. `corrected_coefficient` shows what that does to his radius: 65 light-years becomes about 37.

## Step 2: the full run

This reads the stars within 0.6° of M13 from your bulk Gaia copy, so `SDP_ARTIFACT_ROOT` must point at it, as it did for the cluster sweep.

```
uv run sdp einstein1921 run --out reports\uc03
```

It prints its progress and ends with a line such as `Verdict supports_H1. Wrote reports\uc03\report.json and reports\uc03\report.md.` Open `report.md` first.

For a quick look before the full run, add `--resamples 5 --baseline-only`. It takes about a minute. The report then says it is a quick look and that its verdict is not the protocol's.

If you have no bulk copy, export the cone yourself as Parquet with the `gaia_source` column names, and pass `--parquet path\to\cone.parquet`.

## What the report means

- **Part A** sets the paper's printed numbers beside the recomputed ones.
- **Baseline** is the predeclared main choice of settings. Here are its numbers next to Einstein's:
  - the cluster's distance, from Gaia's parallaxes;
  - its size `2a`, against Einstein's "at least 100 light-years";
  - the rms speed of its stars, against his borrowed 26 km/s;
  - its mass from those motions, against his 2,000 × 15 suns;
  - its light, and the mass-to-light ratio. The 16 to 84% range comes from refitting 100 resampled copies of the stars.
- **Verdict** applies the predeclared rule to the mass-to-light ratio:
  - `supports_H1`: below 10 in the baseline range and in all 48 scenarios. Einstein's conclusion holds.
  - `supports_H0`: 10 or more everywhere. It does not hold.
  - `inconclusive`: anything in between. The report names the scenarios on each side.

The light misses stars fainter than G = 19. That makes the ratio too high, so a `supports_H1` result is conservative.

## Send it back

Commit nothing from `reports\`. Send `report.json` and `report.md` to the thread, and we will write up the result.
