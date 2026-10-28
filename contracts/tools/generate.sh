#!/usr/bin/env bash
# Regenerate every language binding from contracts/schemas (CTR-001). With --check, fail on drift (CTR-002).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
"$root/contracts/tools/generate_python.sh"
(cd "$root/typescript/packages/contracts" && node scripts/generate-zod.mjs)
if [[ "${1:-}" == "--check" ]]; then
  cd "$root"
  if ! git diff --exit-code -- python/packages/sdp-contracts/src typescript/packages/contracts/src/generated; then
    echo "Generated contract code is stale: run contracts/tools/generate.sh and commit the result." >&2
    exit 1
  fi
  untracked="$(git ls-files --others --exclude-standard -- python/packages/sdp-contracts/src typescript/packages/contracts/src/generated)"
  if [[ -n "$untracked" ]]; then
    echo "Generated files are not committed:" >&2
    echo "$untracked" >&2
    exit 1
  fi
fi
