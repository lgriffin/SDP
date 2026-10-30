# Journey: reproduce Einstein's 1919 eclipse test

**Time:** 5 minutes · **Download:** nothing · **Needs:** the [developer tools](install-dev-tools.md)
(Node.js 22.18 or newer for step 2); a database only for part B

In May 1919, two British expeditions photographed the stars around the eclipsed Sun, from Sobral in
Brazil and Príncipe off West Africa. If Einstein's general relativity was right, starlight passing the
Sun's edge would be bent by **1.75 arcseconds**; Newtonian physics gave half that, **0.87**. Dyson,
Eddington and Davidson published the measurements in 1920 (*Phil. Trans. R. Soc. A* 220, 291) and
reported 1.98″ from Sobral's 4-inch telescope, 0.93″ from Sobral's astrographic telescope, and 1.61″
from Príncipe.

SDP's UC-02 study rebuilds those numbers from the paper's printed tables, then re-analyses them under
four hypotheses and 216 ways of handling the plates, with a second fitter written independently in
TypeScript as a cross-check. The method is in
[docs/protocols/uc02-einstein-1919.md](../protocols/uc02-einstein-1919.md).

## The data

Every number comes from the 1920 paper itself. The 2,079 printed values are in
[experiments/uc02-einstein-1919/transcription/dyson-1920.csv](../../experiments/uc02-einstein-1919/transcription/dyson-1920.csv),
one row per printed cell, with its page, table, plate, star, unit and a `check` column recording who
re-read it against the page and when. A measurement only counts as **verified** when every cell it was
built from has a recorded check; one person's careful second pass is enough. Open the file in Excel to
see it.

## Part A: the quick run (no database)

From the `python` folder of your copy (Windows shown; use `/` on Linux and macOS):

```bat
cd /d D:\code\SDP\python
mkdir ..\reports\uc02
uv run python -m sdp_relativity.paper1920 ..\experiments\uc02-einstein-1919\transcription\dyson-1920.csv ..\reports\uc02\bundle.json
```

**Step 1 success:** three lines, one per instrument:

```
principe-astro: 4 plates, 20 star rows
sobral-4in: 7 plates, 49 star rows
sobral-astro: 16 plates, 79 star rows
```

Step 2, the independent fitter (from the repository's top folder):

```bat
cd ..
node typescript\packages\relativity-fit\src\cli.ts reports\uc02\bundle.json --out reports\uc02\independent-fit.json
cd python
```

It prints nothing and writes `independent-fit.json`. (If Node says it cannot run a `.ts` file, update
Node to 22.18 or newer.)

Step 3, the whole protocol:

```bat
uv run python -m sdp.testing.emit ..\reports\uc02 --protocol uc02-einstein-1919 --input observations=..\reports\uc02\bundle.json --input independent_fit=..\reports\uc02\independent-fit.json
```

**Success:** it prints the path of the evidence manifest it wrote,
`..\reports\uc02\experiments\uc02-einstein-1919\exp-000001\manifest.json`, then the verdict,
`INCONCLUSIVE`. That verdict is expected; the next section says why. On a laptop the run takes under a
minute (add `--param bootstrap_resamples=200` to make it quicker still while you experiment).

`just cross-language` does all three steps and also passes the manifest through the specification
harness.

## Part B: the recorded run (with the database)

The same experiment through SDP's full lifecycle, so it lands in your database and ledger and can be
bundled and checked by anyone. Steps 1 and 2 from part A first, then:

```bat
uv run sdp predeclaration freeze uc02-einstein-1919 --signer leigh
```

This **freezes** the study's plan ([predeclaration.json](../../experiments/uc02-einstein-1919/predeclaration.json))
into the ledger, for good. Read it first. It prints a `predeclaration_hash` (`sha256:…`). Then:

```bat
uv run sdp experiment define uc02-einstein-1919 --predeclaration sha256:… --input observations=..\reports\uc02\bundle.json --input independent_fit=..\reports\uc02\independent-fit.json
uv run sdp experiment run exp-…
uv run sdp experiment validate exp-…
uv run sdp experiment show exp-…
```

`define` prints the `experiment_id` (`exp-…`) to use in the rest. `run` takes about 15 seconds;
`validate` prints every gate and the verdict; `show` prints the whole evidence manifest. Then make the
dossier and the shareable bundle:

```bat
uv run sdp dossier assemble exp-… --limitation "Measurements transcribed from the 1920 report; the plates were not remeasured."
uv run sdp bundle dos-… --out ..\reports\uc02\einstein-1919.zip
```

The [releases and checking results](releases-and-verification.md) guide shows how anyone re-runs that
bundle and checks it matches.

## Reading the result

The verdict is decided by nine **gates**, REL-G-01 to REL-G-09. On the transcribed data every gate passes
except **REL-G-07**, which reports `oracle_unverified`: it compares against a later published
re-analysis, Gilmore and Tausch-Pebody (2020), whose numbers have not been transcribed and checked yet.
A gate may not use an unchecked reference, so the verdict stays **INCONCLUSIVE** rather than guessing.
That is the system doing its job.

The gates worth reading (in `validate`'s output, or the manifest's `checks` and `findings`):

| Finding | What it says | On 29 October 2026 |
|---|---|---|
| `unverified_measurement_count` (REL-G-02) | Measurements with any unchecked cell | 0 |
| `historical_reproduction_max_z` (REL-G-04) | How far SDP's rebuild of the 1920 reduction lands from the published values, in standard errors; must be at most 1 | 0.73: Sobral 4-inch 2.02 against 1.98, astrographic 0.96 against 0.93, Príncipe 1.65 against 1.65 (before the paper's final −0.04 correction to 1.61) |
| `independent_fitter_max_abs_diff_arcsec` | The largest difference between the Python and TypeScript fitters | about 1e-13 arcsec |
| `gr_limb_arcsec`, `half_limb_arcsec` | The two predictions | 1.751 and 0.876 |
| `alpha_sobral_4in_arcsec` and the other `alpha_*` | SDP's own re-analysis per instrument, with its uncertainty | 2.06 ± 0.17, 0.96 ± 0.17, 2.11 ± 0.47 |

Every number in the dossier traces back to the cells it was built from, so a reader can find each one on
the page.

## Checking a transcribed cell yourself

If you re-read a cell against the paper, record it: put `checked by <your name> <YYYY-MM-DD>` in that
row's `check` column. A cell you correct gets a new value **and** a new check. Then run part A again.
The paper itself is not in the repository; keep your own copy (doi
[10.1098/rsta.1920.0009](https://doi.org/10.1098/rsta.1920.0009)). Never type values from memory: the
whole point is that each one can be found on its printed page.

## If it goes wrong

- **`FileNotFoundError` for `bundle.json`.** The `reports\uc02` folder does not exist yet: run the `mkdir`
  line.
- **REL-G-02 fails.** A row's `check` column is empty or malformed. The finding's value says how many
  measurements are affected.
- **`experiment define` is refused** in part B. It needs the hash of a frozen predeclaration: run the
  `freeze` line, and copy its `predeclaration_hash` exactly, `sha256:` included.
