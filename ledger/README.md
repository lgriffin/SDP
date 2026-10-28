# Ledger

Signed, append-only, hash-chained entries (ADR-004, §15). One JSON object per line in `ledger/<kind>.jsonl`, each conforming to `contracts/schemas/ledger-entry.schema.json`:

- `predeclarations.jsonl`: one entry per frozen predeclaration, written by `sdp predeclaration freeze`.
- `approvals.jsonl`: one entry per reviewer approval of a verdict, written by `sdp approve`.

Each entry carries the hash of the previous entry, so any edit or deletion breaks the chain and is detected by `sdp ledger verify` and by the harness. On the DGX Spark every ledger commit must be signed (`GitSignedLedger(require_signatures=True)`); branch protection requires review for changes under this directory.

Signatures are Ed25519 over the entry's canonical JSON (everything but the signature), made with the signer's OpenSSH key, the same key used for `git commit -S` with `gpg.format=ssh`. `ledger/allowed_signers` maps principals to public keys in the format `ssh-keygen -Y verify` reads.
