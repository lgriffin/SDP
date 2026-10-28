#!/usr/bin/env bash
# Regenerate the Pydantic models from contracts/schemas (CTR-001). CI reruns this and fails on any diff (CTR-002).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
out="${SDP_PYDANTIC_OUT:-$root/python/packages/sdp-contracts/src/sdp/contracts}"
cd "$root/python"
# Generate outside the repository so no project ruff config shapes the output (CTR-002).
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
uv run --quiet datamodel-codegen \
  --input "$root/contracts/schemas" \
  --input-file-type jsonschema \
  --output "$work/v1" \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 \
  --use-standard-collections \
  --use-union-operator \
  --use-annotated \
  --field-constraints \
  --use-schema-description \
  --use-double-quotes \
  --capitalise-enum-members \
  --disable-timestamp \
  --custom-file-header "# Generated from contracts/schemas by contracts/tools/generate_python.sh. Do not edit." \
  --formatters ruff-format
# Format in isolation so the output does not depend on where it was written (CTR-002).
uv run --quiet ruff format --isolated --line-length 120 --quiet "$work/v1"
rm -rf "$out/v1" "$out/schemas"
mkdir -p "$out/schemas"
cp -R "$work/v1" "$out/v1"
cp "$root"/contracts/schemas/*.schema.json "$out/schemas/"
