# ADR-019: Two-method probabilistic association above the cross-match

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: Integration Architecture v0.4.0 §11 (`docs/integration.md`)

## Context and problem statement

Nearest-neighbour matching silently asserts identity.

## Decision outcome

`AssociationService` produces competing hypotheses with posteriors; Bayes-factor cross-identification (Budavári and Szalay 2008) is the CPU reference and likelihood-ratio matching (Sutherland and Saunders 1992) the second implementation; acceptance is a reviewer's ledger entry.

## Considered alternatives

- **Radius matching only.** Rejected.
- **A single probabilistic method.** Rejected by principle 12.

## Consequences

- More stored state per field.
- A reference fixture with known truth is required before Stage D.
