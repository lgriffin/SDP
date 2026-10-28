# ADR-021: Four-kind conformance with per-product-type admission

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: Integration Architecture v0.4.0 §11 (`docs/integration.md`)

## Context and problem statement

Contract tests prove interfaces, not meaning.

## Decision outcome

Contract, operational, semantic and scientific conformance per adapter and product type; a new T5s tier on hand-verified reference records; an `AdapterCompatibilityRecord` in every manifest that the harness checks before any verdict.

## Considered alternatives

- **Rely on upstream test suites.** Rejected: they test the package, not SDP's use of it.

## Consequences

- A reference-record registry to maintain.
- Adapters are admitted product type by product type.
