# Protocol UC-02: re-examination of the 1919 eclipse deflection

- Protocol id: `uc02-einstein-1919`, version 1.0.0
- Module: `sdp_relativity` (architecture spec §9)
- Draft predeclaration: [`experiments/uc02-einstein-1919/predeclaration.json`](../../experiments/uc02-einstein-1919/predeclaration.json)

## Research question

Carried from SDD 0.2.0 §11.2: given only what the 1920 report and later remeasurements published, which of the four deflection hypotheses (none, half, general-relativistic, free amplitude) do the 1919 Sobral and Príncipe measurements support, per instrument, and how much does that conclusion depend on reduction choices the original authors made?

## Hypotheses

| Id | Statement |
|---|---|
| UC02-H0 | No deflection (alpha = 0) |
| UC02-H1 | Half deflection, alpha(b) = 2GM/(c²b) |
| UC02-H2 | General-relativistic deflection, alpha(b) = 4GM/(c²b) |
| UC02-H3 | Free limb amplitude alpha |

## Inputs

Three observation sets, never pooled without a `PoolingJustification`: Sobral 4-inch, Sobral astrographic, Príncipe astrographic. Each measurement is transcribed with its page or table locator and uncertainty convention (`standard` or `probable_error`) and must carry a second-pass verification record before it may enter a fit (REL-G-01, REL-G-02).

## Predeclared scenario axes (REL-006)

