# sdp-cli

The `sdp` command (ADR-016): the command side, where every change that needs a signature happens. Output is JSON.

```bash
sdp db migrate
sdp predeclaration freeze synthetic-linear --signer leigh
sdp experiment define synthetic-linear --predeclaration sha256:… --input series=series.json --seed 42
sdp experiment run exp-…
sdp experiment validate exp-…
sdp dossier assemble exp-… --limitation "…"
sdp approve exp-… --reviewer someone-else
sdp publish dos-…
sdp ledger verify
```

Settings come from the environment; see `python/packages/sdp-adapters/README.md`.
