# ADR-016: GraphQL for the query side, CLI and minimal REST for commands

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

The UI browses a provenance graph.

## Decision outcome

Strawberry GraphQL on the ASGI app for queries; commands stay on the CLI and a small REST surface; the committed `schema.graphql` is a contract.

## Considered alternatives

- **REST only.** Rejected: lineage queries become many round trips.
- **GraphQL mutations.** Rejected: commands belong to the CLI's signed workflow.

## Consequences

- A generated typed client in the UI.
- One more schema under drift checks.
