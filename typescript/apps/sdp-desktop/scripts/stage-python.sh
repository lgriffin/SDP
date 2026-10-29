#!/usr/bin/env bash
# Stages a portable CPython 3.12 with the locked SDP packages (sdp, sdp-api and the science modules)
# into resources/python, so the desktop app needs no Python on the machine. Runs on the build OS:
# Windows (Git Bash, in CI) for the installer, Linux for the smoke test.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(cd "$here/../../.." && pwd)"
out="$here/resources/sdp/python"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

rm -rf "$out" && mkdir -p "$(dirname "$out")"
uv python install 3.12 --install-dir "$work/pythons" --no-bin
installed="$(find "$work/pythons" -mindepth 1 -maxdepth 1 -type d -name 'cpython-3.12.*' ! -name 'cpython-3.12-*' | head -n1)"
mv "$installed" "$out"
if [[ -x "$out/python.exe" ]]; then python="$out/python.exe"; else python="$out/bin/python3"; fi
# python-build-standalone marks itself externally managed; this copy belongs to the app alone.
find "$out" -name EXTERNALLY-MANAGED -delete

cd "$repo/python"
uv export --frozen --no-dev --all-packages --no-editable --no-emit-workspace --no-hashes -o "$work/requirements.txt" >/dev/null
UV_PYTHON_DOWNLOADS=never uv pip install --python "$python" --no-deps --compile-bytecode -r "$work/requirements.txt"
UV_PYTHON_DOWNLOADS=never uv pip install --python "$python" --no-deps --compile-bytecode packages/*/

# Headers and the bundled pip are not needed at run time (package test folders stay: astropy imports its own).
rm -rf "$out/include" "$out/Lib/ensurepip" "$out/lib/python3.12/ensurepip"

"$python" -c "import sdp.api.serve, sdp.cli.app, sdp.adapters.bootstrap; print('staged', __import__('sys').version)"
