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

The compiler also holds every text to the strict form (TST-008): `shall` exactly once, a named system as its subject (`the kernel shall`, `the harness shall`, never a passive "Logs shall be structured"), and none of the vague or optional words listed in [`docs/testing.md`](../docs/testing.md#the-strict-form-tst-008). An exclusion is an unwanted requirement with a negated response: `If <condition>, then the <system> shall not <response>`.

## Status lifecycle

Every requirement starts as `draft`. The pull request that lands the behaviour and its tests flips the requirement to `approved`; from then on the coverage gate (TST-001) fails the build if no passing test carries its marker. Retired requirements keep their rows with `status: retired` so history survives.

## Test markers

- Python: `@pytest.mark.req("KRN-002")`
- Python gates: `@pytest.mark.gate("REL-G-01")` on the test that evaluates a predeclared gate.
- Gherkin: a Rule titled `<ID>: <the exact text>` under `python/tests/bdd/features`; each scenario under it carries the id. `spec-harness bdd` fails a Rule whose text drifts from this file (TST-010).
- TypeScript: put the id in brackets in the test title, `it("[CTR-003] accepts every valid fixture", ...)`; gates as `[gate:REL-G-01]`, tiers as `[tier:T6]`.

pytest writes markers as JUnit `<property>` elements (`junit_family = "xunit1"`); vitest writes titles. `spec-harness trace` reads both. It fails the build when an approved test-verified requirement in scope has no passing test, when a marker names an unknown requirement, or when a gate of a frozen predeclaration (its hash is in the ledger) has no passing evaluating test. Gates of draft predeclarations are reported as warnings.

`spec-harness guard` fails a pull request that adds a use case, port, adapter or gate without touching any `.ears` file (TST-002).
