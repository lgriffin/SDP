# One entry point (§16): just test, just gate, just reproduce.

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Contract fixtures and draft predeclarations against the schemas
contracts:
    uv run --quiet --no-project --with jsonschema --with referencing python contracts/tools/check_fixtures.py

# Structural check of requirements/*.ears
ears:
    uv run --quiet --no-project --with pyyaml python tools/check_ears.py

# Python static checks: ruff, mypy strict, import-linter
lint:
    cd python && uv run ruff check . && uv run ruff format --check . && uv run mypy packages/sdp-kernel/src packages/sdp-contracts/src tests && uv run lint-imports

# Python tiers T0-T4 (no live archives, no GPU)
pytest *args:
    cd python && uv run pytest -q -m "not integration and not live and not gpu" {{args}}

# Regenerate Pydantic and Zod bindings from contracts/schemas
codegen:
    contracts/tools/generate.sh

# Fail if committed generated code differs from a fresh generation (CTR-002)
codegen-check:
    contracts/tools/generate.sh --check

# TypeScript typecheck and tests
ts:
    pnpm -r typecheck && pnpm -r test

# Stages 1-2 of §11.7
test: contracts ears codegen-check lint pytest ts
