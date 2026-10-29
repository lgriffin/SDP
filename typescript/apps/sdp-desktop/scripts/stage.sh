#!/usr/bin/env bash
# Fills resources/sdp with everything the desktop app runs: the evidence UI build, a portable Python
# with the SDP packages, the PostgreSQL 16 binaries, the repository data sdp-api reads, and the
# SDP Command Prompt scripts. electron-builder copies the folder into the installer as-is.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(cd "$here/../../.." && pwd)"
out="$here/resources/sdp"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) platform=windows-x64 ;;
  Linux) platform=linux-x64 ;;
  *) echo "stage.sh: unsupported build OS $(uname -s)" >&2; exit 1 ;;
esac

mkdir -p "$out"
rm -rf "$out/ui" "$out/postgres" "$out/repo" "$out/bin"

echo "== evidence UI"
(cd "$repo" && pnpm --filter @sdp/evidence-ui build)
mkdir -p "$out/ui"
cp -R "$repo/typescript/packages/evidence-ui/build" "$out/ui/build"
# adapter-node output is ESM; this package.json makes Node load it as such.
printf '{ "type": "module" }\n' > "$out/ui/package.json"

echo "== PostgreSQL 16 ($platform)"
native="$here/node_modules/@embedded-postgres/$platform/native"
[[ -d "$native/bin" ]] || { echo "missing $native: run pnpm install" >&2; exit 1; }
mkdir -p "$out/postgres"
cp -RL "$native/bin" "$native/lib" "$native/share" "$out/postgres/"

echo "== repository data"
mkdir -p "$out/repo"
cp -R "$repo/contracts" "$repo/experiments" "$out/repo/"

echo "== SDP Command Prompt"
cp -R "$here/shell" "$out/bin"

if [[ "${SKIP_PYTHON:-0}" != 1 || ! -d "$out/python" ]]; then
  echo "== Python and the SDP packages"
  "$here/scripts/stage-python.sh"
fi
du -sh "$out"/* || true
