# Protocol UC-03b: corrections to the UC-03 Einstein 1921 rerun (exploratory)

- Protocol id: `uc03b-einstein-1921-corrections`, version 1.0.0. Its parent is [UC-03](uc03-einstein-1921.md).
- Module: `sdp_relativity.einstein1921_corrections`.
- Choices: [`sdp_relativity/einstein1921_corrections.json`](../../python/packages/sdp-relativity/src/sdp_relativity/einstein1921_corrections.json). Every run records that file's SHA-256.
- **Status: exploratory.** This protocol was written on 2026-10-31, after the first UC-03 run on Gaia DR3 and before any of the corrections below was run on data. It cannot change UC-03's verdict, which stays **inconclusive**. Its job is to find out why that run was inconclusive, using corrections that UC-03 itself named in advance as needed before any H0 reading.

## What the first run showed

The UC-03 run read the committed choices unchanged, and all 100 bootstrap refits converged. Its baseline M/L_G was 11.11, with a 16–84% band of 10.37 to 12.01. Of the 48 scenarios, 11 came out below 10 and 37 at or above. Two scenario axes drove that split:

- **Inner cut.** With `r_in` = 1.5′ the mean M/L_G was 16.6 and the fitted `a` was about 3.4′. With `r_in` = 3′ the mean was 10.3 and `a` was about 2.6′. All 11 scenarios below 10 use `r_in` = 3′. The fit is sensitive to where the core is cut, which is what incompleteness in a crowded core would produce.
- **Distance.** The mean M/L_G was 15.4 at zero-point 0 and 11.6 at −0.034 mas. M scales as d³ and L as d², so M/L scales as d. The fitted parallax, 0.1045 mas, puts M13 at 8.2 kpc.

UC-03 had already declared a bias against H1: the light of stars fainter than G = 19 is missing from L_G.

## Sources (fetched 2026-10-31, quoted in the choices file)

| Quantity | Value | Source |
|---|---|---|
| Distance | 7.42 ± 0.08 kpc | Baumgardt globular cluster database v4 (Mar. 2023), NGC 6205. The database says its distances are mostly from Baumgardt & Vasiliev (2021), MNRAS, doi 10.1093/mnras/stab1474, arXiv 2105.09526. |
| Distance, alternative | 7.1 kpc | Harris (1996, AJ 112, 1487), December 2010 revision |
| Total V light | L_V = 4.84 × 10⁵ / 2.05 = 2.361 × 10⁵ L☉ at 7.42 kpc | Baumgardt database: mass 4.84 ± 0.06 × 10⁵ M☉, M/L_V 2.05 ± 0.08 |
| Comparison only | mass 4.84 × 10⁵ M☉; central σ 8.9 km/s (Baumgardt), 7.1 ± 0.4 km/s (Harris) | the same pages |

None of these values was typed from memory. Each was read on the page named in the choices file.

## The corrections

1. **Distance (C1).** The distance is fixed at the published value instead of 1/ϖ. Gaia's fitted parallax is still fitted and reported, but it no longer sets the scale. Scenarios use 7.42 kpc, 7.42 ± 0.08 kpc, and Harris's 7.1 kpc.
2. **Light (C2).** The light is the published total V light, scaled to each scenario's distance as `L_V(d) = 2.361 × 10⁵ · (d / 7.42 kpc)²`. Being a whole-cluster value, it includes the stars fainter than G = 19 and the crowded core that Gaia misses. That removes UC-03's declared bias, and it no longer assumes that light follows the counts of bright stars. The ratio reported is `Υ_V = M / L_V`, in V instead of G. A stellar population's own M/L is of order one in either band, so the threshold of 10 keeps its meaning. UC-03's L_G at the published distance is also reported, for comparison.
3. **Crowding (C3).** Three checks of whether the core cut and faint crowded stars drive `a` and σ₀:
   - inner cut `r_in` at 3′, 4.5′ and 6′;
   - magnitude limit `G` < 17, 18 and 19, since crowding hurts faint stars first;
   - proper-motion and parallax error scale at 1.0, 1.1 and 1.3, because Gaia's reported errors in crowded fields may be too small, and errors that are too small inflate the fitted dispersion.

The fit is unchanged: UC-03's mixture model, centre finder, and annulus out to `R_max` = 0.6°. It is the same code (`sdp_relativity.einstein1921.fit`). The mass follows from the fit at the fixed distance as `M = 64·a·σ₀²/(3πG)`.

That gives 3 × 3 × 3 = 27 fits. Each is converted at 4 distances, which makes 108 scenarios. The **corrected baseline** is `r_in` = 3′, `G` < 19, error scale 1.0, d = 7.42 kpc. It gets a bootstrap over stars, with 100 resamples and seed 1921.

## Reading the result (exploratory)

- **Consistent with UC03-H1 after corrections:** the corrected baseline's 84th percentile of Υ_V is below 10, and so is every scenario's.
- **Consistent with UC03-H0 after corrections:** the 16th percentile, and every scenario, is 10 or more.
- **Mixed** otherwise, and also if any fit or refit fails. The report names the scenarios on each side.

The crowding check is reported but decides nothing. It is a table of `a`, σ₀ and M against `r_in` and the magnitude limit at error scale 1.0, alongside the published comparison values. If the fitted `a` and σ₀ settle as the core cut widens and the limit brightens, crowding was driving the UC-03 split. If they keep moving, the Plummer law itself is the question. M13 is better described by a King model with concentration c = 1.53 (Harris), and a Plummer `a` fitted outside the core is a model choice, not a measurement.

## What this is not

- It is not a second confirmatory test. The corrections were chosen after seeing a result, and they are labelled that way wherever they are reported.
- The published light and distance come from models of their own: Baumgardt's N-body fits, and the distance averages of Baumgardt & Vasiliev. Using them makes the result depend on that work, which this protocol cites and does not check.

## Implementation

| Piece | Where |
|---|---|
| Corrections, scenarios, reading | `sdp_relativity.einstein1921_corrections` |
| Choices with their sources | `sdp_relativity/einstein1921_corrections.json` |
| Command | `sdp einstein1921 corrections` |
| Requirements | `requirements/relativity/einstein-1921.ears` (REL-017 to REL-019) |
