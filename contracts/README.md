# contracts/

The single source of truth for every type that crosses the Python/TypeScript boundary (§7, ADR-003).

```
contracts/
├── schemas/            JSON Schema 2020-12; $id carries the major version, x-version the full semver
├── graphql/            schema.graphql exported from the ASGI app and committed
├── fixtures/valid/     golden examples both languages must accept
├── fixtures/invalid/   examples both languages must reject, with the expected error path
├── fixtures/canonical/ canonical-JSON vectors: identical bytes and hashes in both languages
└── CHANGELOG.md
```

`python contracts/tools/check_fixtures.py` validates every fixture and the draft predeclarations against the schemas. ## Generated bindings

`contracts/tools/generate.sh` regenerates both languages; `--check` fails on drift (CTR-002, CI job `contracts-codegen`).

| Language | Generator | Output | Entry point |
|---|---|---|---|
| Python | `datamodel-code-generator` (pinned in `python/pyproject.toml`) | `python/packages/sdp-contracts/src/sdp/contracts/v1/` plus a byte copy of the schemas | `sdp.contracts.load(name, document)` validates against the schema, then builds the Pydantic model |
| TypeScript | `typescript/packages/contracts/scripts/generate-zod.mjs` | `typescript/packages/contracts/src/generated/` | `parse(name, document)` from `@sdp/contracts` |

The Zod generator is ours, because off-the-shelf converters drop cross-file `$ref` and `if`/`then`. It supports exactly the keywords the schemas use and fails generation on anything else, so a schema change that needs a new keyword cannot silently weaken TypeScript validation.

Pydantic cannot express `if`/`then`, so `sdp.contracts.load` runs the JSON Schema first. Both languages report errors as JSON pointers, and the fixture suites assert the same pointer in each (CTR-003).

Domain-to-DTO mappers live in `sdp.contracts.mappers` and round-trip under property tests (CTR-006).
