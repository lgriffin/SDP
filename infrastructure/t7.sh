#!/usr/bin/env bash
# T7 end to end on the full compose stack (§11): a synthetic UC-02 experiment from its input files
# through PostgreSQL, the artifact store and the signed ledger to a verdict, a dossier, an approval
# and an RO-Crate bundle; then the harness checks the manifest and re-derives the verdict, the
# bundle reproduces (T9 logic), and the evidence UI serves the experiment from the live API.
#
#   infrastructure/t7.sh [OUT]        (needs docker compose or podman compose, uv, node and pnpm)
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="$(realpath -m "${1:-$repo/reports/t7}")"
compose=(${COMPOSE:-docker compose} -f "$repo/infrastructure/compose/compose.yaml")
secrets="$repo/infrastructure/compose/secrets"

cleanup() { "${compose[@]}" logs --no-color > "$out/compose.log" 2>&1 || true; "${compose[@]}" down -v || true; }
trap cleanup EXIT

mkdir -p "$out/inputs" "$secrets"
chmod a+rwx "$out"
# Throwaway credentials for this run only, written as the secret files the stack mounts.
[ -f "$secrets/postgres_password" ] || openssl rand -hex 16 > "$secrets/postgres_password"
printf 'postgresql://sdp:%s@postgres:5432/sdp' "$(cat "$secrets/postgres_password")" > "$secrets/database_url"
[ -f "$secrets/grafana_admin_password" ] || openssl rand -hex 16 > "$secrets/grafana_admin_password"

echo "== inputs: a synthetic 1919 bundle and the independent TypeScript fit"
(cd "$repo/python" && uv run python -m sdp_relativity.synthetic "$out/inputs/bundle.json")
node "$repo/typescript/packages/relativity-fit/src/cli.ts" "$out/inputs/bundle.json" --out "$out/inputs/independent-fit.json"
chmod -R a+rX "$out/inputs"

echo "== stack up"
"${compose[@]}" up -d --build --wait api ui
sdp() { "${compose[@]}" run --rm -T -v "$out:/t7" migrate "$@"; }
field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

echo "== predeclare, define, run, validate, dossier, approve, bundle"
hash="$(sdp predeclaration freeze uc02-einstein-1919 --signer ci | field "['predeclaration_hash']")"
experiment="$(sdp experiment define uc02-einstein-1919 --predeclaration "$hash" --seed 1919 \
  --input observations=/t7/inputs/bundle.json --input independent_fit=/t7/inputs/independent-fit.json \
  --param bootstrap_resamples=200 | field "['experiment_id']")"
sdp experiment run "$experiment" >/dev/null
verdict="$(sdp experiment validate "$experiment" | field "['verdict']")"
echo "verdict: $verdict"
mkdir -p "$out/experiments/uc02-einstein-1919/$experiment"
sdp experiment show "$experiment" > "$out/experiments/uc02-einstein-1919/$experiment/manifest.json"
dossier="$(sdp dossier assemble "$experiment" --limitation "Synthetic observations; no 1919 measurement is used." | field "['dossier_id']")"
sdp approve "$experiment" --reviewer reviewer >/dev/null
sdp bundle "$dossier" --out /t7/bundle.zip >/dev/null

echo "== harness: conformance and independent re-derivation"
cd "$repo"
node typescript/packages/spec-harness/src/cli.ts conform "$out/experiments"
node typescript/packages/spec-harness/src/cli.ts rederive "$out/experiments" --out "$out/rederive.json"

echo "== the bundle reproduces"
infrastructure/reproduce.sh --no-container "$out/bundle.zip" "$out/inputs" "$out/reproduction"

echo "== the evidence UI serves the experiment from the live API"
page="$(curl -fsS "http://127.0.0.1:3000/experiments/$experiment")"
grep -q "$experiment" <<<"$page"
grep -q "$verdict" <<<"$page"
echo "T7 passed: $experiment $verdict"
