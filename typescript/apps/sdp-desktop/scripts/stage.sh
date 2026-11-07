#!/usr/bin/env bash
# Fills resources/sdp with everything the desktop app runs: the evidence UI build, a portable Python
# with the SDP packages, the PostgreSQL 16 binaries, the repository data sdp-api reads, and the
# SDP Command Prompt scripts. electron-builder copies the folder into the installer as-is.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(cd "$here/../../.." && pwd)"
out="$here/resources/sdp"
# WSL reports Linux, so from a Windows checkout it would stage Linux Postgres and Python without a
# word (#194). Stop and name the bash to use instead.
if [[ -n "${WSL_DISTRO_NAME:-}" || -n "${WSL_INTEROP:-}" ]] || grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
  if [[ "${SDP_STAGE_ALLOW_WSL:-0}" != 1 ]]; then
    cat >&2 <<'MSG'
stage.sh: this is WSL's bash, which would stage the Linux build. For the Windows app use Git Bash:
  pnpm --filter @sdp/desktop stage
or
  & "C:\Program Files\Git\bin\bash.exe" typescript/apps/sdp-desktop/scripts/stage.sh
(To stage a Linux build inside WSL on purpose, set SDP_STAGE_ALLOW_WSL=1.)
MSG
    exit 1
  fi
fi
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
# The provenance collector records the digest of python/uv.lock, the lock this Python was built from.
mkdir -p "$out/repo/python"
cp "$repo/python/uv.lock" "$out/repo/python/uv.lock"

echo "== default test profiles"
rm -rf "$out/profiles"
cp -R "$repo/profiles" "$out/profiles"

echo "== SDP Command Prompt"
cp -R "$here/shell" "$out/bin"

if [[ "${SKIP_PYTHON:-0}" != 1 || ! -d "$out/python" ]]; then
  echo "== Python and the SDP packages"
  "$here/scripts/stage-python.sh"
fi
du -sh "$out"/* || true
