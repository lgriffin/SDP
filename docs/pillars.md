# The Pillars of Creation: Gaia, JWST and Hubble in 2 GB

The Pillars of Creation in the Eagle Nebula (M16) have been imaged by Hubble since 1995 and by JWST in
2022. This investigation asks, star by star, whether each Gaia star appears on each of those images
where its motion puts it, and it does so within a 2 GB download that you approve first. It is
exploratory: a flagged star is a lead, never a claim.

This page covers the first part, the plan. The image tests, Hubble product validation and the screens in
the app come in later pull requests (see [What is not here yet](#what-is-not-here-yet)).

## The field: confirm it first

SDP does not look the Pillars up by name. A name resolver would pick a centre for you, and the centre
decides which images and which stars are in. Give the region yourself, after checking it:

| Region | What it covers | Where the centre comes from |
| --- | --- | --- |
| `icrs;circle(274.7104,-13.8289,0.025)` (proposed) | The three pillars, 90 arcsec around them; inside the JWST NIRCam mosaic, about 107 arcsec from its centre | MAST's pointing for HST programme 13926 (WFC3/UVIS, 2014), the classic Hubble framing |
| `icrs;circle(274.7299,-13.8517,0.035)` | The whole 2022 JWST field, with the pillars off-centre | MAST's pointing for JWST programme 2739 |

Both centres were read from MAST on 2026-10-31.

## What MAST holds there

Checked live on 2026-10-31 through MAST's API: 411 level-3 Hubble and JWST images lie within
3 arcminutes of the pillars.

| Images | Dates | Whole file | A 180-arcsec cutout reads, and keeps |
| --- | --- | --- | --- |
| JWST NIRCam F090W, F187N, F200W (14344 x 8589 pixels) | 2022-08-14 | 5.4 GB each | 282 MB, keeps 113 MB |
| JWST NIRCam F335M, F444W, F444W-F470N (7065 x 4177) | 2022-08-14 | 945 MB each | 67 MB, keeps 27 MB |
| JWST MIRI F770W, F1130W, F1500W (2305 x 2072) | 2022-08-30 | 153 MB each | 12 MB, keeps 8 MB |
| Hubble WFC3/UVIS F502N, F657N, F673N, per visit (6419 x 6555) | 2014-09-02 | 505 MB each | 58 to 117 MB, keeps 35 to 83 MB |
| Hubble WFC3/IR F110W, F160W, per visit (1471 x 1454) | 2014-09-02 | 26 MB each | 4 to 8 MB |

Earlier and later Hubble epochs are there too: WFPC2 in 1995 (programme 5773, the original Pillars
image), NICMOS in 1997 and 1998, ACS in 2006, WFC3/IR in 2018 (programme 15238) and WFC3/UVIS in 2021.
For every visit's mosaic, MAST also lists one mosaic per exposure, which is most of the 411.

The cutout figures were measured by the plan's own header probe at the proposed centre on 2026-10-31.
A cutout reads every row it covers at the image's full width, so it downloads about 2.5 times what it
keeps from the NIRCam mosaics. The plan counts what it reads: all nine JWST filters come to about
1.08 GB, which leaves about 0.7 GB of the 1.8 GB for Hubble.

## Draft the plan on the PC

```bat
powershell -ExecutionPolicy Bypass -File harness\run-pillars-plan.ps1
```

The script updates the checkout, registers the archive descriptors, and drafts the plan for the proposed
region (`-Region` for another). Drafting lists MAST's mosaics over the field and reads each candidate's
FITS headers, a few kilobytes each, to cost its cutout. No pixels are read and nothing is approved.

It prints the mosaics the plan keeps with what each will read, the ones it leaves out grouped by reason,
and the total against the 2 GB cap. The whole plan is saved beside the log, in
`<SDP data>\reports\pillars\`.

The same from the SDP Command Prompt:

```bat
sdp integration register
sdp plan draft --region "icrs;circle(274.7104,-13.8289,0.025)" --profile profiles\pillars-of-creation.yaml --label "Pillars of Creation"
sdp plan show <plan id>
```

## How the plan chooses

The [`pillars-of-creation`](../profiles/pillars-of-creation.yaml) profile sets the rules
([what each setting means](../profiles/README.md#archive-settings-pillars-of-creation)):

1. Group MAST's mosaics by mission, instrument and filter. Leave out single exposures, and images that
   name no filter (detection images).
2. Within a group, try the newest combined mosaic first. Read its headers: if the cutout would miss the
   image, or the image holds less than half of the cutout's square, try the next one.
3. Take the groups JWST first, then Hubble, newest first, and keep each group's mosaic while the total
   stays under 1.8 GB (2 GB less the 10% reserve). A mosaic that does not fit gives way to a smaller one in
   its group, if there is one.
4. Record every mosaic left out, and why, in the plan (`constraints.excluded`), with every header probe
   (`constraints.selection`).

A plan above the deployment's approval threshold (500 MB by default) needs a signed approval:
`sdp plan approve <plan id> --actor leigh`. Execution then cuts only the mosaics the plan names; any
other is refused and the refusal recorded.

## What is not here yet

- **A running byte meter.** The cap is checked when the plan is drafted. Execution reads what the plan
  costed, but does not yet stop itself at 2 GB if the archive serves more.
- **Hubble product validation.** JWST products are validated against their envelope; Hubble's go through
  the ordinary product path, so they stay unadmitted and the run stays exploratory.
- **One PSF width for every instrument.** `fwhm_px: 2.0` suits NIRCam short-wave and WFC3/UVIS; MIRI and
  WFC3/IR are coarser, so expect fewer detections there.
- **Image-to-image tests.** Morphology and the change between Hubble and JWST epochs come with their own
  pull requests.
