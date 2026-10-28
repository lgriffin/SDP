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
    cd python && uv run ruff check . && uv run ruff format --check . && uv run mypy packages/sdp-kernel/src packages/sdp-contracts/src tests && uv run lint-imports

# Python tiers T0-T4 (no live archives, no GPU)
pytest *args:
    cd python && uv run pytest -q -m "not integration and not live and not gpu" --junitxml=../reports/junit-python.xml {{args}}

# Regenerate Pydantic and Zod bindings from contracts/schemas
codegen:
    contracts/tools/generate.sh

# Fail if committed generated code differs from a fresh generation (CTR-002)
codegen-check:
    contracts/tools/generate.sh --check

# TypeScript typecheck and tests
ts:
    pnpm lint && pnpm -r typecheck && CI=true pnpm -r test

# Stages 1-2 of §11.7
test: contracts ears codegen-check lint pytest ts cross-language trace
