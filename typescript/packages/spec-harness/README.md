# @sdp/spec-harness

The TypeScript specification harness (ADR-002, §3): a separate hexagon that reads the kernel's output as files and checks it independently. Its only workspace dependency is `@sdp/contracts` (enforced by dependency-cruiser).

```
pnpm harness ears      [--root requirements] [--out dir]           compile .ears; fail on grammar problems
pnpm harness trace     [--junit dir...] [--tiers T0,T1,...] [--out dir]
                                                                   traceability.json/.md; fail on TST-001 violations
pnpm harness conform   [manifest.json | dir ...]                   CTR-004: current and previous schema major
pnpm harness rederive  [manifest.json | dir ...] [--out file]      TST-007: recompute checks and verdict; fail on disagreement
pnpm harness guard     [--base origin/main] [--head HEAD]          TST-002: behaviour changes need an .ears change
pnpm harness ratchet   --scores file [--high-water file] [--update]
                                                                   TST-004: mutation score >= high-water - 2
pnpm harness report    --version v --claim 1,2 [--external T9=passed] [--mutation file] [--evidence citation]
                                                                   TST-006: release report; refuses unearned levels
```

## Ports

`RequirementSource`, `TestReportSource`, `ManifestSource`, `PredeclarationSource`, `LedgerSource` and `GateEvaluator` (`src/ports.ts`). Filesystem adapters live in `src/adapters/fs.ts`; in-memory fakes in `src/testing/fakes.ts`. No test needs the Python side running.

## Re-derivation

`rederive` takes the frozen predeclaration recorded in the manifest, checks its hash against `intent.predeclaration_hash`, evaluates every gate against the recorded findings and oracle records with its own `GateEvaluator`, and compares each check and the verdict with the kernel's. Schema 1.0 manifests carry no oracle records, so any gate that uses an oracle re-derives as `oracle_unverified` and the manifest is reported as a disagreement: the harness never assumes an oracle was verified.

Reproducibility levels map to tiers as follows: Level 1 needs T9, Level 2 needs T7 and T8, Level 3 needs T8, and Level 4 is granted only with cited independent evidence.
