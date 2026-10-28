# ADR-020: Plan, approve, execute as separate ports with budgeted approval

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: Integration Architecture v0.4.0 §11 (`docs/integration.md`)

## Context and problem statement

Large downloads were implicit in the Investigate stages.

## Decision outcome

`PlanInvestigation`, `ApprovePlan` and `ExecutePlan` are distinct; the plan is a content-addressed artifact; approval above a budget threshold is a signed ledger entry; the executor refuses anything the plan does not name.

## Considered alternatives

- **Plan inside `InvestigateField`.** Rejected: not inspectable.
- **Approval on every plan.** Rejected: friction for small runs.

## Consequences

- A new schema and a harness re-derivation check (`rederive-plan`).
- Plan accuracy becomes measurable.
