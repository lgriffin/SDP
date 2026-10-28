# Infrastructure

How SDP is built, deployed and reproduced (§15, §16). Everything here runs rootless and
unprivileged. Credentials arrive only as Podman secrets or secret files (NFR-009), and
`python/tests/architecture/test_deployment.py` checks both on every CI run.

| Path | What it is |
|---|---|
| `containers/kernel.Containerfile` | `sdp-kernel`: kernel, adapters, science modules, `sdp` and `sdp-api`, from `uv.lock`. Runs as uid 10001. |
| `containers/harness.Containerfile` | `sdp-harness`: `spec-harness`, from `pnpm-lock.yaml`. Runs as `node`. |
| `containers/ui.Containerfile` | `sdp-ui`: the evidence UI (adapter-node). Runs as `node`. |
| `containers/reproduce.Containerfile` | The clean-room image for T9, with both toolchains installed from the lock files. Runs as uid 10001. |
| `quadlet/` | Rootless systemd units for the DGX Spark: PostgreSQL with a nightly `pg_dump`, `sdp-api`, `sdp-runner@<experiment>`, `sdp-harness`, `sdp-ui`, Prometheus, node_exporter (textfile metrics), the DCGM and postgres exporters, and Grafana. |
| `compose/compose.yaml` | The developer stack: PostgreSQL, migrations, `sdp-api`, the UI, node_exporter, Prometheus and Grafana. |
| `grafana/`, `prometheus/` | Datasource, dashboard (`sdp_experiment_*`, `sdp_gpu_*`, DCGM) and scrape configuration (NFR-010). |
| `reproduce.sh` | `just reproduce <bundle> <inputs>`: re-runs a released experiment in a fresh container with no network (T9, NFR-004). |
| `t7.sh` | `just t7`: a synthetic UC-02 experiment end to end on the compose stack, through the harness, a bundle reproduction and the UI. This is the nightly T7. |
| `pin-images.sh` | Writes the units with every image pinned by digest. The release workflow runs it and attaches the result (deviation D16). |

## Deploying on the DGX Spark

1. Mount the NAS at `/mnt/nas/sdp`. It needs the areas `artifacts/`, `experiments/`, `ledger/`, `metrics/`, `reports/` and `backups/`. Only `artifacts/`, `experiments/`, `metrics/`, `reports/` and `backups/` are mounted writable.
2. Create the secrets once:
   ```sh
   printf '%s' "$PG_PASSWORD" | podman secret create sdp_postgres_password -
   printf 'postgresql://sdp:%s@sdp-postgres:5432/sdp' "$PG_PASSWORD" | podman secret create sdp_database_url -
   podman secret create sdp_signing_key ~/.ssh/sdp_ledger_ed25519
   printf '%s' "$ZENODO_TOKEN" | podman secret create sdp_zenodo_token -
   printf '%s' "$GRAFANA_PASSWORD" | podman secret create sdp_grafana_admin_password -
   ```
3. Put the non-secret settings (`SDP_SIGNER`, `SDP_ALLOWED_SIGNERS`, `SDP_ZENODO_CREATORS`, `SDP_ZENODO_URL`, and so on) in `~/.config/sdp/sdp.env`. Keep a checkout at `~/sdp` for the Prometheus and Grafana configuration.
4. Unpack the release's `quadlet-pinned.tar.gz` into `~/.config/containers/systemd/`. Then run `systemctl --user daemon-reload`, start `sdp-postgres`, `sdp-api`, `sdp-ui` and the metrics units, and enable `sdp-pg-backup.timer`.
5. Run an experiment with `systemctl --user start sdp-runner@<experiment-id>`.

The DCGM exporter's image tag waits for the Phase 0 spike (`docs/spike/phase0-dgx-spark.md`). NFR-001 (the platform runs on the DGX Spark under rootless Podman) is shown by doing these steps on the host.

## Reproducing a release

```sh
just reproduce bundle.zip inputs/   # podman by default; CONTAINER_ENGINE=docker also works
```

`inputs/` holds the experiment's input artifacts. They are matched by SHA-256, so file names do not matter. The script builds the clean-room image from this checkout's lock files and runs the experiment again with no network. It then writes `reports/reproduction/reproduction.json`, the reproduced manifest, and the harness's conformance and re-derivation results. A difference in the verdict, any gate outcome, or any finding beyond the recorded tolerance fails the run.