1. Error convention: probable error taken as standard error versus converted (factor 1.4826).
2. Plate scale solved from the eclipse plates (one scale and rotation per plate, or the 1920 reduction's six constants per plate) versus fixed from check plates or calculated refraction, per instrument.
3. Astrographic set included, excluded, or down-weighted by its own scatter.
4. Independent versus per-plate correlated errors.
5. Robust (Student-t) versus Gaussian likelihood.
6. Prior width on alpha for the Bayesian comparison: 0.5, 1.0 and 2.0 arcsec.

## Gates

REL-G-01 to REL-G-09 as in the specification §9; each names the finding it evaluates in the predeclaration.

## Status of the oracle registry

A gate that references an unverified oracle cannot pass. The three 1920 values in `sdp_relativity/oracles.yaml` (Sobral 4-inch, Sobral astrographic, Príncipe) were transcribed from the cited pages and re-read against the page images on 2026-10-29, so REL-G-04 can now judge them. The 2020 reanalysis that REL-G-07 compares against is not transcribed yet, so a run on the 1919 data returns INCONCLUSIVE with that gap named in the report. That is the designed behaviour, not a defect.

## Recoverability statement (REL-008)

The original plates are not available to this project. Every analysis is a reanalysis of published tables (1920) and of a published remeasurement (Harvey 1979); the dossier must say so and must never describe a table-based reanalysis as a plate remeasurement (REL-G-09).

## Implementation

| Piece | Where |
|---|---|
| Constants (CODATA 2022, IAU 2015), recorded in every run | `sdp_relativity.constants` |
| Uncertainty conventions; a probable error cannot become a weight unconverted (REL-002) | `sdp_relativity.measurement` |
| The four models, limb value from the constants (REL-003) | `sdp_relativity.models` |
| Observation bundle (`sdp-relativity/observations@1`), per-instrument sets, `PoolingJustification` (REL-004) | `sdp_relativity.observations` |
| Transcribed records to a bundle, keeping every locator; second-reader corrections (REL-001) | `sdp_relativity.transcription` |
| Scenario axes and the 216-point sweep (REL-006); the 1920 reduction of each instrument (REL-010) | `sdp_relativity.reduction` |
| The 1920 tables as records and a bundle (REL-009) | `sdp_relativity.paper1920`, `experiments/uc02-einstein-1919/transcription/dyson-1920.csv` |
| GLS and Student-t (IRLS, nu = 4) fits, bootstrap, evidence and posterior | `sdp_relativity.fitter` |
| Hypothesis comparison per instrument and scenario (REL-005) | `sdp_relativity.comparison` |
| Oracle registry; the analytic oracles and the three 1920 values are verified | `sdp_relativity/oracles.yaml` |
| Dossier limitations and the remeasurement-claim lint (REL-008) | `sdp_relativity.dossier` |
| Plan, interpretation and gates | `sdp_relativity.protocol`, `.analysis` |
| Independent fitter (REL-007) | `typescript/packages/relativity-fit` |
| Synthetic bundles with a planted deflection | `python -m sdp_relativity.synthetic out.json --alpha 1.75 --seed 1919` |

### The fit

Per plate and axis, `dx = a_x + s x + r y + alpha g_x` and `dy = a_y + s y - r x + alpha g_y`, with `g` the response of a 1/b deflection normalised to the limb. Plate scale and rotation are solved from the eclipse plate; or, as the 1920 reduction did (p. 304), six constants are solved, `dx = a_x + s_xx x + s_xy y + alpha g_x` and `dy = a_y + s_yx x + s_yy y + alpha g_y` (REL-010); or the scale is fixed. A fixed scale subtracts the known linear terms (one check-plate scale, or four terms where refraction stretches one axis more than the other) and the plate's rotation when it is known, and solves the rotation when it is not. A constant no measurement touches, as on plates measured in one coordinate only, is left out. When fixed, the per-plate correlated-error scenario adds the check-plate scale variance as a rank-one block per plate. The astrographic axis acts on the pooled fit: included, excluded, or down-weighted by its own reduced chi-square. Bayes factors use a Gaussian prior on alpha centred midway between the half and GR values, so the prior favours neither.

### The 1919 data

`experiments/uc02-einstein-1919/transcription/dyson-1920.csv` holds 2,079 values read from the page images of the 1920 report, each with its page; `python -m sdp_relativity.paper1920 dyson-1920.csv bundle.json` builds the bundle (REL-009). A record counts as verified only when every cell it cites has a check recorded in the CSV's `check` column. The module docstring says how each instrument's rows follow the paper:

- Sobral 4-inch: Table II eclipse values less the mean of the seven comparison plates (which removes the intermediary scale plate), at 6.25 arcsec per revolution; for a fixed scale, the p. 307 refraction and aberration and the adopted scale and orientation.
- Sobral astrographic: the p. 311 declination measures at 12.3 arcsec per revolution; for a fixed scale, the p. 312 refraction and aberration term, with the orientation solved.
- Principe: the y measures after the calculated plate constants are removed (the paper's Delta_1, units of 0.003 arcsec), with each comparison's orientation from its x measures; plate U against K2 is left out as on p. 327.
- Positions are recentred on the Sun, found from the printed displacement coefficients.

The 1920 reduction of each instrument (`reduction.historical`) reproduces the published values: Sobral 4-inch 2.02 arcsec against 1.98, astrographic 0.96 against 0.93, Principe 1.65 against 1.65 before the paper's final -0.04 correction (1.61 published).

Rebuilding the 1920 normal equations (Tables III-VI) from Table II reproduces every sum except three cells, each read correctly from the page: eclipse plate I star 2 Dx (printed -0.789, the equations used -0.798), comparison plate 14_2a star 2 Dx (printed -0.396, the equations imply -0.345), and plate VIII's Dy constant (-1.322 printed, -1.332 implied, which cannot change the deflection). Solving the 1920 normal equations themselves gives exactly 1.98 with the authors' working values and 2.01 with the printed ones; the three cells are pinned in `test_paper1920.py`. The bundle uses the printed values; this module's six-constant fit of them gives the 2.02 above, the 0.01 coming from how the plates are combined.

### Running it

```
python -m sdp_relativity.paper1920 experiments/uc02-einstein-1919/transcription/dyson-1920.csv reports/uc02/bundle.json
node typescript/packages/relativity-fit/src/cli.ts reports/uc02/bundle.json --out reports/uc02/independent-fit.json
cd python && uv run python -m sdp.testing.emit ../reports/uc02 --protocol uc02-einstein-1919 \
    --input observations=../reports/uc02/bundle.json --input independent_fit=../reports/uc02/independent-fit.json
```

For synthetic data with planted deflections, replace the first line with `python -m sdp_relativity.synthetic reports/uc02/bundle.json`. Every cell of the 1919 transcription has a recorded check (a full re-read against the page images on 2026-10-29), so every record is verified; a record whose cells were not all checked would stay unverified.

`just cross-language` runs this on the 1919 data and passes the manifest through `spec-harness conform` and `rederive`. The verdict is INCONCLUSIVE: REL-G-07 reports `oracle_unverified` and every other gate passes, REL-G-04 included. Synthetic data is not the 1919 data, so its tests judge it with the oracle verifications removed, where REL-G-04 and REL-G-07 both report `oracle_unverified`; the tests in `python/tests/relativity/test_protocol.py` plant each fault a gate guards against and check that gate fails.
