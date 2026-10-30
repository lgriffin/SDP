# Glossary

The words these guides use, in plain language. Astronomy terms first, then SDP's own.

## The sky

- **Right ascension (RA)** and **declination (Dec).** A position on the sky, like longitude and
  latitude on Earth. SDP uses degrees: RA runs from 0 to 360, Dec from −90 (south pole) to +90.
- **Arcsecond (″ or arcsec).** 1/3600 of a degree. The full Moon is about 1,800 arcseconds across.
  An **arcminute (′)** is 1/60 of a degree.
- **Cone** or **circle.** A round patch of sky: a centre and a radius. SDP writes one as a *region*,
  for example `icrs;circle(56.75,24.1167,0.2)`: the Pleiades, radius 0.2°. `icrs` names the standard
  sky coordinate frame; you can always leave it as it is.
- **Parallax.** The tiny yearly wobble of a nearby star as Earth goes round the Sun. It gives the
  star's distance: a bigger parallax means a closer star. Measured in milliarcseconds (mas).
- **Proper motion.** How fast a star crawls across the sky, in milliarcseconds per year.
- **Magnitude.** Brightness, on a backwards scale: bigger numbers are fainter. Gaia's main band is
  **G**; G = 18 is about the faintest star whose distance Gaia measures usefully.
- **Epoch.** The date a position belongs to. Gaia's positions are for 2016.0; SDP moves stars along
  their proper motion to the date of any other picture.
- **Open cluster** or **moving group.** Stars born together, which still share a motion and a
  distance. The Pleiades and Praesepe (the Beehive) are examples.
- **Light curve.** A star's brightness measured again and again over time.
- **Plate solving.** Working out exactly which patch of sky a picture shows, by matching the
  patterns its stars make against a catalogue.
- **FITS.** The file format astronomers use for images and tables. **WCS** (world coordinate
  system) is the part of a FITS header that says where on the sky each pixel points.

## The archives

- **Gaia DR3.** The European Space Agency's star-mapping satellite, data release 3 (2022): positions,
  motions, distances and colours of 1.8 billion stars.
- **SIMBAD.** The astronomers' "who's who" of known objects, run by CDS in Strasbourg, with a mirror
  at Harvard.
- **CDS.** The Strasbourg astronomical data centre. It runs SIMBAD, VizieR (published catalogues) and
  X-Match (cross-matching one catalogue against another).
- **2MASS.** An infrared survey of the whole sky (1997–2001), served by IRSA.
- **Pan-STARRS (PS1).** A deep optical survey, served by MAST.
- **MAST.** NASA's archive for Hubble and James Webb (JWST) data.
- **IRSA.** NASA's infrared archive (2MASS, WISE, NEOWISE).
- **DASCH.** Harvard's digitised photographic plates, about 1890 to 1990.
- **TAP and ADQL.** The standard way to ask an archive a question (TAP) and the SQL-like language
  the question is written in (ADQL). You never write ADQL yourself; SDP does.
- **DataLink.** The standard way an archive hands out files that are not table rows, such as Gaia's
  light curves.

## SDP's own words

- **Test profile.** The recipe for exploring a patch of sky: which stars, which archives, which tests,
  and how strong the evidence must be. Seven ship with SDP; see [profiles/README.md](../../profiles/README.md).
- **Plan.** What SDP intends to download for a field and profile, from which archive, and how big it
  is, worked out before anything is fetched. You approve it, then execute it. A plan under 500 MB is
  approved automatically; a bigger one needs your signed approval.
- **Investigation set** (`invset-…`). One run of a profile over a field: its stars, the evidence it
  gathered, its tests and its candidates.
- **Stages.** A run goes through four: **identify** (find the stars), **retrieve** (gather evidence),
  **test** (ask each question of each star) and **candidates** (rank them).
- **Candidate.** A star or group that the tests flagged. Each one is an *investigation* you can park,
  close, annotate or reopen, always saying why.
- **p-value.** The chance of seeing a result at least this unusual if nothing interesting were going on.
  Small means "chance alone rarely does this". **Alpha** is the line below which SDP calls it significant
  (0.01 in the shipped profiles).
- **Null field.** A copy of your field with the clues shuffled so any real signal is gone. How often the
  tests still fire on null fields is your honest false-alarm rate.
- **EXPLORATORY.** The label on every result from exploring. It means "a lead", never "a finding".
- **Predeclaration.** An experiment's plan (regions, cuts, tests and pass marks) written down and
  **frozen** before it runs, so it cannot be tuned to the answer.
- **Gate.** One pass-or-fail check in a predeclared experiment, such as "every measurement links back
  to its page in the paper". The gates decide the **verdict**: PASS, FAIL or INCONCLUSIVE.
- **Oracle.** A trusted reference value a gate compares against, such as the 1920 paper's published
  deflection. An oracle must be verified before a gate may use it.
- **Artifact store.** The folder where SDP keeps every file it downloads or makes, each named by its
  **SHA-256** (a fingerprint of the file's bytes), so the same bytes are never stored twice and
  nobody can swap a file without it showing.
- **Ledger.** An append-only record of who froze, approved or signed what, each entry chained to the
  previous one so any edit is detected.
- **Signing key.** An Ed25519 SSH key that proves an approval came from you. The desktop app and the
  developer setup in these guides start without one (`SDP_ALLOW_UNSIGNED=1`), which is fine for
  learning.
- **Evidence dossier.** One zip file (an **RO-Crate**, a standard research package) holding a run's
  plan, outputs, candidates and the data its tests read, so someone else can check it.
- **Cassette.** A recording of a real archive answer. Tests replay cassettes instead of calling archives.
- **Bulk copy.** Your own copy of the whole Gaia DR3 catalogue, made with `sdp archive bulk`.
- **Environment variable.** A named setting a program reads when it starts, such as
  `SDP_ARTIFACT_ROOT`. On Windows you set one with `set NAME=value` (this window only) or
  `setx NAME value` (every new window).
