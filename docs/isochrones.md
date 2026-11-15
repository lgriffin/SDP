# MIST isochrones and stellar masses (STL-033 to STL-036)

**Status: EXPLORATORY.** Nothing here is a discovery claim. Every result file says so.

SDP does not run MESA. It reads **MIST** (MESA Isochrones and Stellar Tracks; Dotter 2016, ApJS 222, 8;
Choi et al. 2016, ApJ 823, 102), a published grid of stellar models that the MIST team computed with
MESA, and uses it for two jobs:

1. **Cluster isochrone fits.** A real star cluster is one age, one composition and one distance, so its
   members lie along one isochrone in the colour-magnitude diagram. `sdp isochrones fit` finds the age,
   [Fe/H], extinction and distance that put a cluster's members closest to one MIST isochrone, and
   says what share of them sit on it.
2. **Stellar masses.** A cluster member's mass is read off its cluster's isochrone. A field star's mass
   and age come from weights over the whole grid. The hidden-companion screen uses the cluster masses:
   a partner heavier than the heaviest star still alive in its cluster cannot be an ordinary cluster
   star (`sdp companions screen --isochrones`).

Code: `sdp_stellar/isochrones.py` (science) and `sdp/cli/isochrones.py` (commands and plots). Settings:
`sdp_stellar/isochrones.json`, hashed into every result.

## Commands

```bat
sdp isochrones fetch                       :: download MIST v1.2 + Gaia photometry (about 116 MB), keep SDP's columns
sdp isochrones fetch --file MIST_v1.2_vvcrit0.4_UBVRIplus.txz   :: or use a tarball you downloaded yourself
sdp isochrones fit --sweep gaia-dr3        :: every sweep cluster, members from the bulk Gaia copy
sdp isochrones fit --members members.csv   :: cluster,source_id, members from the bulk Gaia copy
sdp isochrones fit --stars stars.csv       :: cluster,source_id,phot_g_mean_mag,bp_rp,parallax,parallax_error
sdp isochrones stars --stars field.csv     :: source_id,phot_g_mean_mag,bp_rp,parallax[,parallax_error,a_v]: one mass and age each
sdp companions screen --sweep gaia-dr3 --isochrones --name clusters-mist
```

The PC script `harness\run-isochrones.ps1` runs the fetch, the sweep fit and the companion screen.

## The grid

`fetch` reads every `.iso.cmd` file in MIST's `MIST_v1.2_vvcrit0.4_UBVRIplus.txz` (rotating models,
v/vcrit = 0.4; one file per [Fe/H] from -4.0 to +0.5), keeps the isochrones from log age 6.5 to 10.15 and
the columns named in the settings (EEP, age, initial and current mass, Teff, log g, L, phase, Gaia G, BP,
RP), and writes one Parquet table, `<SDP data>\isochrones\mist-v1.2-gaia.parquet`. The table's metadata
carries the MIST version, the MESA revision, the photometric system, the tarball's URL and SHA-256 and
the files read. A file with extinction already applied, a second file for the same [Fe/H], a file of
another photometric system or MESA revision, or a table without the expected Gaia columns is refused
with the reason. The header is read in the layout of MIST's own reader (`read_mist_models.py`: the
photometric system after `=` on line 3, A_V at the end of line 9).

The tarball's files carry three sets of Gaia bands (DR2 revised, MAW, and EDR3); SDP reads the EDR3
ones (`Gaia_G_EDR3`, `Gaia_BP_EDR3`, `Gaia_RP_EDR3`), the passbands Gaia DR3 photometry uses. The header
layout and the column names were checked against the real tarball on 2026-11-15 (A_V sits on a line
`# CCM89 extinction: Av =  0.000`), and a test keeps the first lines of a real file verbatim.

## How a cluster is fitted

