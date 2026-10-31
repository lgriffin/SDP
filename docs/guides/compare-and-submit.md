# Check a candidate against catalogues, then submit it

A candidate from any journey is a lead. This guide covers the two checks that kill most leads (is it
already known, is it an artefact) on the **Compare with catalogues** page, and where a discovery goes
once a confirmatory test has passed.

## Compare with catalogues

1. Open a candidate (**Candidates**, a set, a candidate) and press **Compare with catalogues**.
2. Tick the catalogues to ask. The first time every one is ticked:

   | Catalogue | What it tells you |
   | --- | --- |
   | SIMBAD | Whether the object is already known, its type, and how many papers mention it |
   | Gaia DR3 | Position, parallax, proper motion and brightness of every Gaia source nearby |
   | 2MASS | Near-infrared J, H, Ks, measured about 1998-2001 |
   | AllWISE | Mid-infrared W1 to W4 and contamination flags, 2010-2011 |
   | Pan-STARRS DR2 (MAST) | Optical g, r, i, z, y, deeper than Gaia, north of Dec -30 |
   | AAVSO VSX (VizieR) | Whether a variable star is already catalogued here |

3. Choose a search radius: 5″ suits a star, minutes of arc a cluster. Press **Pull references**.
   Archives answer in seconds to a minute.

The page then shows **What the references say** in plain sentences, and **Side by side**: what the search
found beside each catalogue's nearest source. The candidate is moved along its proper motion to each
catalogue's epoch before distances are measured, so a fast-moving star still lines up with 2MASS from
1999. A cell is marked when brightness in the same band differs by more than 0.3 mag, parallax by more
than 3σ, proper motion by more than 3 mas/yr (or 20%), or the source is more than 1″ away.

Every pull is kept with the candidate: who asked, when, the radius, the release and citation, and the
archive's raw answer (linked under **Every pull**). A catalogue that does not answer says so and the
others still show. SIMBAD falls back to CDS's Harvard copy and VSX to VizieR's mirrors during a CDS
outage, unless `SDP_ARCHIVE_MIRRORS=0`. Pulls are context, not evidence: they never change the
candidate's results, rank or state (INV-023).

The API behind the button is `POST /investigations/{id}/references` with
`{"sources": ["simbad", "gaia"], "radius_arcsec": 5, "actor": "leigh"}`.

## From lead to claim

If nothing explains the candidate, follow
[From lead to claim](releases-and-verification.md#from-lead-to-claim): write a predeclaration that names
evidence the search did not use (another archive, release, epoch, or your own frames), freeze it,
promote the candidate, run and validate, approve (one approval is enough), bundle and publish.

## Where a discovery goes

| What you found | Where it goes |
| --- | --- |
| New star cluster or moving group | A refereed paper (A&A, MNRAS, AJ) or a Research Note of the AAS; CDS then adds it to SIMBAD and VizieR |
| New variable star | The AAVSO VSX submission form (free AAVSO account) |
| Transient (supernova, nova, outburst) | The IAU Transient Name Server; an Astronomer's Telegram for fast alerts |
| Comet, asteroid or other moving object | The Minor Planet Center, astrometry in ADES format with an observatory code |
| New double star | The Washington Double Star Catalog, usually through the Journal of Double Star Observations |

The IAU Working Group on Star Names does not take naming requests for new discoveries; a new object is
known by its catalogue designation. Check each venue's own submission page before sending.
