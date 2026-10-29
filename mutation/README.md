# Mutation high-water marks (§11.5, TST-004)

`high-water.json` holds each package's best mutation score. The weekly **Mutation** workflow scores
every package (mutmut for Python through `python/tools/mutation.py`, Stryker for TypeScript), and
`spec-harness ratchet` fails when a package drops more than 2 points below its mark or loses its
score. No mark is written by hand: the file starts empty, and the first full run records the initial
thresholds. A manual run with `update` pushes the raised marks to a `mutation/high-water-<run>`
branch for review.

Packages scored: `sdp-kernel` (domain, science and application separately), `sdp-relativity`
(5 shards), `sdp-stellar` (3 shards), `@sdp/spec-harness`, `@sdp/relativity-fit`. A shard is a group of
modules mutated against the package's full test suite, and each has its own mark. Packages from the Investigate and
integration trains join when their owners add them.
