# Investigate one JWST field from start to finish

This walkthrough takes one patch of sky from "where shall I look?" to a saved evidence dossier
that someone else can check. It uses the outskirts of the globular cluster M92, where programme
1334 imaged the field with NIRCam and Gaia measures the stars well. You can do it in the app (the
Windows desktop app, or the evidence UI in a browser) or in a terminal. The steps are the same.

Plan on about 30 minutes the first time. Most of that is waiting for MAST.

| Step | In the app | In a terminal |
|---|---|---|
| 0. Set up the archives once | Start page: **Read what the archives offer** | `sdp integration register` |
| 1. Select a field | Start page: **JWST: the outskirts of M92** | `--region "icrs;circle(259.323,43.1505,0.004)"` |
| 2. Discover the JWST observations | **Plan the downloads** | `sdp plan draft` |
| 3. Approve a bounded download | **Approve**, then **Download now** | `sdp plan approve`, `sdp plan execute` |
| 4. Ingest and validate the FITS files | The set page: **The images**, then each image's checks | `sdp investigate show <set>` |
| 5. Overlay Gaia's stars | The image page | (app only) |
| 6. Inspect ambiguous matches | **Stars to look at**, then **Zoom in** | `sdp investigate dossier <candidate>` |
| 7. Save a reproducible dossier | **Save evidence dossier** and **Check it reproduces** | `sdp investigate bundle <set>`, `sdp investigate reproduce <file>` |

`sdp demo jwst` in the SDP Command Prompt runs steps 0 to 3 in one go and prints the set id.

## 0. Set up the archives (once)

A fresh installation does not yet know what each archive offers, so it cannot plan anything. On
the Start page, **Read what the archives offer** reads each archive's description. MAST's comes
from the Virtual Observatory registry and takes a few minutes. The page lists each archive and
when its description was last checked. You only need to do this again when an archive changes.

## 1. Select a field

On the Start page, choose the example **JWST: the outskirts of M92**. It fills in:

- the centre (RA 259.323°, Dec 43.1505°);
- a radius of 0.004° (14 arcsec);
- the **jwst-image-check** tests.

The core of M92 is too crowded for Gaia, which is why the example uses the outskirts. Type your
name, so the record shows who asked.

## 2. Discover the JWST observations

**Plan the downloads** asks the archives what they hold for this field and drafts a plan. Nothing
is downloaded yet. The plan page shows:

- each item: the Gaia rows to fetch, and the NIRCam mosaics MAST holds over the field, each as a
  40-arcsec cutout rather than the whole file;
- what it costs in megabytes and requests, against the budget;
- any gaps, meaning what the tests need that no archive offers.

## 3. Approve a bounded download

A plan under the approval threshold (500 MB by default) is approved with one click. A larger one
needs a signed approval, and the page shows the `sdp plan approve` command to run in the SDP
Command Prompt. Only what the approved plan names is ever fetched.

**Download now** starts the run, which carries on in the background. It takes 10 to 20 minutes for
M92. The page follows it through the stages:

1. find the stars;
2. download and check the data;
3. run the tests;
4. open candidates.

You can leave the page and come back. If MAST is busy and the run stops, **Start again** is safe:
the plan stays approved.

## 4. The calibrated FITS files and their checks

When the run finishes, **See the candidates** opens the set. Under **The images**, each NIRCam
cutout has a card. Open one to see:

- what it is: instrument, filter, programme and date;
- the checks its file went through before any test read it: size, checksum, units, the WCS, and
  the extensions the file says it has. A file that fails a check is never curated.

## 5. Gaia's stars on the image

The image page draws every Gaia star at the place its proper motion puts it on the image's date:

- a green circle is a star found where expected;
- a dashed amber circle is an ambiguous match;
- a red diamond is a star found off its predicted place;
- a red square is a star missing from the image.

A cross marks the detection the test matched. **Show Gaia stars** turns the markers off, so you
can look at the image itself. The image is drawn pixel y upward, as FITS viewers draw it; it is
not rotated to north up.

## 6. Ambiguous matches

**Stars to look at** lists the flagged and ambiguous stars first. **Zoom in** centres on one and
shows every hypothesis the test weighed. For example, "a blend of 2 Gaia stars" means two Gaia
stars fall on one detection, and the other star is also kept. Each flagged star links to its
candidate page, where you can park, close or annotate it, saying why.

Every result is **exploratory**: a flag says where to look, not what is true.

## 7. Save the evidence, and check it reproduces

At the foot of the set page, **Save the evidence**:

- **Save evidence dossier** downloads one file (an RO-Crate zip, about 16 MB for M92). It holds:
  - the plan and who approved it;
  - each stage's output, byte for byte;
  - every candidate with its notes;
  - each image with its stars marked (open `images/*.svg` in a browser);
  - the FITS cutouts and Gaia rows the tests read, each named by its SHA-256;
  - a README.
- **Check it reproduces** re-runs the tests from that file alone and compares every result. For
  M92 it takes about 5 minutes. When it finishes, the page says "Reproduced" and how many results
  matched, or lists every difference.

Anyone with SDP can check the file without your database or any archive:

```
sdp investigate reproduce invset-….zip
```

The command exits with code 1 if any result differs beyond the tests' numerical tolerance, and it
refuses a file whose checksums do not match.

## In a terminal only

```
sdp integration register
sdp demo jwst                         # plans, approves (as "demo") and runs M92; prints the set id
sdp investigate list <set>            # the candidates, strongest first
sdp investigate dossier <candidate>   # one candidate's evidence, with every hypothesis
sdp investigate bundle <set> --out m92.zip
sdp investigate reproduce m92.zip
```

## When something goes wrong

- **"No archive offers …" in the plan's gaps.** Do step 0, or run `sdp integration register`
  again.
- **The run stops while it is downloading.** MAST sometimes answers 504 for minutes. Wait, then
  press **Start again**.
- **An image's checks failed.** Its file was not curated, and the tests did not read it. The
  failed check says what was wrong.
- **"Check it reproduces" lists differences.** Either the tests installed now are a different
  version from the ones that ran, or the file was changed. The differences name each result that
  moved.
