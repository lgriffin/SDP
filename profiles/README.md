# Test profiles

A **test profile** is the recipe for exploring a patch of sky. It says which stars to look at, which archives to ask, which tests to run, and how strong the evidence must be before a star or group becomes a candidate worth your time. You pick one on the Start page.

SDP records a fingerprint (a hash) of the profile before anything runs. A run therefore cannot be tuned after the fact to make a result look better. If you change a profile, raise its `version`, because it is now a different recipe.

These three profiles ship with SDP. You do not need to edit them to get started. The Start page picks first-look for you, because it is first in alphabetical order.

| Profile | Use it when | Archives | Typical run |
|---|---|---|---|
| [first-look](first-look.yaml) | You are new to a field, or new to SDP | Gaia only | Seconds to a few minutes |
| [moving-group-search](moving-group-search.yaml) | You are hunting for a star cluster or moving group | Gaia and 2MASS | Up to about ten minutes |
| [star-history](star-history.yaml) | You want a century of each bright star's past | Gaia and DASCH plates | Minutes. Keep the field small. |

All three only *suggest* candidates. Every result is labelled EXPLORATORY. A candidate becomes a claim only after a separate, predeclared confirmatory experiment, which you promote in the SDP Command Prompt.

## How a run works

1. **Identify.** SDP asks Gaia (the European Space Agency's star-mapping satellite, data release 3) for every star in your region that passes the profile's *quality cuts*. It then asks SIMBAD, the astronomers' "who's who" of stars, what each star is already known to be.
2. **Retrieve.** It gathers any extra evidence the profile names, such as 2MASS infrared brightness or the Harvard plate history.
3. **Test.** Each test asks one question of every star (or every group) and returns a **p-value**. A p-value is the chance of seeing a result at least this unusual if nothing interesting were going on. A small p-value means "chance alone rarely does this".
4. **Rank.** Each star or group gets a score. Those above the threshold become investigations on your home page.

Alongside the real field, SDP also runs the same tests on **null fields**. These are copies of your field with the clues shuffled so that any real signal is destroyed. The share of null-field stars that still get flagged is your honest false-alarm rate for *this* profile on *this* field. It shows on the result page.

## The settings, and why these values

### Quality cuts (all profiles)

- **`ruwe < 1.4`.** RUWE measures how well a single, well-behaved star fits Gaia's measurements. Values above about 1.4 usually mean a hidden companion star or a measurement problem, which makes the motion and distance unreliable. The cut-off comes from Lindegren et al. (2021). The SDP Gaia dialect and the UC-01 cluster study use the same value.
- **`phot_g_mean_mag < 18`** (first-look, moving-group-search). Magnitudes run backwards: bigger numbers are fainter stars. Fainter than about G = 18, Gaia's distances become too uncertain to compare stars meaningfully. The UC-01 study predeclared this same limit.
- **`phot_g_mean_mag < 15`** (star-history). The Harvard plates record only bright stars well. Each star also costs a request to the DASCH archive, so this cut keeps the work proportionate.
- **`row_cap`.** This is the most stars SDP will read from Gaia: 5,000 for a first look or a group search, and 300 for a plate history. The group search reruns its clustering thousands of times, so more stars would make it slow. If a region hits the cap, choose a smaller region.

### Tests

- **parallax-consistency: "Is this star's distance unusual for the field?"** Parallax is the tiny yearly wobble that tells us distance. The test compares each star's parallax with the middle of the field, allowing for both the star's own measurement error and the field's natural spread. The result is a z-score: how many "typical spreads" away the star is.
- **cmd-coherence: "Does it sit where the field's stars sit in colour and brightness?"** Plotting brightness against colour (a colour–magnitude diagram) puts most stars on a band called the main sequence. The test first corrects each star's brightness for its distance, using the parallax, so that near and far stars can be compared. It then fits a gentle curve through the field (`degree: 2`, a parabola) and measures how far each star sits from that curve, in units of the field's typical scatter. A straight line misses the band's bend, and higher-degree curves start chasing noise.
- **astrometric-coherence: "Are some stars moving together?"** Stars born together share a motion across the sky and a distance. The test looks for clumps in motion and distance with HDBSCAN, a clustering method that does not need to know in advance how many groups exist.
  - `min_cluster_size: 10` is the smallest group it will call a group. Real open clusters usually have tens to hundreds of members.
  - `min_samples: 5` controls how dense a clump must be.
  - The **grid** reruns the search with UC-01's six predeclared settings (`min_cluster_size` 10, 20 or 40, each with `min_samples` 5 or 10). A real group survives all of them, while a coincidence usually appears under only one. This *stability* feeds the score.
  - `inner_trials: 199` sets how many shuffled copies give the group its own p-value. With n shuffles the smallest possible p-value is 1/(n + 1), so 199 shuffles let a strong group reach 0.005, which is below `alpha`. With 20 shuffles no group could ever get below 1/21 ≈ 0.05, and none would ever become a candidate. `magnitude_bins: 4` means motions are shuffled only among stars of similar brightness, because bright and faint stars have different typical errors.
- **photometric-corroboration: "Do 2MASS infrared colours agree with Gaia's colour?"** 2MASS measured the whole sky in the infrared. A star's infrared colour (J − K) should follow from its optical colour (BP − RP). A mismatch can mean dust, a companion, or a mix-up between two neighbouring stars. Stars are paired across catalogues within 1 arcsecond, a little over 1/3600 of a degree, which comfortably finds the same star.
- **long-baseline-motion: "Has the star moved as Gaia predicts since about 1890?"** Gaia measures how fast a star crosses the sky. The test uses that motion to predict where the star was on each Harvard plate, then checks the plate positions against the predictions. Plate positions are good to roughly 1–2 arcseconds, so plates are matched within 3 arcseconds. DASCH's own star catalogue records positions from around 2000 to 2016, so SDP uses each star's Gaia motion to look for it along its path over those years. A fast-moving star is still found.
- **variability: "Has its brightness changed over a century?"** The test asks whether the plate brightness measurements scatter more than their own error bars allow for a constant star (a χ² test). It also reports the strongest repeating period it finds (a Lomb–Scargle search) for you to look at. It needs at least `min_points: 20` measurements: fewer give a weak constancy test and no meaningful period.

### Ranking

- **`alpha: 0.01`.** A test counts as significant when its p-value is below 0.01, a 1-in-100 chance. A field of 5,000 stars should still produce about 50 chance flags per test. That is why first-look asks for both of its tests, and why the null fields matter.
- **`policy: significance`** (first-look, star-history). The score is the share of a star's tests that came out significant, counting only the tests that could judge that star.
  - first-look uses `threshold: 1.0`: both its tests must be significant.
  - star-history uses `threshold: 0.5`: either plate test is enough on its own, because a star can move oddly without changing brightness, and the reverse. It runs only plate tests, so a star with no usable plates is never a candidate.
- **`policy: stellar` with `threshold: 0.5`** (moving-group-search). This is the UC-01 cluster study's ranker. A group scores on how strongly its members belong, how tight their colour–magnitude sequence is, and how stable it is across the grid. A group whose own p-value is not below `alpha` scores zero.

### Null fields

- **`trials`.** This is how many shuffled copies of the field run alongside: 10 for the quicker profiles, and 20 for the group search, as UC-01 predeclared. More trials give a steadier false-alarm rate but take longer.

## Making your own

1. Copy the profile closest to what you want, and give the copy a new `profile_id` and file name.
2. Change one thing at a time, and set `version: 1.0.0` on the new copy.
3. In the desktop app, put the file in `%LOCALAPPDATA%\SDP\profiles`. On a server, put it in the folder that `SDP_PROFILES_DIR` names. It appears on the Start page the next time you open it.

These rules keep a profile valid:
- Columns must be ones the archive declares.
- Tests must be ones SDP has installed: `parallax-consistency`, `cmd-coherence`, `astrometric-coherence`, `photometric-corroboration`, `long-baseline-motion`, `variability` and `image-counterpart`.
- A test that needs plate data must say so under `needs_products`.

`python/tests/investigate/test_default_profiles.py` checks all of this for the profiles in this folder.