For each member: dereddened colour (BP-RP)0 = BP-RP - E(BP-RP) and absolute G0 = G - A_G - (m-M), with
A_G and E(BP-RP) fixed ratios of A_V (Wang and Chen 2019). The member's distance to the isochrone is
measured in units of its errors: a smooth model of Gaia's photometric errors by G (the bulk copy keeps
none) plus floors of 0.03 mag in colour and 0.08 mag in G, which also stand for the models' own error.
Each squared distance is capped at 3 squared, so an unresolved binary (0.75 mag above the line) or a
field interloper costs a fixed amount and cannot drag the fit. The cluster's mean parallax, corrected for
Gaia's zero point (-0.017 mas, Lindegren et al. 2021) and with a 0.011 mas systematic floor, adds a
Gaussian prior on the distance modulus.

The search is a grid: every [Fe/H] from -1.0 to +0.5, every second MIST age, A_V from 0 to 3 in steps of
0.1 and the modulus within three sigma of the parallax's; then the neighbouring metallicities, all ages
nearby and fine steps of A_V (0.02) and modulus (0.01) around the best. A grid is slower than an
optimiser but gives the same answer every time and a cost for every point, which is what the plots show.

Each parameter's **range** is the set of grid values whose best cost is within 1 of the minimum: a grid
profile, not a calibrated confidence interval. **on_isochrone_share** is the share of fitted members
within 2 errors of the line.

**Known answers.** The settings list sanity windows for four famous clusters (the Pleiades, the Hyades,
Praesepe and M67). A run that fits one of them reports whether its age falls inside its window. The
windows are broad and **not yet verified** against a published Gaia fit (for example Gaia
Collaboration, Babusiaux et al. 2018); until Leigh checks them, a miss prints a warning instead of
failing the run, unlike the companion screen's Gaia BH1 check.

## Masses

* **Cluster member:** the initial and current mass, and the phase, of the nearest point of the cluster's
  best isochrone, measured exactly in the star's own errors. A star more than 3 errors off the line (a
  white dwarf, an equal-mass photometric binary, an interloper) gets no mass, since the isochrone does
  not describe it; `on_isochrone` says whether the star is within 2 errors of the line.
* **Heaviest living star:** the largest current mass anywhere on the cluster's isochrone. No partner
  that is itself a normal star of the cluster can be heavier. A heavier partner is a remnant (white
  dwarf, neutron star, black hole), a merger product such as a blue straggler, or not coeval with the
  cluster.
* **Field star:** weights over every grid point of one metallicity (the [Fe/H] nearest 0.0 by default):
  a Kroupa initial mass function times the span of initial mass the point stands for, a prior uniform in
  age, and a Gaussian likelihood in the star's errors: the photometric errors at its apparent G, and the
  error of its distance modulus from `parallax_error` (with the 0.011 mas floor) added to its G error. Each star gets the 16th, 50th and 84th
  percentiles of its initial mass, current mass and log age, its most likely phase, and `off_grid` when
  no model comes within 5 errors (a white dwarf, a binary, bad photometry). One star's age is usually
  poorly fixed; its mass much better.

## In the companion screen

`sdp companions screen --isochrones` fits each cluster after finding its members again. `clusters.csv`
gains `iso_log_age`, `iso_feh`, `iso_av`, `iso_distance_modulus`, `iso_on_isochrone_share`,
`iso_heaviest_living_msun` and `iso_turnoff_initial_msun`. Each orbit's row in `candidates.csv` gains:

