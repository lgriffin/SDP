# ADR-004: Signed predeclaration ledger

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

Preregistration was named in 0.2.0 but had no mechanism.

## Decision outcome

`freeze()` hashes the predeclaration and writes a signed, append-only ledger entry; experiments refuse to start without it; reviewer approvals of verdicts are ledger entries too.

## Considered alternatives

- **Database flag.** Rejected: mutable, unsigned.
- **External preregistration service.** Rejected: breaks offline reproduction.

## Consequences

- Git signing is required on the DGX Spark.
- Self-approval ends when a second maintainer joins.
