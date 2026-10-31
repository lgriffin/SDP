# Test profiles

A **test profile** is the recipe for exploring a patch of sky. It says which stars to look at, which archives to ask, which tests to run, and how strong the evidence must be before a star or group becomes a candidate worth your time. You pick one on the Start page.

SDP records a fingerprint (a hash) of the profile before anything runs. A run therefore cannot be tuned after the fact to make a result look better. If you change a profile, raise its `version`, because it is now a different recipe.

These ten profiles ship with SDP. You do not need to edit them to get started. The Start page picks first-look for you, because it is first in alphabetical order.

| Profile | Use it when | Archives | Typical run |
|---|---|---|---|
| [first-look](first-look.yaml) | You are new to a field, or new to SDP | Gaia only | Seconds to a few minutes |
| [gaia-light-curves](gaia-light-curves.yaml) | You want to know which stars changed brightness while Gaia watched | Gaia and Gaia's DataLink light curves | About two minutes for a small field: each star is looked up once |
| [wise-light-curves](wise-light-curves.yaml) | You want to know which stars changed infrared brightness over ten years | Gaia and IRSA's WISE/NEOWISE light curves | Several minutes for a small field: a NEOWISE light curve can take a minute to cut out |
| [moving-group-search](moving-group-search.yaml) | You are hunting for a star cluster or moving group | Gaia and 2MASS | Up to about ten minutes |
| [survey-contradictions](survey-contradictions.yaml) | You want the stars whose other surveys disagree with Gaia: a position or a brightness that does not fit | Gaia, 2MASS and Pan-STARRS | A few minutes: two catalogue pulls for the whole field |
| [star-history](star-history.yaml) | You want a century of each bright star's past | Gaia and DASCH plates | Minutes. Keep the field small. |
| [image-changes](image-changes.yaml) | You want to know which stars changed between Hubble and Webb images of a field taken years or decades apart | Gaia and MAST (HST and JWST) | 10 to 30 minutes, mostly waiting for the archive. Needs the same filter at two dates. |
| [jwst-image-check](jwst-image-check.yaml) | You want to check Gaia's stars against a James Webb Space Telescope image | Gaia and MAST (JWST) | 10 to 20 minutes, mostly waiting for the archive. Try `sdp demo jwst`. |
| [my-frames](my-frames.yaml) | You want to check Gaia's stars against your own solved stacks | Gaia and your own frames (`local:astro`) | A minute or two a stack. Add, index and solve your frames first (`sdp local`). |
| [my-frames-variability](my-frames-variability.yaml) | You want to know which stars changed brightness over the nights you imaged a target | Gaia and the light curves `sdp local lightcurve` measured from your subs | Seconds, once the light curves are built. Solve the subs and run `sdp local lightcurve` first. |

