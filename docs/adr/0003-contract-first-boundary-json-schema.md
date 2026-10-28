# ADR-003: Contract-first boundary with JSON Schema and generated types

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

Two languages must agree on manifests, reports and ledger entries.

## Decision outcome

Schemas in `contracts/` are the single source; Pydantic and Zod are generated and committed; golden fixtures are tested in both languages.

## Considered alternatives

- **Protobuf.** Rejected: poorer fit for JSON-LD provenance and RO-Crate.
- **Hand-written types on both sides.** Rejected: drift.

## Consequences

- Schema changes are visible diffs.
- Breaking changes need an ADR and a migration note in `contracts/CHANGELOG.md`.
