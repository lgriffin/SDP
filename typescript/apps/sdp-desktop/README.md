# SDP for Windows

The evidence UI as a desktop app for people who do not run servers. One installer, no administrator
rights, no Docker, no PostgreSQL to set up. It runs the same pieces as the compose stack, without containers:

| Piece | How it runs in the app |
|---|---|
| Evidence UI | The adapter-node build, served inside Electron's main process on `127.0.0.1` and shown in the app window |
| `sdp-api` | A portable CPython 3.12 with the locked SDP packages (`uv export --frozen`), started as a child process on `127.0.0.1` |
| PostgreSQL 16 | The binaries from `@embedded-postgres/windows-x64`. The cluster is created on first start with a random password and listens on loopback only. It is started and stopped with the app. |
| Migrations | `sdp db migrate` on every start |
| `sdp` CLI | The **SDP Command Prompt** in the Start menu, connected to the running app, for the signed steps the UI shows as commands (INV-015) |

Test profiles: the defaults in the repository's `profiles/` folder are copied into `%LOCALAPPDATA%\SDP\profiles` on each start, unless a file with that name is already there, so a person's edits survive. The Start page lists that folder (`SDP_PROFILES_DIR`), and `profiles/README.md` travels with them as a guide.

The person's data stays in `%LOCALAPPDATA%\SDP` (`postgres`, `artifacts`, `ledger`, `logs`, `secrets`), and uninstalling leaves it in place. If start-up fails, the app shows the error and offers to open the `logs` folder.

## Differences from the server deployment

- `SDP_ALLOW_UNSIGNED=1`, as in the developer compose stack. Runs from the UI append unsigned ledger entries. Signed steps use the person's key through the Command Prompt (`SDP_SIGNING_KEY`, `SDP_SIGNER`).
- `SDP_MIN_FREE_BYTES` is 2 GiB, not 50 GiB, so that a laptop can still acquire data.
- There are no metrics (Prometheus or Grafana) and no Zenodo token unless the person sets one in the Command Prompt.

## Building

```bash
pnpm install
pnpm --filter @sdp/desktop compile
bash typescript/apps/sdp-desktop/scripts/stage.sh   # UI build, Python + SDP packages, PostgreSQL, contracts/experiments
pnpm --filter @sdp/desktop smoke                    # fresh data folder, whole start-up, home page, clean stop
cd typescript/apps/sdp-desktop && pnpm exec electron-builder --config electron-builder.yml --win nsis
```

Staging runs on the OS you are building for: Windows (Git Bash) for the installer, or Linux for development (`--linux dir`). The `desktop-windows` CI job does all of this on `windows-latest` and uploads `SDP-Setup-<version>.exe`. A release attaches it.

Code signing: set the repository secrets `WINDOWS_SIGNING_CERT` (a base64 .pfx) and `WINDOWS_SIGNING_PASSWORD`. Without them the installer is unsigned, and Windows SmartScreen warns on first install ("More info", then "Run anyway").

PostgreSQL will not run as root on Linux, so run the smoke test as an ordinary user. On Windows, `pg_ctl` drops administrator rights by itself.
