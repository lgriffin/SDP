# sdp-adapters

Driven adapters for the kernel's ports (§6). Each one passes the same contract suite as its fake (`python/tests/integration/test_real_adapters.py`, TST-003).

| Port | Adapter | Notes |
|---|---|---|
| `UnitOfWork` and repositories, `EventLog`, `EvidenceGraph` | `postgres.PostgresDatabase` | One connection and transaction per unit of work; events are append-only (trigger) and announced on `NOTIFY sdp_events` |
| schema | `postgres.migrate` | `NNNN_name.up.sql` / `.down.sql`, tested one step forward and back (NFR-007) |
| `ArtifactStore` | `storage.nas.NasArtifactStore` | `objects/sha256/ab/…`, atomic and read-only writes, SHA-256 verified on every read (NFR-003) |
| `Ledger` | `ledger.git.GitSignedLedger` | `<kind>s.jsonl`, one hash chain, Ed25519 signatures from an OpenSSH key, checked against `allowed_signers`; optional commit per entry |
| `ComputeExecutor` | `compute.cpu.CpuExecutor` | The kernel's reference loop with BLAS/OpenMP threads pinned |
| `Clock`, `IdGenerator`, `Hasher`, `EnvironmentProbe` | `system` | ULIDs; Git revision, `uv.lock` digest, image digest and CUDA versions |
| `HistoricalSourceGateway` | `historical.filesystem.FilesystemHistoricalSources` | `<doc>/document.json`, page scans, append-only `transcriptions.jsonl` and `verifications.jsonl`; a correction is a non-matching verification, never an edit (REL-001) |
| `ExperimentTracker` | `telemetry.prometheus.PrometheusTextfileTracker` | node_exporter textfiles for Grafana, plus `sdp_gpu.prom` from `nvidia-smi` (NFR-010) |

`logs.configure_logging` emits one JSON object per line with every `extra` the kernel attaches (NFR-005). `bootstrap.build_application(Settings.from_env())` is the composition root the CLI and API share; it is the only module that imports more than one adapter (import-linter enforces it).

## Settings

| Variable | Meaning |
|---|---|
| `SDP_DATABASE_URL` | PostgreSQL DSN (required) |
| `SDP_ARTIFACT_ROOT` | NAS directory for artifacts (required) |
| `SDP_REPO_ROOT` | Repository checkout; default `.` |
| `SDP_LEDGER_DIR` | Default `$SDP_REPO_ROOT/ledger` |
| `SDP_SIGNING_KEY`, `SDP_ALLOWED_SIGNERS` | OpenSSH Ed25519 private key; `allowed_signers` file (default `ledger/allowed_signers`) |
| `SDP_LEDGER_COMMIT` | `1` to commit every ledger append |
| `SDP_ALLOW_UNSIGNED` | `1` only for local experiments; signatures are required by default |
| `SDP_METRICS_DIR` | node_exporter textfile directory |
| `SDP_THREADS`, `SDP_MIN_FREE_BYTES`, `SDP_LOG_LEVEL` | Executor threads, NAS headroom, log level |

Credentials come only from the environment, never from Git (NFR-009).
