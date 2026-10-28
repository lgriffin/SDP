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

`python contracts/tools/check_fixtures.py` validates every fixture and the draft predeclarations against the schemas. Generated code (Pydantic in `python/packages/sdp-contracts`, Zod in `typescript/packages/contracts`) is committed and CI regenerates and diffs it (CTR-002).
