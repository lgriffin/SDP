# Journey: a star's history from Harvard's plates

**Time:** 20 to 40 minutes · **Download:** about 80 MB · **Needs:** the [first look](first-look.md) done,
and an internet connection

Between about 1890 and 1990, Harvard photographed the whole sky again and again on glass plates. DASCH
(Digital Access to a Sky Century @ Harvard) has scanned them, so many bright stars have a century of
positions and brightnesses. This journey checks each bright star in a field against that century.

## The profile: star-history

Two questions of every bright star (G < 15, at most 300 of them):

- **long-baseline-motion:** has the star moved since about 1890 the way Gaia's measured motion
  predicts? Plate positions are good to 1 or 2 arcseconds, so plates are matched within 3.
- **variability:** has its brightness changed by more than the plates' own error bars allow? It also
  reports the strongest repeating period it finds, for you to look at. A star needs at least 20 plate
  measurements to be judged.

Either one is enough to make a candidate. Only plate tests run, so a star with no usable plates is never
a candidate here. The [profiles guide](../../profiles/README.md#tests) explains both.

## Run it

The Pleiades again, a little wider than the first look: 0.3° holds 137 stars brighter than G = 15, so
none is dropped by the 300-star cap.

In the app: **Start**, **Explore a field** with RA `56.75`, Dec `24.1167`, radius `0.3` and tests
`star-history`, then **Plan the downloads**, **Approve** and **Download now**.

In a terminal (on the developer-tools route, `uv run sdp …` from the `python` folder, and the profile at
`..\profiles\star-history.yaml`):

```bat
sdp investigate --region "icrs;circle(56.75,24.1167,0.3)" --profile "%SDP_DATA%\profiles\star-history.yaml" --label "Pleiades plates" --actor leigh
```

Most of the time is the retrieve stage, which asks DASCH for each star's plates one star at a time. Keep
fields small: each star costs requests to Harvard.

## What success looks like

From a real run on 29 October 2026 ([#66](https://github.com/lgriffin/SDP/issues/66#issuecomment-6507826795)),
which took 23 minutes:

- Every stage `completed`; `objects_examined` is 137.
- `archive_releases` lists `dasch:DR7`, `gaia:DR3` and `simbad:live`.
- The retrieve stage fetched 258 plate products (78 MB).
- The null fields fire at about 1%, as they should.

## Reading the result carefully

That run opened **87 candidates out of 137 stars**, while its null fields fired on only about 1%. When the
real field fires far more often than the shuffled ones, suspect the null model before the stars. Here
the likely reasons are:

- bright stars (G < 15) saturate on photographic plates, so their plate brightness is less certain than
  the quoted errors say;
- plate positions carry systematic errors that the motion test's null model leaves out.

So treat these flags as "the plates disagree with the simple model", and look at a few by hand before
believing any. Open a candidate to see its plate light curve and positions against Gaia's prediction,
and park or close it with a reason. This is a known open question about the null models, recorded on
issue #66.

## Try next

- A field away from any cluster, to see how often the tests fire on ordinary stars.
- Read the [profiles guide](../../profiles/README.md#quality-cuts-all-profiles) on why the plate profile
  stops at G = 15.

## If it goes wrong

- **The run is very slow.** Each star costs DASCH requests. Make the field smaller, or lower `row_cap`
  in a copy of the profile.
- **Few stars are judged.** Stars with under 20 plate measurements are skipped by the variability test.
  That is expected for faint stars and crowded fields.
- **A DASCH error.** Harvard's service may be busy. Run the same command again with `--resume <set_id>`:
  completed stages are read back, not repeated.
