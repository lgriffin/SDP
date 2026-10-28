# ADR-014: Einstein examination as the first vertical slice

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

Carried from 0.2.0 §22.

## Decision outcome

Build the relativity module end to end before any Gaia science; build the data-source layer next.

## Considered alternatives

- **Stellar programme first.** Rejected: millions of rows before the kernel is proven.

## Consequences

- Kernel, contracts, harness and UI are proven on hundreds of numbers before millions.
