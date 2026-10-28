# ADR-005: Requirements as .ears data with a CI coverage gate

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

Markdown tables are not checkable.

## Decision outcome

`.ears` files, compiled by the harness into tables, matrix and marker lists; CI fails on untested approved requirements and on behaviour changes without an `.ears` change. The harness stays purpose-built in CSH-compatible conventions with no dependency on CSH (decision 2, §19).

## Considered alternatives

- **Gherkin only.** Rejected: scenario-shaped, not requirement-shaped.
- **Issue tracker.** Rejected: not versioned with code.

## Consequences

- Small friction on every PR.
- A living requirements baseline.
