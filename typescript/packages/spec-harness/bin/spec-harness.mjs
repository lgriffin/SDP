#!/usr/bin/env node
// Node 22 strips TypeScript types natively; the harness runs from source.
await import("../src/cli.ts");
