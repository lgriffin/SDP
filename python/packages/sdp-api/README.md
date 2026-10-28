# sdp-api

The ASGI app (ADR-016): Strawberry GraphQL at `/graphql` for queries, `POST /experiments/{id}/run` and `/validate`, and `/health` (database, NAS headroom, ledger chain). Freezing and approval need a signer and stay on the CLI.

`sdp-api --export-schema` prints the SDL committed at `contracts/graphql/schema.graphql`; `contracts/tools/generate.sh` regenerates it and the TypeScript types in `@sdp/contracts` (`graphql` export), and CI fails on drift (CTR-005).
