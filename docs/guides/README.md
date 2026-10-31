# SDP user guides

These guides take you from "nothing installed" to real results, one step at a time. They are
written for scientists who would rather not think about computers. Every guide gives the exact
commands to type, says how long each step takes, and says what you should see when it worked.

If a word is new, look it up in the [glossary](glossary.md).

## 1. Install SDP

Pick **one** of these. If you are not sure, take the first.

| Guide | Pick it when | You get |
|---|---|---|
| [Windows desktop app](install-desktop.md) | You use Windows and want to click, not type | The app, its own database, and the SDP Command Prompt |
| [Developer tools](install-dev-tools.md) | You want the newest code, big data on another drive (such as `D:\SDP`), or Linux or macOS | The `sdp` command from a copy of the repository, with your own PostgreSQL |
| [Containers](install-containers.md) | You already use Podman or Docker | The whole stack (database, API, app in the browser) in containers |
| [pip, into a Python environment](install-pip.md) | You live in Python and only want the `sdp` command | The command line only; you bring the database |

## 2. Follow a journey

Each journey is one piece of real science, start to finish. Do the warm-up first: every other
journey assumes it worked.

| Journey | What you will do | Time | Needs |
|---|---|---|---|
| [First look at a field](first-look.md) | Your first real run: the Pleiades, Gaia only | 5 to 15 minutes | Any install |
| [Hunt for star clusters](discovery-search.md) | Look for stars moving together, copy all of Gaia to your own disk, and sweep the sky | Minutes for one field; a day for the whole sky | Any install; 400 GB free for the full copy |
| [A star's history from Harvard's plates](star-history.md) | Check bright stars against a century of photographs (DASCH) | 20 to 40 minutes | Any install |
| [Reproduce Einstein's 1919 eclipse test](einstein-1919.md) | Rebuild the 1920 paper's three results from its printed tables | 5 minutes | Developer tools |
| [Rerun Einstein's 1921 globular-cluster calculation](einstein-1921.md) | Check Einstein's arithmetic for M13, redo his calculation with Gaia's measured star motions, then the corrections | 1 to 30 minutes | Developer tools; the bulk Gaia copy for the full run |
| [Investigate a JWST field](jwst-field.md) | Gaia's stars against a James Webb image, through to a saved evidence dossier | About 30 minutes | Any install |
| [Your own telescope frames](my-frames.md) | Plate-solve your own pictures and check Gaia against them | Minutes per target | Developer tools or the desktop app |
| [Light curves from Gaia and NEOWISE](light-curves.md) | Find stars whose brightness changed, in visible and infrared light | 2 to 5 minutes a field | Waiting on pull requests #108 and #114 |
| [Record live archive data](recording-live-data.md) | Refresh the recordings the tests replay | A few minutes | GitHub, or the developer tools |
| [Check a candidate, then submit it](compare-and-submit.md) | Put a candidate beside SIMBAD, Gaia, 2MASS, WISE, Pan-STARRS and VSX; where a discovery goes | A minute per candidate | Any install |
| [Releases and checking results](releases-and-verification.md) | What a release contains, and how anyone can check a result | Varies | Any install |

## What every result means

Almost everything SDP finds while exploring is labelled **EXPLORATORY**. A candidate says *where
to look*, not *what is true*. A result becomes a claim only through a separate experiment whose
plan was frozen before it ran (a *predeclared* experiment), and only that kind of run issues a
verdict. The [releases and checking results](releases-and-verification.md) guide explains how.

## Where to read more

These guides link to the reference pages rather than repeating them:

- [profiles/README.md](../../profiles/README.md): every test profile and every setting, in plain words.
- [docs/live-data.md](../live-data.md): live archive data, measuring pulls, your own frames, the Gaia bulk copy.
- [docs/jwst-field.md](../jwst-field.md): the JWST walkthrough, screen by screen.
- [docs/testing.md](../testing.md): how SDP itself is tested.
- [docs/protocols](../protocols): the two predeclared experiments, UC-01 (star clusters) and UC-02 (Einstein 1919).
- The data-pull issues on GitHub, which record real runs on a Windows PC with their timings:
  [#62](https://github.com/lgriffin/SDP/issues/62) warm-up,
  [#64](https://github.com/lgriffin/SDP/issues/64) every archive,
  [#66](https://github.com/lgriffin/SDP/issues/66) one pull per archive,
  [#67](https://github.com/lgriffin/SDP/issues/67) the stress run,
  [#76](https://github.com/lgriffin/SDP/issues/76) all of Gaia,
  [#90](https://github.com/lgriffin/SDP/issues/90) your own frames.

## What is still changing (29 October 2026)

- **Pull requests [#108](https://github.com/lgriffin/SDP/pull/108) and [#114](https://github.com/lgriffin/SDP/pull/114)**
  add Gaia DataLink and NEOWISE light curves. The [light-curves guide](light-curves.md) works once both merge.
- **Pull request [#104](https://github.com/lgriffin/SDP/pull/104)** makes the candidate stage fast on very large
  fields (hundreds of thousands of stars). Until it merges, keep fields under a few degrees.
- **CDS in Strasbourg** (SIMBAD, VizieR and X-Match) was down on 29 and 30 October 2026. SIMBAD fails over
  to Harvard by itself, and `sdp benchmark fetch` to VizieR's mirrors. Cross-matching (the 2MASS cross-check
  in the cluster search) now runs on your own machine by default, so it never waits for CDS
  (`SDP_CROSS_MATCH`, [docs/live-data.md](../live-data.md)).
- **Release 0.3.0** waits in pull request [#53](https://github.com/lgriffin/SDP/pull/53). The last
  published release, v0.2.0, has no installer attached, so the desktop guide takes it from CI.
