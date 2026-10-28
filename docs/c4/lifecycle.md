# Experiment lifecycle

No experiment runs without a frozen predeclaration. A resumed experiment re-enters Running from its last checkpoint; Refused and Failed are terminal but retained with their manifests, and only a Validated experiment with a signed approval reaches Published.

```mermaid
stateDiagram-v2
  [*] --> Defined: DefineExperiment (manifest open)
  Defined --> Running: frozen hash in ledger
  Defined --> Refused: unfrozen predeclaration
  Running --> Running: checkpoint / resume
  Running --> Completed: findings
  Running --> Failed: task error (kept with logs)
  Completed --> Validated: validate() issues verdict
  Validated --> Approved: reviewer signs (ledger)
  Approved --> Published: RO-Crate bundle + DOI
  Refused --> [*]
  Failed --> [*]
  Published --> [*]
```
