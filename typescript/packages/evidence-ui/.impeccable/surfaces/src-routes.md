---
version: 1
slug: "src-routes"
primary_target: "src/routes"
related_targets: []
---

Scope: the whole evidence UI (home, run, results, candidates, plans, details). Mode: operate.

Audience and job: scientists with minimal computing experience start work, read whether it held up, review candidates and approve downloads, without a terminal for anything unsigned.

Constraints: GraphQL reads and the small REST command surface only (ADR-016); signed steps show a copyable `sdp` command; planner API arrives from the Stage E thread; exploratory and null-rate distinctions are never hidden.

Unresolved: which actions get buttons (decision card, recommended: buttons except signed steps); home layout (decision card, building the lead).

## Direction contract

FORM: seed:a811e88380e0aa9c, dealt structure 5 (work as a journey) leads.
THESIS: every piece of work shows where it is on one path (Plan, Approve, Run, Verdict, Review) and offers exactly one next step. Refuses the category default of a table of ids and states.
OWN-WORLD: deep observatory green shell (top bar), tinted paper-green ground, white work sheets, ink text. State colours only for verdicts: green verified, brick rejected, ochre inconclusive or candidate. System sans for UI, tabular numerals, mono only for ids and commands.
FIRST VIEWPORT: top bar with Home, Start, Results, Candidates, Plans, Records. Greeting line, then "Waiting for you" (things needing a decision), then "Your work" rows with journey strips and next-step buttons.
SIGNATURE: the gate ruler, each predeclared gate drawn as a scale with the threshold mark and where the measured value fell, so a verdict reads at a glance.
STORY: the scientist knows where their work is, why a verdict came out as it did, and what is safe to do next.
