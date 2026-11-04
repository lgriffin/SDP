# Look for hidden black holes in star clusters

A star with an unseen partner wobbles on the sky. Gaia DR3 fitted an orbit to about 169,000 such
stars. From the orbit's size, its period and the star's distance, Kepler's third law gives a lower
bound on the partner's mass. When Gaia found that no ordinary (main-sequence) partner could explain
the orbit, the partner is dark: a white dwarf, a neutron star or a black hole. Above about 1.4 solar
masses (the Chandrasekhar mass) it cannot be a single white dwarf.

`sdp companions` runs that test on the members of the star clusters your sweep found. A cluster gives
two things a lone star does not: one shared distance, more precise than any one star's parallax, and a
known age. Every result is **EXPLORATORY**: this is the Gaia DR3 trial of the screen that will run on
Gaia DR4, released on 2 December 2026, whose individual measurements let SDP fit the orbits itself.

## Run it

```bat
powershell -ExecutionPolicy Bypass -File harness\run-companions.ps1
```

or step by step:

```bat
uv run sdp companions fetch
uv run sdp companions field
uv run sdp companions screen --sweep gaia-dr3
```

`--members my-members.csv` (columns `cluster,source_id`) screens a published member list instead of a
sweep.

## What success looks like

- `field` says **Gaia BH1 is flagged as a neutron star or black hole**. That is the known answer: BH1
  (source_id 4373465352415301632) is a black hole of about 10 solar masses found this way. If it is not flagged,
  something is wrong; stop and report it.
- `field.csv` lists every flagged orbit in the whole sky. Treat each as a lead, not a find: a published
  orbit can be a poor fit, which is why the screen compares each cluster with the field.
- `clusters/candidates.csv` lists every cluster member with a Gaia orbit, flagged ones first, with the
  partner's lower mass bound at the cluster's distance. `seed_member` is false for members the sweep
  had left out because they wobble (the sweep drops high-RUWE stars); the screen finds members again
  without looking at RUWE.
- `clusters/clusters.csv` gives each cluster's members, its wobblers (RUWE well above stars of the same
  brightness), its flagged orbits, and how many of each the field would give for stars of the same
  brightness and distance (`*_expected`), with the chance the field alone gives as many (`*_field_p`).

## Terms

- **RUWE**: how badly a single-star model fits a star's Gaia positions. A wobbling star has a high RUWE.
- **Dark partner**: Gaia found no flux ratio that fits two ordinary stars, or put the orbit in its
  "AMRF class III" (a test for compact companions).
- **Lower mass bound**: Gaia's `m2_lower`, the 16% level of the partner's mass from `binary_masses`;
  at a cluster's distance SDP moves it to that distance, taken two standard errors nearer.
