# ADR-011: Parquet curated, PostgreSQL metadata, DuckDB analytics

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

Carried from 0.2.0 ADR-003 to ADR-005, merged.

## Decision outcome

Curated data are Parquet on the NAS, metadata live in PostgreSQL 16, analytical queries run in-process on DuckDB; FITS originals are retained in raw.

## Considered alternatives

- **A data warehouse or cluster.** Rejected: single host.

## Consequences

- One relational store, one columnar format, no cluster.
