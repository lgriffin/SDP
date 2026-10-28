# One entry point (§16): just test, just gate, just reproduce.

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Contract fixtures and draft predeclarations against the schemas
contracts:
    uv run --quiet --no-project --with jsonschema --with referencing python contracts/tools/check_fixtures.py

# Compile requirements/*.ears (spec-harness ears)
ears:
    pnpm --silent harness ears --out reports/requirements

# Requirement coverage gate over the JUnit reports in reports/ (TST-001)
trace *args:
    pnpm --silent harness trace --junit reports {{args}}

# Kernel manifests through the harness: conformance and independent re-derivation (T6, TST-007)
cross-language:
    rm -rf reports/emitted && cd python && uv run python -m sdp.testing.emit ../reports/emitted
    pnpm --silent harness conform reports/emitted/experiments
    pnpm --silent harness rederive reports/emitted/experiments

# Behaviour changes carry an .ears change (TST-002)
guard base="origin/main":
    pnpm --silent harness guard --base {{base}}

# Python static checks: ruff, mypy strict, import-linter
lint:
    cd python && uv run ruff check . && uv run ruff format --check . && uv run mypy packages/sdp-kernel/src packages/sdp-contracts/src packages/sdp-adapters/src packages/sdp-cli/src packages/sdp-api/src tests && uv run lint-imports

# Python tiers T0-T4 (no live archives, no GPU)
pytest *args:
    cd python && uv run pytest -q -m "not integration and not live and not gpu" --junitxml=../reports/junit-python.xml {{args}}

# Tier T5: real adapters on PostgreSQL. Needs SDP_TEST_DATABASE_URL (a server where you may create databases)
integration *args:
    cd python && SDP_REQUIRE_DATABASE=1 uv run pytest -q -m integration --junitxml=../reports/junit-python-integration.xml {{args}}

# Apply pending database migrations (SDP_DATABASE_URL)
migrate:
    cd python && uv run sdp db migrate

# Serve the GraphQL API and health endpoint from the environment's settings
api:
    cd python && uv run sdp-api

# Regenerate Pydantic, Zod and GraphQL bindings from contracts/schemas
codegen:
    contracts/tools/generate.sh

# Fail if committed generated code differs from a fresh generation (CTR-002)
codegen-check:
    contracts/tools/generate.sh --check

# TypeScript typecheck and tests
ts:
    pnpm lint && pnpm -r typecheck && CI=true pnpm -r test

# Stages 1-2 of §11.7
test: contracts ears codegen-check lint pytest integration ts cross-language trace
