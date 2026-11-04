# Century trends on the Harvard plates

Has a star brightened or faded over the last hundred years? The Harvard College Observatory
photographed the whole sky from the 1880s to 1989, and DASCH (Digital Access to a Sky Century @
Harvard, data release 7) has measured every star on those plates. Gaia measured the same stars again
from 2014 to 2017. Between them they cover more than a century, longer than any other survey covers
the same stars.

This page records the first experiment (2026-11-04): one field, about 1,000 stars, a trend fitted to
each, and the measured rate at which constant stars pass for trending ones. The test it led to is
`century-trend` (requirement INT-027, `sdp_investigate/century.py`, profile `plate-trends`).

## The field

M67, an old open cluster at RA 132.846, Dec +11.814, was the field. Its stars are mostly old and
quiet, it sits well away from the Galactic plane, and the Harvard plates cover it well.

- DASCH catalogue (APASS reference) within 1.1 degrees, tiled with seven 30-arcmin cones. DASCH's
  `querycat` times out on a single 45-arcmin cone.
- Stars with B < 15 that appear on at least 100 plates: **1,100 stars, 263,000 clean measurements**
  (daschlab's standard AFLAGS cleaning) on **3,668 exposures**, from 1885 to 1989.
- Gaia DR3 within the same circle down to G = 17, with synthetic Johnson B from
  `gaiadr3.synthetic_photometry_gspc`: 1,090 of the stars matched within 3 arcsec.
- **520 stars** have at least 100 measurements with 20 or more on each side of the 1953-1969 gap
  in the plates (the "Menzel gap").

## What a naive trend finds

A straight line through each star's plate magnitudes, weighted by DASCH's quoted errors, flags
**30 to 36%** of the stars Gaia calls constant as trending at the 1% level, and about 27% at 0.1%.
Correcting each plate by the median of all the stars on it barely helps. The constant stars' slopes
spread by 0.08 to 0.14 magnitudes a century, two to three times their quoted errors.

This is the trap behind the long argument over Boyajian's star (KIC 8462852), whose 0.16 mag/century
dimming on the same plates (Schaefer 2016) other authors put down to the plates themselves: many
comparison stars showed trends too (Hippke et al. 2016; Lund et al. 2016). A century-trend
claim is only as good as the false-positive rate measured on the field's own stars.

## What makes the trends trustworthy

Each step below was measured on the 295 Gaia-nonvariable stars without a bright neighbour.

| Step | False-positive rate at nominal 1% | at nominal 0.1% |
|---|---|---|
| Straight line, quoted errors, each plate's median offset removed | 33.2% | 25.8% |
| + each plate's offset fitted with a magnitude and a colour term | 30.5% | 18.3% |
| each plate corrected instead by the 15 nearest clean stars (position, magnitude, colour) | 28.5% | 19.3% |
| + red-noise errors from 5-year bin means | 11.5% | 6.1% |
| + slope compared with its peers' (colour, magnitude) and their spread added (cross-validated) | 3.9% | 1.9% |
| + reference stars must have no Gaia neighbours within 60" adding over 10% of their light | 3.7% | 1.1% |

The test as written (`century-trend`, all defaults) reproduces this. On the same stars it scored 521,
with 280 reference stars and sigma_sys 0.031 mag/century, and its null (calibrate on half, score the
other half) ran at **4.4% at nominal 1% and 1.5% at nominal 0.1%**. `tools/century_scan.py`, whose
default field tiles the same 1.1 degrees slightly differently (1,110 stars), gives 525 scored, 284
reference stars, sigma_sys 0.034 and a null rate of **1.8% at nominal 0.1%**. The tail is still
heavier than a normal one, so a field of a few hundred clean stars should expect three or four false
candidates at alpha 0.001.

The smallest trend that stands out is about **0.25 to 0.3 magnitudes a century** for a typical
B = 12 star. A Boyajian-sized 0.16 mag/century would not.

## Blending is the main trap

The 1970s and 1980s plates come mostly from the Damon patrol cameras (series `dnb`, `dsb`), which
are coarse enough to merge neighbours up to about a minute of arc away. A star with a neighbour there
looks brighter after the gap. Every one of the five first-pass candidates (before the blend cut) was
such a case:

- flat to within a few hundredths of a magnitude from the 1890s to 1952, then 0.17 to 0.25 mag
  brighter on the post-gap plates, mainly on `dnb`;
- 0.22 to 0.37 mag brighter after the gap than the B magnitude Gaia measured in 2014-2017, five to
  eight times the 0.046 mag spread the constant stars show;
- four of the five have Gaia neighbours within 60" adding 4 to 54% of their light.

So the test computes each star's blend fraction from the catalogue stars around it. Blended stars are
still scored and say how blended they are, but they never calibrate the field.

## The Gaia anchor

Gaia's synthetic Johnson B is a modern magnitude in nearly the plates' band, measured independently
of the plates and of their APASS calibration. A real trend should reach 2016 at Gaia's magnitude; a
jump on the post-gap plates tilts the line to a 2016 magnitude Gaia does not see. The test reports the
line's 2016 magnitude minus the anchor, in units of the reference stars' spread (`anchor_z`).

On M67 (the scan tool's run), 42 stars are significant at alpha 0.001. 40 of them are blends, and
Gaia contradicts every blend's trend (|anchor_z| of 3 or more). Of the two unblended ones, one is
contradicted by Gaia too; one agrees.

## Result

No discovery in this field. One unblended star is significant at alpha 0.001 and agrees with Gaia,
which is no more than chance alone gives (the null passes 1.8% of clean stars, about 4 of 240):

| Gaia DR3 | G | BP-RP | Trend (mag/century) | Peer-relative | z | Gaia anchor z | Notes |
|---|---|---|---|---|---|---|---|
| 608052027767556224 | 9.97 | 0.58 | +0.20 (fading) | +0.16 | 3.5 | 2.3 | RUWE 6.0, `non_single_star` 3 (astrometric and spectroscopic binary), 77 mas/yr, 210 pc. Fades 0.15 mag/century on the pre-1953 plates alone. APASS's B for it is a magnitude off (11.41 against Gaia's 10.47). |

The unblended star Gaia contradicts is Gaia DR3 607997288408680064 (G 9.61): flat until 1952, then
0.19 mag brighter on the post-gap plates and 0.27 mag brighter at 2016 than Gaia measured, the same
jump the blends show though no Gaia neighbour within 60" adds more than 4% of its light. In the first
exploratory run a second star, Gaia DR3 608049175909265536 (G 11.62, fading 0.13 mag/century, z 3.3),
also passed; in the scan tool's field it falls just short.

The binary is worth a closer look, because a star with a Gaia orbit could hide a slow change, but on
these numbers it is a target for the refine step, not a claim.

## Reproduce it

    cd python
    uv run python tools/century_scan.py                       # M67, writes century-scan/m67/
    uv run python tools/century_scan.py --ra 130.1 --dec 19.67 --label praesepe

The tool fetches DASCH and Gaia directly (about half an hour for 1,000 stars, then a minute from its
cache) and writes `results.csv` and `summary.json`. Inside an investigation, the `plate-trends`
profile runs the same test; it has no Gaia anchor yet, because SDP's Gaia catalogue rows do not carry
synthetic photometry.

## What is next

1. Gaia synthetic Johnson B in the catalogue rows, so the investigation reports the anchor too.
2. More fields, so the false-positive rate is measured on more than one (Praesepe, the Pleiades and
   NGC 752 are bright, northern and well covered).
3. Plate-level colour terms. The peers absorb a colour-dependent drift on average, but not one that
   changes from one plate series to the next.
4. For each survivor: its plate cutouts on the post-gap plates, a fit without the patrol series, and
   Gaia's epoch photometry for its 2014-2017 behaviour.
