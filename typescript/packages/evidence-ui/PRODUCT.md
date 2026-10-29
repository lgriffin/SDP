# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Working scientists (astronomers, physicists, historians of science) with minimal computing experience. They know their science well: proper motions, plate epochs, p-values, gates and oracles mean something to them. They do not know, and should not need to know, GraphQL, JSON, content hashes, task graphs or the shell. They come to the UI to start a piece of work, see whether it held up, and decide what to look at next. Confirmed by Leigh (2026-10-28): "scientists with minimal computing experience. We value simplicity and good UX."

A second audience is the reviewer or maintainer who needs the full provenance (hashes, lineage, ledger, raw events). Their material stays available but sits behind the scientist's view, not in front of it.

## Product Purpose

SDP (Scientific Discovery Platform) runs preregistered science reproducibly. The UI lets a scientist:

- run the programmes on the kernel: UC-01 stellar clusters (Gaia DR3), UC-02 the Einstein 1919 eclipse re-examination, and UC-03 Investigate (field to candidate on the STScI ecosystem);
- read a verdict (VERIFIED, REJECTED, INCONCLUSIVE, CANDIDATE) and understand which predeclared gates decided it;
- review Investigate candidates, beside how often the same test fires on null fields, and triage them;
- see the plan for any large archive download before it happens, and approve or refuse it (v0.4.0 plan, approve, execute; ADR-020).

Success: a scientist goes from "I want to look at this field" to "here is what was found and why it is or isn't believable" without opening a terminal for anything that does not need a signature.

## Positioning

The UI never overstates evidence. Every verdict comes from gates frozen before the run, every candidate is labelled exploratory until a confirmatory predeclaration names evidence it did not use, and every result is shown beside its null rate. Simplicity must not come at the cost of these distinctions; it comes from plain language and ordering, not from hiding them.

## Operating Context

- Runs on a lab workstation or the DGX Spark host, served by `sdp-api` (GraphQL for reading, a small REST surface for commands, ADR-016).
- Commands that need a signature (freezing a predeclaration, approving a verdict, promoting a candidate, approving an above-threshold acquisition plan) stay on the `sdp` CLI's signed workflow (ADR-004, INT-014). The UI shows the exact command to copy for those.
- Defining an experiment needs a frozen predeclaration; that is done once per protocol by a maintainer.
- Investigate test profiles are versioned YAML documents; the Imviz plugin already offers them.

## Capabilities and Constraints

- Reads: experiments, dossiers, gates, tasks, lineage, ledger, events, investigation sets, investigations.
- Commands available over REST today: run and validate an experiment, start Investigate on a field (`POST /investigations`).
- Planner (Stage E) API is being built in a parallel thread; the UI must say plainly when a server does not offer it yet.
- Terminology to keep: verdict, gate, predeclaration, candidate, exploratory, null field, plan, budget. Translate the rest (artifact ids, task kinds) into plain words or move them to the details view.
- Inferred, not confirmed: candidate triage (park, close, reopen, note) as UI buttons. Pending Leigh's decision in the design thread.

## Evidence on Hand

- `src/lib/testing/fixtures.json`: real `sdp-api` answers over the in-memory kernel (synthetic-linear protocol experiments, one synthetic Investigate set); `src/lib/testing/jwst.json`: the synthetic JWST field through the planner, with its image. Demonstration data must stay labelled synthetic.
- No real Relativity or Stellar run outputs are in the fixtures; historical 1919 measurements must never be invented.

## Product Principles

1. Plain words first, exact detail one click away. Never remove the detail; move it.
2. Say what the evidence supports, no more. Exploratory stays exploratory; null rates sit beside results.
3. One obvious next step on every screen.
4. Nothing irreversible or expensive without a clear, specific confirmation that names the cost.
5. When something cannot be done here, say why and give the exact command.

## Accessibility & Inclusion

WCAG 2.2 AA. Works at phone width (the e2e suite runs desktop and Pixel 7). Status is never carried by colour alone.
