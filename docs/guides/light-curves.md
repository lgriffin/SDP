# Journey: light curves from Gaia and NEOWISE

> **Not on `main` yet.** This journey needs pull requests
> [#108](https://github.com/lgriffin/SDP/pull/108) (Gaia DataLink) and
> [#114](https://github.com/lgriffin/SDP/pull/114) (WISE and NEOWISE). Both were open on 29 October 2026.
> Once they merge, update (`git pull`, `uv sync --frozen`, or the new desktop installer) and run
> `sdp integration register` once more, so the database learns the two new sources.

**Time:** 2 to 5 minutes a field · **Download:** a few MB · **Needs:** any
[install](README.md#1-install-sdp), updated after both pull requests merge

A **light curve** is a star's brightness measured again and again. A star whose brightness changes more
than its error bars allow is *variable*: a pulsating star, an eclipsing pair, a flaring young star. This
journey asks that question of two sets of light curves:

| Profile | Light curves from | Years | Light |
|---|---|---|---|
| `gaia-light-curves` | Gaia's own scans, through Gaia's DataLink service | 2014–2017, 20 to 70 points | Visible (Gaia's G band) |
| `wise-light-curves` | NASA's WISE and NEOWISE, from IRSA | 2010, then 2013–2024, about 200 points | Infrared (3.4 µm, WISE's W1) |

Both run the same `variability` test: do the measurements scatter more than a steady star's would? It
also reports the strongest repeating period. The [profiles guide](../../profiles/README.md#tests) (once
updated by the pull requests) explains both.

## Gaia light curves

The field is a small circle in the Pleiades holding a known variable star.

```bat
sdp investigate --region "icrs;circle(56.5349,23.3473,0.05)" --profile "%SDP_DATA%\profiles\gaia-light-curves.yaml" --label "Pleiades Gaia light curves" --actor leigh
```

On the developer-tools route: `uv run sdp …` from `python`, with `--profile ..\profiles\gaia-light-curves.yaml`.
In the app, choose `gaia-light-curves` under **Tests to run**.

**Success** (live run, 29 October 2026, about 2 minutes): one candidate,
**Gaia DR3 64971933089093760**, with a period near 106 days. Only stars Gaia published a light curve for
are tested; the rest are listed and skipped, so most stars in a small field are not judged.

Gaia's error bars on each point are tiny and leave out a small calibration floor, so a bright star with
only slight scatter can still come out significant. Look at the period and the light curve before
reading much into a flag.

## WISE and NEOWISE light curves

The same neighbourhood, a smaller circle, because each star costs a request to IRSA and a NEOWISE light
curve can take up to a minute to cut out:

```bat
sdp investigate --region "icrs;circle(56.5349,23.3473,0.02)" --profile "%SDP_DATA%\profiles\wise-light-curves.yaml" --label "Pleiades WISE light curves" --actor leigh
```

**Success** (live run, 29 October 2026, about 4½ minutes on a 72-arcsecond field):

- 4 stars examined; the field's error scale came out at 1.17 (see below);
- one candidate, **Gaia DR3 64971967448830592**, p = 0.007, with no significant period;
- the null fields flagged 1 in 40.

Two things to know when reading it:

- **NEOWISE's quoted error bars are too small.** In the Pleiades every star, steady or not, scattered
  1.3 to 1.8 times more than its errors allow. The profile therefore scales every error bar by what the
  field's own stars show (`error_scale: field`), so a star is flagged only when it scatters more than its
  neighbours. That needs at least three light curves in the field.
- **WISE's view is blurry: about 6 arcseconds.** Two stars closer than that share one light curve. SDP
  keeps such a pair as a **blend** rather than picking one, and the candidate page says so.

The Gaia variable above does **not** stand out in WISE's infrared light curve: its scatter is what every
star in the field shows. Variable in one band and steady in another is itself a clue about what kind of
star it is.

## If it goes wrong

- **"No archive offers …" in a plan, or the source is unknown.** Run `sdp integration register` again
  after updating.
- **The WISE run is slow.** Make the circle smaller; each star is one IRSA request of 8 to 70 seconds.
- **Every star is skipped.** The field has no stars with published light curves. Try the regions above
  first, then widen slowly.
