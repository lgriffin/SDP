# Requirements as data

Requirements live here as `.ears` files, one per capability (ADR-005, §12). The harness (`spec-harness ears`) compiles them into the tables in `docs/requirements.md` and, together with the test markers in JUnit reports from both toolchains, into the traceability matrix (§13).

## Format

```yaml
capability: Predeclaration freezing
owner: kernel
requirements:
  - id: KRN-002
    pattern: event            # ubiquitous | event | state | unwanted | optional | complex
    text: "When a predeclaration is frozen, the kernel shall ..."
    rationale: "..."
    verification: test        # test | analysis | inspection | demonstration
    tiers: [T4]               # where the evidence comes from: T0..T11, harness, lint; a -dgx suffix runs only on the DGX runner
    gates: [REL-G-01]         # optional: predeclared gates that also verify it
    status: approved          # draft | approved | retired
    traces: []                # optional hand-written seeds; the generated matrix is authoritative
```

The EARS keywords (While, When, Where, If/then, shall) are kept exactly so the text stays machine-checkable: the compiler rejects an `event` requirement that does not start with "When", an `unwanted` one without "If ... then", and so on.

## Status lifecycle

Every requirement starts as `draft`. The pull request that lands the behaviour and its tests flips the requirement to `approved`; from then on the coverage gate (TST-001) fails the build if no passing test carries its marker. Retired requirements keep their rows with `status: retired` so history survives.

## Test markers

- Python: `@pytest.mark.req("KRN-002")`
- TypeScript: `it(req("CTR-003", "accepts every valid fixture"), ...)`, which prefixes the test name with `[CTR-003]`.
