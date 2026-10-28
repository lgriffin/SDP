#!/usr/bin/env bash
# Clean-room reproduction (T9, NFR-004; §11.6 Level 1).
#
#   infrastructure/reproduce.sh BUNDLE INPUTS [OUT]
#
# BUNDLE is a released RO-Crate bundle (.zip) or a manifest.json; INPUTS is a directory holding the
# experiment's input artifacts (found by SHA-256, so names do not matter); OUT defaults to
# reports/reproduction. The script builds infrastructure/containers/reproduce.Containerfile from this
# checkout's pinned lock files, then runs the experiment again inside it with no network, the bundle
# and inputs mounted read-only. Inside, `sdp reproduce` re-runs the recorded protocol with the
# recorded parameters and seed and compares verdict, gates and findings; the harness then checks the
# reproduced manifest conforms and re-derives its verdict independently.
#
# Options: --no-container runs the same steps in the current environment (a developer's quick check;
# it is not a clean room). CONTAINER_ENGINE picks podman (default when installed) or docker.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

inside() {
  local bundle="$1" inputs="$2" out="$3"
  cd "$repo"
  echo "== sdp reproduce"
  (cd python && uv run --frozen sdp reproduce "$bundle" --inputs "$inputs" --out "$out")
  echo "== harness: the reproduced manifest conforms and its verdict re-derives"
  node typescript/packages/spec-harness/src/cli.ts conform "$out/experiments"
  node typescript/packages/spec-harness/src/cli.ts rederive "$out/experiments" --out "$out/rederive.json"
  echo "reproduced: $out/reproduction.json"
}

case "${1:-}" in
  --inside)
    shift
    inside "$@"
    exit 0
    ;;
  --no-container)
    shift
    [ $# -ge 2 ] || { echo "usage: $0 --no-container BUNDLE INPUTS [OUT]" >&2; exit 64; }
    out="$(realpath -m "${3:-reports/reproduction}")"
    inside "$(realpath "$1")" "$(realpath "$2")" "$out"
    exit 0
    ;;
  ""|-h|--help)
    sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
    exit 64
    ;;
esac

[ $# -ge 2 ] || { echo "usage: $0 BUNDLE INPUTS [OUT]" >&2; exit 64; }
bundle="$(realpath "$1")"
inputs="$(realpath "$2")"
out="$(realpath -m "${3:-reports/reproduction}")"
engine="${CONTAINER_ENGINE:-$(command -v podman >/dev/null && echo podman || echo docker)}"
image="sdp-reproduce:$(git -C "$repo" rev-parse --short HEAD 2>/dev/null || echo local)"

mkdir -p "$out"
# Docker runs the container user without a user-namespace mapping; let it write the report.
[ "$engine" = docker ] && chmod a+rwx "$out"
"$engine" build --pull -f "$repo/infrastructure/containers/reproduce.Containerfile" -t "$image" "$repo"
userns=()
[ "$engine" = podman ] && userns=(--userns=keep-id:uid=10001,gid=10001)
"$engine" run --rm --network none --cap-drop ALL --security-opt no-new-privileges "${userns[@]}" \
  -v "$bundle:/in/$(basename "$bundle"):ro" -v "$inputs:/in/inputs:ro" -v "$out:/out" \
  "$image" "/in/$(basename "$bundle")" /in/inputs /out
