# Protocol UC-03: Einstein's 1921 globular-cluster calculation, rerun with Gaia

- Protocol id: `uc03-einstein-1921`, version 1.0.0
- Module: `sdp_relativity.einstein1921`
- Predeclared choices: [`sdp_relativity/einstein1921.json`](../../python/packages/sdp-relativity/src/sdp_relativity/einstein1921.json). Every run records that file's SHA-256, and a run whose file differs from the committed one says so.
- Written and committed on 2026-10-31, before any Gaia star in the field of M13 was read for this protocol.

## The source

A. Einstein (1921), "Eine einfache Anwendung des Newtonschen Gravitationsgesetzes auf die kugelförmigen Sternhaufen", *Festschrift der Kaiser-Wilhelm-Gesellschaft zur Förderung der Wissenschaften*, Springer, Berlin, pp. 50–52 (CPAE 7, Doc. 56). Every equation and number below was read from the German scan. The English translation we hold is of unknown origin and was used only as a reading aid.

Einstein applies the virial theorem, `L = ½Φ` (eq. 1), to the cluster in Hercules (M13). He treats the brighter stars as `N` stars of equal mass `m` with root-mean-square speed `v` (`L = N·mv²/2`, eq. 2), and describes the cluster by the density law `ρ = (3/4π)(N/a³)(1 + r²/a²)^(−5/2)` (eq. 3), today called a Plummer sphere. That gives `Φ = A·kN²m²/a` (eq. 4), with "A ein Zahlenfaktor, für den ich etwa 0,6 finde", and `2a = 1,2·kNm/v²` (eq. 5). Here `2a` is the radius where the density has fallen to about 2% of the central density.

His inputs were `N = 2000`, `m = 15` solar masses and `v = 26 km/s`. The speed was not measured in the cluster: it was taken from bright stars near the Sun ("die Ortsänderungen der Sterne sind ... zu gering"). He obtained `2a = 0,65·10¹⁸ cm = 0,65 Lichtjahre`, against at least 100 light-years from the cluster's distance. After raising the number of stars by a factor of about 100, he got 65 light-years. He concluded that "die nichtleuchtenden Massen zur Gesamtmasse keinen Beitrag von höherer Größenordnung liefern als die leuchtenden Massen", and that "Genauere Ergebnisse sind an eine bessere Kenntnis der Sternmassen und Sterngeschwindigkeiten gebunden".

## Research question

With Gaia's measured stellar motions in place of Einstein's borrowed 26 km/s, and his own density law fitted to Gaia star counts, does M13's dynamical mass bear out his conclusion that non-luminous mass is not of a higher order of magnitude than luminous mass?

## Part A: the 1921 arithmetic (no data)

These are checks of the paper, run on every invocation:

1. For eq. (3), the factor in eq. (4) is `A = 3π/32 ≈ 0.2945`, from the closed form and from numerical integration of eq. (3). Einstein's "etwa 0,6" equals `3π/16` to within 2%, the value obtained if each pair of stars is counted twice. Eq. (5) then reads `2a = 2A·kNm/v²`: `1.2` as printed, `0.589` with `A = 3π/32`.
2. Eq. (5) with Einstein's inputs, as printed and as corrected, in cm and light-years, next to his printed `0,65·10¹⁸ cm` and `0,65 Lichtjahre`, and the same with the factor of 100.

The constants come from `sdp_relativity.constants` (CODATA 2022, IAU 2015).

## Hypotheses

| Id | Statement |
|---|---|
| UC03-H1 | Einstein's 1921 conclusion holds for M13: the dynamical mass-to-light ratio in Gaia's G band, in solar units, is below 10. |
| UC03-H0 | It does not: that ratio is 10 or more. |

The threshold of 10 is Einstein's own phrase, "a higher order of magnitude", taken literally. A stellar population's own mass-to-light ratio is of order one, so a ratio below 10 leaves room for non-luminous mass comparable to the luminous mass, and no more.

## Inputs

One Gaia DR3 cone about the starting position RA 250.42°, Dec +36.46° (ICRS), radius 0.6°, read from the local bulk copy (`sdp einstein1921 run`) or from a Parquet file with the native `gaia_source` columns (`--parquet`). The starting position only places the cone. The centre itself is fitted.

## Selection (predeclared)

- `ruwe < 1.4`, `astrometric_params_solved` in {31, 95}, `duplicated_source` false.
- `phot_g_mean_mag < 19`.
- Finite parallax, proper motions and their errors.
- Stars inside the inner exclusion radius `r_in` are dropped from the fit, because Gaia's astrometry is incomplete and degraded in the crowded core.

## The model

The centre is found first: the mean position of stars within 0.1° of the current centre whose proper motion lies within 1.5 mas/yr of their median, iterated five times from the starting position. A core hole that is symmetric does not bias this centroid.

Stars in the annulus `r_in ≤ R ≤ R_max` about that centre are a mixture of cluster and field, fitted by maximum likelihood:

- **Positions.** The cluster follows the Plummer surface density of eq. (3), `Σ(R) ∝ (1 + R²/a²)^(−2)`, and the field is uniform. Both are normalised over the annulus. `a` and the cluster fraction are free.
- **Proper motions.** Cluster stars scatter about the cluster's mean proper motion with the isotropic Plummer projected dispersion `σ²(R) = σ₀²(1 + R²/a²)^(−1/2)`, convolved with each star's own error ellipse (errors and `pmra_pmdec_corr`, scaled by the error-scale factor). Field stars follow a free two-dimensional Gaussian convolved with the same errors.
- **Parallax.** Cluster stars share one parallax, convolved with each star's error. The field follows a free Gaussian.

