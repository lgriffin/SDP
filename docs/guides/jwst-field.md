# Journey: investigate a JWST field end to end

**Time:** about 30 minutes, mostly waiting for the archive · **Download:** about 40 MB ·
**Needs:** any [install](README.md#1-install-sdp), and an internet connection that reaches MAST

The James Webb Space Telescope (JWST) sees stars far sharper and fainter than Gaia. Gaia knows where each
of its stars was in 2016 and how fast it moves. This journey asks: **is every Gaia star on the JWST image
exactly where its motion puts it on the day JWST looked?** A star that is missing, or a little off, is
worth a closer look.

The field is the outskirts of the globular cluster **M92**, which JWST imaged with its NIRCam camera
(programme 1334) and where Gaia measures the stars well. You go from choosing the field to a saved
**evidence dossier** that anyone can re-check without your computer.

The step-by-step walkthrough, screen by screen, is **[docs/jwst-field.md](../jwst-field.md)**. Follow it;
this page adds what to expect and the quickest routes.

## The quickest route: one command

In the SDP Command Prompt (desktop app) or a terminal (developer tools: `uv run sdp …` from `python`):

```bat
sdp demo jwst
```

It prints its four steps as it goes:

1. registers the archives, if this database has never done it (MAST's description takes about 5 minutes);
2. plans the field: one Gaia cone and four NIRCam mosaic cutouts, each a 40-arcsecond square, about
   40 MB in all;
3. approves the plan (under 500 MB, so automatic, recorded under the name `demo`);
4. runs it: Gaia stars, then the JWST cutouts, then the image test.

Then it summarises the set and prints its id (`invset-…`). Running it again picks up the same plan where
it stopped, or shows the results if it already finished.

**Success** (a real run on 29 October 2026 took about 13 minutes):

- four NIRCam cutouts stored, about 41 MB;
- 53 Gaia stars tested on the images;
- 2 stars flagged, each EXPLORATORY;
- a null-field false-alarm rate of about 1%.

Then look at it and save the evidence:

```bat
sdp investigate list invset-…
sdp investigate dossier <investigation_id>
sdp investigate bundle invset-… --out m92.zip
sdp investigate reproduce m92.zip
```

- `bundle` writes the evidence dossier: one zip of about 16 MB holding the plan and who approved it, each
  stage's output, every candidate with its notes, each image with its stars marked, the FITS cutouts and
  Gaia rows the tests read, and a README.
- `reproduce` re-runs the tests from that zip alone and compares every result. For M92 it takes about
  5 minutes and should end with `"ok": true`, `"results_compared": 53` and an empty `differences` list. It exits with code 1 if anything
  differs.

## The app route

The same journey in the app shows things a terminal cannot: the image itself with every Gaia star drawn
on it (green circle found, amber dashed circle ambiguous, red diamond off its place, red square missing),
the checks each FITS file passed, and a **Zoom in** on each ambiguous match. On the **Start** page choose
**JWST: the outskirts of M92**, then follow [docs/jwst-field.md](../jwst-field.md) from step 2. At the
foot of the set page, **Save evidence dossier** and **Check it reproduces** do what `bundle` and
`reproduce` do above.

## Your own field

`sdp demo jwst --region "icrs;circle(RA,DEC,RADIUS)" --label "My field"` runs any field, as long as JWST
has a NIRCam mosaic over it and Gaia has stars there. Keep the radius small (0.004° is 14 arcseconds):
the profile cuts a 40-arcsecond square from each image. Avoid crowded cluster cores, where Gaia has few
well-measured stars. Every setting of the `jwst-image-check` profile is explained in the
[profiles guide](../../profiles/README.md#archive-settings-jwst-image-check).

## If it goes wrong

- **The run stops while downloading.** MAST sometimes answers 504 for minutes at a time. Wait, then run
  `sdp demo jwst` again (or press **Start again**); the plan stays approved and nothing is fetched twice.
- **"No archive offers …" in the plan.** Run `sdp integration register` (or `sdp demo jwst --register`).
- **An image's checks failed.** That file was not curated, and no test read it. The failed check says why.
- **`reproduce` lists differences.** Either a different version of the tests is installed now, or the
  file was changed. Each difference names the result that moved.
