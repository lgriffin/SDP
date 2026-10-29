# Level 3: Harness components

`spec-harness` is the independent reader. It never imports the Python side and never trusts the kernel's verdict: it compiles the requirements, joins them to test results, re-derives every verdict and acquisition plan from the manifests, and refuses a release report that claims more than the run proved. It is hexagonal too: the commands are pure functions over ports, and `adapters/fs.ts` is the only file that touches the disk.

```mermaid
flowchart LR
    subgraph inputs ["Read from the repository and the NAS"]
        direction TB
        ears[(".ears files")]
        features[("Gherkin features")]
        junit[("JUnit reports<br/>pytest, vitest, pytest-bdd")]
        manifests[("Evidence manifests,<br/>acquisition plans")]
        ledger[("Signed ledger")]
        scores[("Mutation scores<br/>high-water.json")]
        diff[("git diff base..head")]
    end

    subgraph harness ["spec-harness"]
        direction TB
        fs["adapters/fs.ts<br/>the only I/O"]
        cEars["ears.ts<br/>grammar + strict EARS form (TST-008)"]
        cBdd["bdd.ts<br/>Rules bound to requirements (TST-010)"]
        cTrace["trace.ts · junit.ts<br/>requirement coverage (TST-001)"]
        cGuard["guard.ts<br/>behaviour carries an .ears change (TST-002)"]
        cConform["conform.ts · admit.ts<br/>schema and adapter admission"]
        cRederive["rederive.ts · rederive-plan.ts<br/>independent verdicts (TST-007)"]
        cRatchet["ratchet.ts<br/>mutation high-water (TST-004)"]
        cReport["report.ts<br/>release report (TST-006)"]
        cli["cli.ts"]
    end

    ci(["CI · ci-ok"])

    inputs --> fs
    fs --> cEars
    fs --> cBdd
    fs --> cTrace
    fs --> cGuard
    fs --> cConform
    fs --> cRederive
    fs --> cRatchet
    cEars --> cBdd
    cEars --> cTrace
    cTrace --> cReport
    cRederive --> cReport
    cRatchet --> cReport
    cli --> cEars
    cli --> cBdd
    cli --> cTrace
    cli --> cGuard
    cli --> cConform
    cli --> cRederive
    cli --> cRatchet
    cli --> cReport
    cli -- "exit code" --> ci

    style harness fill:none,stroke:#1168bd,stroke-width:2px
    style fs fill:#438dd5,color:#fff,stroke:#3c7fc0
    style cli fill:#1168bd,color:#fff,stroke:#0e5aa7
    style ci fill:#08427b,color:#fff,stroke:#073b6f
```

Every command has a test under `typescript/packages/spec-harness/test` that feeds it a bad input and expects a failure: that negative case is what makes the gate real (see [the stance](../testing.md#the-stance)).