| column | meaning |
| --- | --- |
| `m1_mist_msun` | the visible star's current mass on the cluster's isochrone, empty when it is more than 3 errors off it (Gaia's own M1 stays in `m1_msun`) |
| `m1_mist_on_isochrone` | whether the star is within 2 errors of the isochrone |
| `m2_dark_mist_msun` | the dark partner's mass from the same mass function with the MIST M1 |
| `m2_lower_mist_msun` | Gaia's lower bound carried over to the MIST M1 (the same mass function, solved again) |
| `heavier_than_living` | `m2_lower_mist_msun` is above the cluster's heaviest living star |

The verdicts do not change: they stay Gaia's rule (STL-020). The isochrone columns sit beside them so a
reader can see where the two lines of evidence agree. The DR4 predeclaration can then decide whether
`heavier_than_living` becomes part of the rule.

## What SDP took from how MESA presents its results

Leigh asked how MESA itself presents its conclusions and outputs, so SDP could learn from it. From the
MESA documentation (docs.mesastar.org, "Using MESA output" and "Test suite") and MIST's README:

| MESA / MIST practice | What it does | What SDP does with it |
| --- | --- | --- |
| `history.data`: a header block of global values, then one row per model, columns named on a line of their own | Global facts are separate from the evolving table; one file holds a whole run | `summary.json` is the header (MIST version, MESA revision, grid SHA-256, settings SHA-256, counts, known answers); `clusters.csv` is one row per cluster |
| `profileN.data` and `profiles.index`: one file per saved model, each with its own header, indexed by model number and a priority | Detail for one moment, findable from the index | `members/<cluster>.csv`, one table per cluster of each member's dereddened position, mass, phase and distance to the line; `clusters.csv` is the index |
| `history_columns.list` / `profile_columns.list`: the user picks the columns from a documented default list | The output is chosen in a file, not in code | The `columns` block of `isochrones.json` names the MIST columns read; changing it changes the settings hash |
| Inlists (`&star_job`, `&controls`, `&pgstar` namelists) | Every choice of a run lives in plain text beside it | `isochrones.json` holds every choice (grid range, phases, extinction ratios, floors, search steps, priors), and its SHA-256 is in every result |
| pgstar: the same live plots for every run (HR diagram, Kippenhahn, T-rho profile, grids of panels) | A reader can judge a run at a glance, and runs look alike | `plots/<cluster>.svg`, the same two panels for every cluster: the dereddened CMD with the fitted isochrone (members off it hollow), and the best cost at each age and [Fe/H] tried (the fit's degeneracies in one picture) |
| MIST's EEPs and `phase` labels (-1 pre-main sequence, 0 main sequence, 2 red giant branch, 3 core helium burning, 4 early AGB, 5 TP-AGB, 6 post-AGB, 9 Wolf-Rayet) | Models of different masses compared point for point; each point says what kind of star it is | Members carry their phase by name; the fit uses phases -1 to 4 and never draws a line across a phase change (the red giant tip to the clump) |
| MESA's test suite: each case has a README stating what it checks and a pass criterion, and a run reports pass or fail | Every capability carries a known answer | The famous-cluster windows (a warning until verified), Gaia BH1 in the companion field list (a failure), and the EARS requirements STL-033 to STL-036 with their tests |
| Version stamps in every output (the MESA revision in MIST's file headers; MESA releases on Zenodo) | A result says which code made it | The grid keeps MIST's version, MESA revision and the tarball's SHA-256; every summary repeats them |

What SDP does **not** copy: MESA's fixed-width text tables (SDP writes CSV and JSON, which every tool
reads), and its photos and restarts (an isochrone fit takes seconds, so a rerun is cheaper than a
checkpoint).

## Limits

* The extinction is one ratio per band; real Gaia extinction depends on a star's colour, a few per cent
  for A_V of a few magnitudes.
* The grid is MIST v1.2 (MESA revision 7503).
* MIST rotating models (v/vcrit 0.4) at solar-scaled [alpha/Fe] only; no grid of other rotation rates.
* Unresolved binaries are capped, not modelled; a cluster rich in binaries fits with a smaller
  `on_isochrone_share`.
* White dwarfs are not in the fit phases, so a white dwarf member is off the line and gets no mass.
* The profile ranges are not calibrated confidence intervals.
* The tests use invented isochrones in MIST's layout (`python/tests/stellar/mist_toy.py`), because the
  sandbox that wrote this cannot reach mist.science, plus the first lines of one real file.