For the isotropic Plummer sphere, `σ₀² = (3π/64)·GM/a`, the mass-weighted mean square speed is `⟨v²⟩ = (3π/32)·GM/a`, and the virial theorem gives eq. (5) with `A = 3π/32`. The test suite checks all three against Jeans-equation integrals. So the fit gives the dynamical mass directly:

`M = 64·a·σ₀² / (3π·G)`, with `σ₀` in km/s = 4.74047 · σ₀ (mas/yr) / ϖ (mas) and `a` in pc from its angle and the distance.

The distance is `1/ϖ`, with `ϖ` the fitted cluster parallax less the parallax zero-point of the scenario.

The light is summed from the stars in the annulus, each weighted by its membership probability, as `L_G = Σ p_i·10^(−0.4(G_i − μ − A_G − M_G,☉))` with `M_G,☉ = 4.67`. It is divided by the Plummer fraction of the cluster's stars inside the annulus, `R_max²/(R_max² + a²) − r_in²/(r_in² + a²)`, which assumes the light follows the star counts. The dynamical mass-to-light ratio is `Υ = M/L_G`.

Bias, declared in advance: stars fainter than G = 19 (the unevolved main sequence at M13's distance) are missing from `L_G`. That makes `L_G` too small and `Υ` too large, so it works against UC03-H1. A result in favour of H1 is conservative. A result in favour of H0 would first need that light restored.

## Einstein's inputs, measured

The report sets each of Einstein's guesses next to its Gaia counterpart:

| Einstein (1921) | Gaia counterpart |
|---|---|
| `v = 26 km/s` | `√⟨v²⟩ = √((3π/32)·GM/a)` |
| `N = 2000` stars of `m = 15` M☉, then ×100 | the dynamical mass `M`, and the number of members brighter than G = 19 (Σ p_i, corrected to the full annulus) |
| `2a ≥ 100` light-years | `2a` from the fit, in light-years |

## Scenario axes (predeclared)

1. Inner exclusion radius `r_in`: 1.5 and 3 arcmin.
2. Outer radius `R_max`: 0.4 and 0.6 deg.
3. Proper-motion and parallax error scale: 1.0 and 1.1.
4. Parallax zero-point subtracted: 0, −0.017 and −0.034 mas.
5. Extinction `A_G`: 0.0 and 0.1 mag.

That is 48 scenarios. The **baseline** is `r_in` = 3 arcmin, `R_max` = 0.6°, error scale 1.0, zero-point −0.017 mas, `A_G` = 0.0. The baseline gets a bootstrap over stars (100 resamples, seed 1921) for the 16th and 84th percentiles of every reported quantity.

## Decision rule

- **Supports UC03-H1** when the baseline's 84th percentile of `Υ` and every scenario's `Υ` are below 10.
- **Supports UC03-H0** when the baseline's 16th percentile of `Υ` and every scenario's `Υ` are 10 or more.
- **Inconclusive** otherwise. The report names the scenarios on each side.

The fit must converge in every scenario and in every one of the baseline's bootstrap refits, and the fitted cluster fraction must be positive. A refit that fails is counted and its reason recorded. A band from fewer refits than requested decides nothing, and the verdict is then inconclusive. A scenario that fails either is reported by name, and the verdict is inconclusive.

## Validation before use

The module is run on synthetic clusters drawn from the isotropic Plummer distribution function with a known mass, scale and distance. Each has Gaia-like errors, a uniform field population, and a core hole. The tests require the mass to be recovered within 15% and the verdict to follow the planted mass-to-light ratio on both sides of 10.

## What this is not

- It is not a remeasurement of anything Einstein measured: he measured nothing in the cluster. It is his calculation with measured inputs.
- It does not test the cosmological constant. The 1921 paper never mentions λ (see the next section).
- Velocities are on the plane of the sky only. The bulk copy carries no radial velocities, so isotropy is assumed. Anisotropy and mass segregation are named as limitations, not modelled.

## Context for the history

O'Raifeartaigh et al. (2017, arXiv 1701.07261, §4(v)) and O'Raifeartaigh et al. (arXiv 1711.06890, §3) describe this paper as Einstein's attempt to estimate the cosmological constant from stellar velocities, following "Geometry and Experience" (1921a). The 1921 text does not mention λ. Its first paragraph says only that general relativity allows large deviations from Newton's law in a spatially finite world when a system's mean density is not much greater than the world's. This protocol does not settle that question of intent. The report states the factual point and leaves the interpretation to historians.

## Implementation

| Piece | Where |
|---|---|
| Part A, the Plummer relations, the mixture fit, light, scenarios, verdict | `sdp_relativity.einstein1921` |
| Predeclared choices | `sdp_relativity/einstein1921.json` |
| Synthetic Plummer clusters | `sdp_relativity.einstein1921.synthetic_cluster` |
| Command | `sdp einstein1921 arithmetic`, `sdp einstein1921 run` |
| Requirements | `requirements/relativity/einstein-1921.ears` (REL-011 to REL-015) |

This first version runs as a command that writes a report (JSON and Markdown) with the SHA-256 of the predeclared choices. Freezing it in the ledger through the kernel's predeclaration and experiment ports, as UC-01 and UC-02 do, is follow-up work.
