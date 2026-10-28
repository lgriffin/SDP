# ADR-008: Immutable content-addressed artifacts

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

Carried from 0.2.0 ADR-007.

## Decision outcome

`ArtifactId` is SHA-256 of canonical bytes; the artifact store is write-once; raw is never modified.

## Considered alternatives

- **Mutable paths with version suffixes.** Rejected: no integrity guarantee.

## Consequences

- Deduplication for free.
- Curation always derives a new artifact.