All of them only *suggest* candidates. Every result is labelled EXPLORATORY. A candidate becomes a claim only after a separate, predeclared confirmatory experiment, which you promote in the SDP Command Prompt.

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
  - `clusterer` (optional) picks which HDBSCAN does the searching. Left out, it is `sklearn-hdbscan`, the reference UC-01 predeclared. `clusterer: fast-hdbscan` is about 60 times faster on a large field (0.015 s against 0.98 s per search on 13,928 stars), which turns a 50-minute Test stage into about a minute. It agrees with the reference on real groups, but on about 1 in 7 shuffled fields it picks a different best clump, so p-values can move slightly. Use it for big exploratory sweeps and keep the reference for results you will report. Every group records which one found it (`algorithm`).
  - The **grid** reruns the search with UC-01's six predeclared settings (`min_cluster_size` 10, 20 or 40, each with `min_samples` 5 or 10). A real group survives all of them, while a coincidence usually appears under only one. This *stability* feeds the score.
  - `inner_trials: 199` sets how many shuffled copies give the group its own p-value. With n shuffles the smallest possible p-value is 1/(n + 1), so 199 shuffles let a strong group reach 0.005. A p-value at or below `alpha` counts as significant (`p <= alpha`, which keeps the test's false-alarm rate at `alpha`; a strict `<` would halve it on this coarse grid), so with `alpha: 0.01` a group qualifies when at most one of its 199 shuffles matches or beats it. With 20 shuffles no group could ever get below 1/21 ≈ 0.05, and none would ever become a candidate. `magnitude_bins: 4` means motions are shuffled only among stars of similar brightness, because bright and faint stars have different typical errors.
- **photometric-corroboration: "Do 2MASS infrared colours agree with Gaia's colour?"** 2MASS measured the whole sky in the infrared. A star's infrared colour (J − K) should follow from its optical colour (BP − RP). A mismatch can mean dust, a companion, or a mix-up between two neighbouring stars. Stars are paired across catalogues within 1 arcsecond, a little over 1/3600 of a degree, which comfortably finds the same star.
- **cross-survey-contradiction: "Do the other surveys measure this star the way Gaia does?"** (survey-contradictions). Matching says 2MASS, Pan-STARRS and Gaia saw the same star; this test asks whether their measurements of it agree. It makes one *comparison* per quantity two surveys share: the star's position in 2MASS (about 1999) and Pan-STARRS (about 2012), against Gaia's position moved back along its proper motion (and, for 2MASS, which measured each star on one night, shifted by the star's parallax on that night), and its brightness in every band (2MASS J, H, Ks; Pan-STARRS g, r, i), against what Gaia's G and BP−RP predict. The prediction is a gentle curve (`degree: 2`) fitted to the field itself, so no published conversion formula is typed in and the field's dust and zero point are taken into account.
  - **The uncertainty budget.** Each comparison is judged against the errors the surveys report (Gaia's motion error grows with the years between the two measurements) plus whatever extra scatter the field itself shows. That extra scatter is worked out separately for bright, middle and faint stars (`magnitude_bins: 3`), because faint stars scatter more, after the field's average offset (2MASS's positions sit a few tens of milliarcseconds from Gaia's frame) is taken off. Every term is written into the result, so you can see why a star was or was not flagged.
  - **Set aside, never counted.** A comparison is set aside, with the reason, when the survey's resolution cannot separate the star from a neighbour (2MASS 2.5 arcsec, Pan-STARRS 1.5 arcsec) no more than `blend_delta_mag: 2.5` fainter in G, judged where both stars were on the survey's date, when 2MASS flags the band as poor (anything but A or B in `ph_qual`, or no grade at all), when Pan-STARRS saw the star fewer than three times, or when fewer than `min_per_bin: 15` stars can set the budget. A star whose every comparison was set aside still appears, with no p-value.
  - **The score.** Each comparison gives a z (a position's two-dimensional distance is turned into the z with the same p-value). The star's statistic is its worst z, and its p-value allows for how many comparisons it had, so a star with eight bands is not flagged more easily than one with two. Because the budget is estimated from a few dozen stars, the p-values use Student's t rather than the normal curve, which keeps the null-field false-alarm rate at about `alpha`.
  - **Reading a flag.** `pattern` names what kind of disagreement it is (`flag_z: 3.0` sets which comparisons count as disagreeing): *position* (a wrong motion, a hidden companion, or a different star matched), *one-band* (an infrared excess, or variability caught in one band), *all-bands-one-way* (the star changed brightness between the surveys, or a wrong match), *colour*, or *position-and-brightness* (usually a different star). It is a hint for you, not a verdict. Variability is not modelled, so a variable star shows as a brightness contradiction.
  - 2MASS is matched within `match_radius_arcsec: 1.5` rather than 1, so a star 1.2 arcsec from where Gaia puts it is still compared rather than silently lost.

- **long-baseline-motion: "Has the star moved as Gaia predicts since about 1890?"** Gaia measures how fast a star crosses the sky. The test uses that motion to predict where the star was on each Harvard plate, then checks the plate positions against the predictions. Plate positions are good to roughly 1–2 arcseconds, so plates are matched within 3 arcseconds. DASCH's own star catalogue records positions from around 2000 to 2016, so SDP uses each star's Gaia motion to look for it along its path over those years. A fast-moving star is still found.
- **variability: "Has its brightness changed over a century?"** The test asks whether the plate brightness measurements scatter more than their own error bars allow for a constant star (a χ² test). It also reports the strongest repeating period it finds (a Lomb–Scargle search) for you to look at. It needs at least `min_points: 20` measurements: fewer give a weak constancy test and no meaningful period.

- **variability on Gaia's light curves** (gaia-light-curves). The same test, on the light curves Gaia itself measured between 2014 and 2017. SDP uses the G band, Gaia's main, broad filter, and drops the measurements Gaia's own processing rejected. Times are in Gaia's barycentric time scale (TCB), which differs from ordinary UTC by about a minute, far less than the days between measurements. It needs at least `min_points: 10` measurements; most Gaia light curves have 20 to 70. Gaia's error bars on each measurement are tiny (about a thousandth of a magnitude for a bright star) and leave out a small calibration floor, so a bright star with only slight scatter can still come out significant. Look at the reported period and the light curve before reading much into it.

- **variability on WISE light curves** (wise-light-curves). The same test again, on the infrared light curves of NASA's WISE satellite: about a day of exposures every six months from 2013 to 2024 (NEOWISE). The 2010 survey (AllWISE) can be fetched too, but the test reads one light curve a star, so the profile asks only for NEOWISE. SDP uses W1, WISE's 3.4-micron band, and drops exposures with a poor frame, near the South Atlantic Anomaly or the Moon, blended, or flagged as an artefact. NEOWISE's quoted error bars are too small: in the Pleiades every star, steady or not, scattered 1.3 to 1.8 times more than its errors allow. `error_scale: field` therefore scales every error bar by the square root of the field's median reduced χ², so a star is flagged only when it scatters more than its neighbours. That needs at least three light curves in the field; with fewer, the errors are used as quoted. WISE's beam is about 6 arcseconds wide, so two stars closer than that share a light curve. A fast-moving star is followed along its path, so its later exposures are not lost. `min_points: 20` keeps out light curves with only a visit or two.

- **exposure-counterpart: "Is what WISE saw at each visit the Gaia star?"** (wise-light-curves). Each NEOWISE exposure also says where it saw the source. The test moves every Gaia star near the object to the date of each exposure and lets both association methods decide what the exposure saw: the star alone, the star blended with a neighbour inside WISE's beam, another star, or nothing Gaia knows. The exposures that saw the star are averaged, and the star is flagged when that average sits away from where Gaia puts it, or when fewer than half the exposures saw it (`min_matched_fraction: 0.5`, the default). The spread of the exposures themselves sets how precise the average is, so NEOWISE's too-small error bars do not matter here. For a blend, the expected position allows for any split of the light between the two stars. Only stars the profile examines (G < 17 here) count as neighbours, so a fainter star inside the beam shows up as a small offset rather than as a blend.
  - `search_radius_arcsec: 6.1` is WISE's W1 beam: an exposure further than that from a star did not see it.
  - `systematic_arcsec: 0.05` is how far NEOWISE's positions sit from Gaia's on average, however many exposures there are. Four Pleiades stars sat 0.02 to 0.045 arcseconds off (checked live on 2026-10-30), so without this every star came out significant.
  - `min_exposures: 20` keeps out light curves with only a visit or two.
  - AllWISE's light curves (2010) have no per-exposure positions: IRSA repeats the source's position on every exposure, so the test leaves them out.

- **image-counterpart: "Is each Gaia star on the JWST image where its motion puts it?"** Gaia measured each star around 2016. The test moves every star along its measured motion to the date of the JWST image, then looks for a source on the image at that spot. A star with no source there, or one further away than both telescopes' uncertainties allow, is flagged. Sources are found with photutils, and each pairing is made by two independent methods that must agree.
  - `fwhm_px: 2.0` is how wide a star looks on NIRCam's mosaic grid, in pixels.
  - `nsigma: 5.0` means a source must stand five times above the background noise to count.
  - `search_radius_arcsec: 0.5` is how far from the predicted spot a counterpart may sit. JWST and Gaia both pin stars to a few thousandths of an arcsecond, so half an arcsecond is generous.
  - `exclude_percentile: 25` lets the background be measured in a crowded cluster, where stars cover more than a tenth of the sky.
  - `align_wcs: true` first slides the whole image onto Gaia. The archive's own coordinates for these M92 mosaics sit about 2 arcseconds off Gaia, which would leave every star "missing". One shift moves the frame, not a star, so a star that really moved still stands out. The fit is recorded with each result.
  - `anchor_sigma_arcsec` and `min_sigma_arcsec: 0.01` are the smallest position uncertainties SDP will assume for Gaia and for JWST: about 10 milliarcseconds, the agreement an aligned mosaic reaches with Gaia. Smaller values would flag ordinary stars.

### image-changes: the same field at different dates

The epoch-change test measures every Gaia star on every HST and JWST image of the field and asks whether
its brightness changed by more than the images' errors allow. Three things differ between cameras and
dates, and the test deals with each before it compares two images:

- **Alignment (`align_wcs: true`).** An archived image's coordinates can be an arcsecond off. Each image is
  first shifted onto Gaia's stars at that image's date, one shift for the whole frame, so a single odd star
  cannot move it. An image that cannot be aligned is left out and listed, because a misplaced aperture
  would look like a star dimming. `align_separation_arcsec: 0.5` sets aside stars closer than that while
  matching, whatever each camera's pixel size.
- **Resolution (`aperture_fwhm: 1.0`).** Every image of one filter is measured in the same circle on the
  sky, one FWHM of the blurriest image in radius. A star's light spreads more on a blurry image, but the
  circle catches the same share of every star on one image, so the calibration takes care of it. Two stars
  closer than two circles on any image are measured together as one blend, reported under the brighter.
- **Calibration (`calibrator_snr: 20`, `min_calibrators: 5`).** The same star gives a different count on
  each camera. Each image is scaled to the deepest image of its filter using the field's own clean stars,
  with a median that ignores the odd variable among them, and the scale's own error is added to every
  comparison. An image with fewer than 5 such stars is left out.
- **`error_scale: field`.** In a crowded field the errors photometry quotes are too small. The test
  scales them by how much the field's stars scatter, allowing for how few images each star has, and never
  shrinks them.
- **Only one filter at a time.** F656N in 1995 and F656N in 2022 are compared; F656N against F200W is
  not, because a red star and a blue one differ between filters without changing at all. You may join two
  filters under one name (`bands: {F656N: halpha, F657N: halpha}`), and must then give
  `band_systematic_mag`, the brightness difference two nearly-alike filters can show.
- **`phot_g_mean_mag` between 16 and 20.5.** Brighter stars saturate HST and JWST; fainter ones have
  proper motions too uncertain to place them on an image taken decades from Gaia's 2016.

Each flagged star gets a verdict: **changed**, **appeared** (not seen on an earlier image, seen on a later
one), or **vanished** (the reverse), with every image's measurement and a 5-sigma upper limit where it
was not seen.

### Archive settings (jwst-image-check)

- **`cutout_arcsec: 40`.** JWST's mosaics are often over a gigabyte. SDP reads only the 40-arcsecond square around your field, about 1,300 by 1,300 pixels (7 MB), straight out of the archive's file. The pixels are not changed.
- **`min_calibration_level: 3`.** Level 3 means the archive has combined all the exposures into one clean mosaic. Level 2 would add every single exposure, ten times the downloads for the same stars.
- **`match_radius_arcsec: 180`.** A NIRCam mosaic is about 5 arcminutes across, so its centre can sit a few arcminutes from your field and still cover it.
- **`phot_g_mean_mag > 17`.** JWST is so sensitive that stars brighter than this saturate its detectors, which leaves no clean centre to compare with Gaia.

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
- Tests must be ones SDP has installed: `parallax-consistency`, `cmd-coherence`, `astrometric-coherence`, `photometric-corroboration`, `long-baseline-motion`, `variability`, `image-counterpart` and `exposure-counterpart`.
- A test that needs plate data must say so under `needs_products`.

`python/tests/investigate/test_default_profiles.py` checks all of this for the profiles in this folder.

### my-frames: your own frames

The same image-counterpart test as jwst-image-check, asked of your own telescope's frames. SDP reads the
source you added with `sdp local add astro ...`, and only its frames that `sdp local solve` solved and that
carry a date (`DATE-OBS`). Nothing in your folder is written. The values assume a Vespera Pro (1.65
arcseconds a pixel); change them, and the `version`, for another telescope.

- **`missions: [stack]`.** For a local source these are frame kinds. Stacks are deep and few, so a run
  reads a handful of frames. `[light]` reads every solved sub instead: hundreds of frames for one target.
- **`cutout_arcsec: 1800`.** SDP cuts a 30-arcminute square around your field out of each frame, so a region
  of up to 15 arcminutes radius fits, such as M31's core, `icrs;circle(10.6847,41.2687,0.2)`.
- **`phot_g_mean_mag` between 9 and 14.** Fainter than 14 a stack may not show the star clearly, and
  "missing" would only mean "too faint". Brighter than 9 the star saturates and has no clean centre.
- **`fwhm_px: 3`, `search_radius_arcsec: 4`.** A star on a stack is about 5 arcseconds wide, 3 pixels; a
  counterpart may sit up to about two and a half pixels from where Gaia puts it.
- **`align_wcs: false`.** SDP solved every frame against Gaia, so there is nothing left to align.
- **`min_sigma_arcsec: 0.5`.** The smallest position uncertainty SDP assumes for a star on your frame. Its
  solution fits Gaia to about an arcsecond, so a smaller value would flag ordinary stars.

### my-frames-variability: light curves from your own subs

The variability test, as wise-light-curves uses it, on the light curves `sdp local lightcurve` measured
from your solved subs. Each frame is calibrated against the field's steadiest stars, so the curves are on
Gaia's G scale with haze and airmass taken out.

- **`missions: [light]`, `product_types: [lightcurve]`.** The curves come from the raw subs, one per star.
- **`match_radius_arcsec: 2`.** Each curve was measured at the Gaia star's own position, so it sits on the star.
- **`phot_g_mean_mag` between 9 and 15.** What `sdp local lightcurve` measures by default: brighter stars
  saturate a 10-second sub, fainter ones are mostly noise.
- **`error_scale: field`.** The photon-noise error bars leave out scintillation, flat-field and tracking
  errors, so they are too small. The test scales them by the field's median reduced χ², so a star is
  flagged only when it scatters more than its neighbours.
- **`min_points: 20`.** About three minutes of 10-second subs; fewer is too short a stretch to say much.
