#!/usr/bin/env bash
# Write the Quadlet units with every image pinned by digest (§15 supply chain).
#
#   infrastructure/pin-images.sh OUT_DIR [IMAGE=DIGEST ...]
#
# The units in git name tags; a release resolves each tag to the digest it points at today with
# `skopeo inspect` (or takes it from IMAGE=DIGEST for images the release itself just pushed) and
# writes the pinned units to OUT_DIR, which the release attaches. An image whose tag is a
# placeholder (SET-FROM-PHASE0-SPIKE) is left unpinned and reported, never guessed.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${1:?usage: $0 OUT_DIR [IMAGE=DIGEST ...]}"
shift
declare -A known=()
for pair in "$@"; do known["${pair%%=*}"]="${pair#*=}"; done

mkdir -p "$out"
unpinned=0
for unit in "$repo"/infrastructure/quadlet/*; do
  name="$(basename "$unit")"
  if ! grep -q '^Image=' "$unit"; then cp "$unit" "$out/$name"; continue; fi
  image="$(sed -n 's/^Image=//p' "$unit")"
  repo_part="${image%:*}"
  if [[ "$image" == *SET-FROM-PHASE0-SPIKE* ]]; then
    echo "unpinned: $name ($image) waits for the Phase 0 spike" >&2
    cp "$unit" "$out/$name"
    unpinned=$((unpinned + 1))
    continue
  fi
  digest="${known[$repo_part]:-}"
  [ -n "$digest" ] || digest="$(skopeo inspect --format '{{.Digest}}' "docker://$image")"
  sed "s|^Image=.*|# ${image}\nImage=${repo_part}@${digest}|" "$unit" > "$out/$name"
  echo "$name: $repo_part@$digest"
done
echo "$unpinned unit(s) left unpinned"
