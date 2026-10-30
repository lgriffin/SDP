# Journey: a first look at a field

**Time:** 5 to 15 minutes · **Download:** about 1 MB · **Needs:** any [install](README.md#1-install-sdp)
with `sdp integration register` done, and an internet connection

Your first real run. It checks that SDP can reach Gaia and SIMBAD, stores what they send, and runs
two tests on every star. Every other journey builds on this one.

## The field

The centre of the **Pleiades** (the Seven Sisters), a star cluster you can see by eye in winter:

- right ascension `56.75`, declination `24.1167` (degrees);
- radius `0.2` degrees, a little under half the width of the full Moon.

About 328 stars here pass the profile's quality cuts. A radius of 0.5° would give 1,805 stars and take
much longer, so keep it small the first time.

## The profile: first-look

Gaia only, two questions of every well-measured star:

1. Is its distance (parallax) unusual for the field? (`parallax-consistency`)
2. Does it sit where the field's stars sit in colour and brightness? (`cmd-coherence`)

A star becomes a candidate only when **both** say yes. The [profiles guide](../../profiles/README.md)
explains every setting.

## In the app

1. Open SDP and click **Start** in the menu.
2. Under **Explore a field**, fill in:
   - Name for this field: `Pleiades warm-up`
   - Right ascension: `56.75`
   - Declination: `24.1167`
   - Radius: `0.2`
   - Tests to run: `first-look` (already selected)
   - Your name: for example `Leigh`
3. Click **Plan the downloads**. Nothing is downloaded yet. The plan page shows:
   - **What it costs**: about 1 MB. This is an upper bound (5,000 rows × 27 columns × 8 bytes).
   - **What no archive could supply**: empty.
4. Click **Approve**. Below 500 MB, approval is automatic and recorded.
5. Click **Download now**. You can leave the page and come back.
6. You land on **Candidates in Pleiades warm-up**.

## In a terminal

The same steps, typed. In the SDP Command Prompt type them as shown. On the developer-tools route,
put `uv run` in front of each `sdp` and run them from the `python` folder.

```bat
sdp plan draft --region "icrs;circle(56.75,24.1167,0.2)" --profile "%SDP_DATA%\profiles\first-look.yaml" --label "Pleiades warm-up" --actor leigh
```

On the developer-tools route the profile is in the repository: `--profile ..\profiles\first-look.yaml`.

It prints the plan, for example:

```json
{
  "gaps": [],
  "items": 1,
  "plan_id": "sha256:702cd28f…",
  "requires_signed_approval": false,
  "state": "drafted",
  "total_bytes": 1080000
}
```

Copy the `plan_id` (the whole thing, starting `sha256:`) into the next two commands:

```bat
sdp plan approve sha256:702cd28f… --actor leigh
sdp plan execute sha256:702cd28f… --actor leigh
```

`approve` answers `"state": "auto_approved"`. `execute` runs the four stages and prints a summary with
the new set's id (`invset-…`). Then:

```bat
sdp investigate list invset-…        the candidates, strongest first
sdp investigate show invset-…        the whole set: field, profile, tests, null fields, archive releases
```

A shortcut that skips the plan: `sdp investigate --region "…" --profile … --label "…"` runs the field
straight away. The plan route is better practice, because the plan records what was fetched and why.

## What success looks like

These are the numbers from a real run on 29 October 2026 (issue
[#62](https://github.com/lgriffin/SDP/issues/62#issuecomment-6506919216)), which took 2½ minutes:

- No error. Every stage is `completed`: identify, retrieve, test, candidates.
- **Objects examined: 328.**
- **How often the tests fire by chance**: 10 null fields per test.
- **Candidates:** a handful (that run found 4), each labelled EXPLORATORY. None is fine too.
- The artifacts folder grew by about 1 MB.

## Reading the result

- A **candidate** is a star both tests flagged. Open it to see each test's p-value, the null-field
  comparison, and what SIMBAD already knows about it. Use **Park**, **Close** or **Add a note**,
  always saying why. In a terminal: `sdp investigate dossier <investigation_id>`, `sdp investigate park
  <investigation_id> --actor leigh --reason "…"`.
- The **false-alarm rate** is the share of null-field stars the test still flagged. If it is much higher
  than alpha (1%), treat that test's flags on this field with suspicion. On the Pleiades,
  parallax-consistency's rate came out at 17%: a cluster's stars all share one distance, which makes the
  "unusual distance" question hard to ask fairly. This is a known open question, not a fault in your run.
- Every result is EXPLORATORY: a lead, not a finding.

## Try next

- Change the radius, or try another field: Praesepe is `icrs;circle(130.1,19.67,0.5)`.
- Copy `first-look.yaml`, change one setting, give it a new `profile_id` and `version`, and compare.
  The [profiles guide](../../profiles/README.md#making-your-own) says how.
- Move on to [hunting for star clusters](discovery-search.md) or [a star's history](star-history.md).

## If it goes wrong

- **"No archive offers …" under the plan's gaps.** The database does not know the archives yet: run
  `sdp integration register`, or click **Read what the archives offer** on the Start page.
- **A Gaia or SIMBAD error.** The archive may be busy. Wait ten minutes and run `plan execute` again;
  it reuses everything already stored. If SIMBAD's home in Strasbourg is down, SDP switches to its
  Harvard mirror by itself and logs a warning; the first switch costs about two minutes of waiting.
- **Investigate is not wired**, or the app will not start: attach the newest file from the `logs`
  folder to a GitHub issue.
