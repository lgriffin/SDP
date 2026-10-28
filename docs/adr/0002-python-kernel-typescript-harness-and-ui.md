# ADR-002: Python kernel, TypeScript specification harness and UI

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

A mix of Python and TypeScript is wanted; the astronomy ecosystem is Python; the EARS, mutation and release-harness tooling is TypeScript.

## Decision outcome

Python owns domain, science and adapters; TypeScript owns `.ears` compilation, traceability, manifest conformance, verdict re-derivation, the release report and the UI.

## Considered alternatives

- **All Python.** Rejected: no independent verification layer, two toolchains across repos.
- **TypeScript API gateway in front of Python.** Rejected: duplicated boundary with no scientific benefit.
- **TypeScript workflows.** Rejected: Prefect-class tooling is Python.

## Consequences

- Two toolchains in CI.
- A contract repository with codegen.
- A genuinely independent check on every verdict.
