# Publication linkage: public data no paper names

Discovery Roadmap capability C3. Which public archive programs have no refereed paper? The archive
says what exists and when it became public; NASA ADS says what has been written. Joining the two gives
an underused-data register, the input to a meta-science note on archive use (roadmap section 6), and
the prior-art check every SDP note runs before it is submitted (roadmap section 8).

Requirements PUB-001 to PUB-004 (`requirements/publication/linkage.ears`). Science in
`sdp_investigate/linkage.py`, ADS client in `sdp/adapters/archives/ads.py`, MAST's program list in
`sdp/adapters/archives/mast_programs.py`, commands in `sdp/cli/publication.py`.

## The ADS token

ADS needs a personal API token (https://ui.adsabs.harvard.edu/user/settings/token). Put it in a
file called `.env` at the top of your SDP checkout:

```
ADS_API_TOKEN=<your token>
```

`.env` is gitignored; `.env.example` shows the shape. `sdp publication` reads `.env` from the working
directory or any folder above it up to the checkout's top, and a variable already set in the shell
wins. The token goes only into the request's `Authorization` header: it is never written to a
cassette, an output file, a log or an error message. ADS allows 5,000 searches a day per token.

## Commands

`sdp publication search '<ADS query>' [--refereed] [--rows 10]` is the prior-art check: how many
records match, and the newest few with their citation counts. For example
`sdp publication search 'full:"Ruprecht 147" AND full:"Gaia DR4"' --refereed`.

`sdp publication programs [--mission JWST] [--out DIR]` builds the register:

1. MAST's CAOM TAP service lists every program of the mission (number, PI, title, category) and,
   for each, its level-3 products and the first and last date one is public. Two queries, seconds.
2. Each science program (GO, GTO, ERS, DD, SURVEY; not calibration, commissioning or engineering)
   whose first level-3 product has been public for at least 365 days is asked about in ADS, once:
   papers whose full text names the mission and the program the ways papers write it
   (`program 1324`, `PID 1324`, `GO 1324`, `ERS 1324`, `proposal ID 1324`, `jw01324`...), from
   2022 on. ADS's `property` facet splits the answer into refereed and not.
3. Each program gets a status: `published` (a refereed paper names it), `only-preprints`,
   `unpublished` (nothing ADS holds names it), `too-recent`, `not-public`, `not-science`, or
   `not-asked-yet`.

Outputs, in `<SDP data>/publication/<mission>` unless `--out` says otherwise: `programs.csv` (every
program, with the ADS query and the first refereed bibcodes), `unpublished.csv` (oldest first),
`summary.json` (counts, marked EXPLORATORY, with the settings hash) and `ads-answers.jsonl` (every
ADS answer as it came, so a rerun asks only about programs not yet asked, or whose answer is older
than `--max-age-days`, 30 by default; `summary.json` gives the range of dates the answers were asked on). A run stops when 200 of
the day's searches are left (`--reserve`); the same command carries on the next day.

ADS answers a full-text query in one to ten seconds, so a JWST register (about 850 programs on
2026-11-05) takes one to two hours.

## What a zero means

- The count is of papers that **name** the program. A paper that uses the data but cites only a data
  DOI or a table is missed, so an unpublished program is a candidate to check by hand, not a finding.
- A paper naming an unrelated program with the same number (an HST GO program, say, in a paper that
  also mentions JWST) is counted, so a count is an upper bound on use. The bibcodes are kept to read.
- ADS's full text lags publication by weeks, hence the 365-day floor.

Before the register becomes a note, a predeclared sample of `unpublished` programs is checked by hand
(MAST's own paper links, the PI's publication list) to measure how often a zero is wrong.
