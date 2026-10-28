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

# Stages 1-2 of §11.7
test: contracts ears
