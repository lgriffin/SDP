# C4 views

The architecture in the [C4 model](https://c4model.com/), drawn in Mermaid so GitHub renders it inline, in the notation ESI.ts uses (people dark blue, the system and its containers blue, external systems grey).

- [Level 1: Context](context.md): the people, CI, and the eight external systems SDP reaches through adapters.
- [Level 2: Containers](containers.md): the Python and TypeScript workspaces, the stores, and who may depend on whom.
- Level 3: Components
  - [Kernel](components.md): the hexagon, its driving and driven ports, the science SPI and the fakes.
  - [Adapters](components-adapters.md): the archive `Transport` seam and the real adapters behind each port.
  - [Harness](components-harness.md): the independent reader of requirements, tests, manifests and scores.
- [Experiment lifecycle](lifecycle.md): eight states and the ledger guard before Running.
- [The test system](../testing.md#the-test-system-in-c4): the same three levels for the test suite itself.
