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
2. Plate scale solved from the eclipse plates versus fixed from check plates, per instrument.
3. Astrographic set included, excluded, or down-weighted by its own scatter.
4. Independent versus per-plate correlated errors.
5. Robust (Student-t) versus Gaussian likelihood.
6. Prior width on alpha for the Bayesian comparison: 0.5, 1.0 and 2.0 arcsec.

## Gates

REL-G-01 to REL-G-09 as in the specification §9; each names the finding it evaluates in the predeclaration.

## Status of the oracle registry

The historical values in `sdp_relativity/oracles.yaml` are placeholders "as commonly reported" until they are transcribed from the cited page and verified by a second pass. A gate that references an unverified oracle cannot pass, so the first runs of this protocol are expected to return INCONCLUSIVE with the transcription gap named in the report. That is the designed behaviour, not a defect.

## Recoverability statement (REL-008)

The original plates are not available to this project. Every analysis is a reanalysis of published tables (1920) and of a published remeasurement (Harvey 1979); the dossier must say so and must never describe a table-based reanalysis as a plate remeasurement (REL-G-09).
