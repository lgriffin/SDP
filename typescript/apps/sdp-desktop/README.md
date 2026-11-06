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

Test profiles: the defaults in the repository's `profiles/` folder are copied into `%LOCALAPPDATA%\SDP\profiles` on each start. A default the person has not edited is updated by each release; one they edited, or a file of their own with the same name, is left alone (`.shipped.json` in that folder records what was last shipped). The Start page lists that folder (`SDP_PROFILES_DIR`), and `profiles/README.md` travels with them as a guide.

The person's data stays in `%LOCALAPPDATA%\SDP` (`postgres`, `artifacts`, `ledger`, `logs`, `secrets`), and uninstalling leaves it in place. If start-up fails, the app shows the error and offers to open the `logs` folder.

## Differences from the server deployment

- `SDP_ALLOW_UNSIGNED=1`, as in the developer compose stack. Runs from the UI append unsigned ledger entries. Signed steps use the person's key through the Command Prompt (`SDP_SIGNING_KEY`, `SDP_SIGNER`).
- `SDP_MIN_FREE_BYTES` is 2 GiB, not 50 GiB, so that a laptop can still acquire data.
- There are no metrics (Prometheus or Grafana) and no Zenodo token unless the person sets one in the Command Prompt.

## Running from source (Windows)

To run the app from a checkout without building the installer. Use **PowerShell** in the checkout's top folder
(for example `D:\code\SDP`). You need Git for Windows (it brings Git Bash), Node.js 22 with pnpm, and uv.

```powershell
pnpm install
pnpm --filter @sdp/desktop compile
pnpm --filter @sdp/desktop stage      # UI build, Python + SDP packages, PostgreSQL, contracts/experiments (10 to 20 minutes)
pnpm --filter @sdp/desktop start      # opens the app window; data goes to %LOCALAPPDATA%\SDP
```

`pnpm stage` runs `scripts/stage.sh` with **Git Bash** (it looks next to `git.exe`, then in `Program Files\Git`;
set `SDP_BASH` to the full path of `bash.exe` if yours is elsewhere). Do not type `bash scripts/stage.sh` in
PowerShell: there, `bash` is usually WSL's, which fails with `node: not found` or would stage the Linux build.
`stage.sh` now stops with this advice if it finds itself under WSL. If you want to call it directly, name Git
Bash in full:

```powershell
& "C:\Program Files\Git\bin\bash.exe" typescript/apps/sdp-desktop/scripts/stage.sh
```

After a change to the Python packages or the UI, run `stage` again (`$env:SKIP_PYTHON=1` first skips the slow
Python step when only the UI changed). After a change to `src/`, `pnpm start` compiles it again by itself.

**`set: pipefail` / `invalid option name`.** The `.sh` files were checked out with Windows line endings, which
the repository has prevented since #193. A checkout made before that keeps the old copies until they are
written again:

```powershell
git ls-files '*.sh' | ForEach-Object { Remove-Item $_; git checkout -- $_ }
```

## Building the installer

```bash
pnpm install
pnpm --filter @sdp/desktop compile
pnpm --filter @sdp/desktop stage                   # UI build, Python + SDP packages, PostgreSQL, contracts/experiments
pnpm --filter @sdp/desktop smoke                    # fresh data folder, whole start-up, home page, clean stop
cd typescript/apps/sdp-desktop && pnpm exec electron-builder --config electron-builder.yml --win nsis
```

Staging runs on the OS you are building for: Windows (Git Bash, which `pnpm stage` finds for you) for the installer, or Linux for development (`--linux dir`). The `desktop-windows` CI job does all of this on `windows-latest` and uploads `SDP-Setup-<version>.exe`. A release attaches it.

Code signing: set the repository secrets `WINDOWS_SIGNING_CERT` (a base64 .pfx) and `WINDOWS_SIGNING_PASSWORD`. Without them the installer is unsigned, and Windows SmartScreen warns on first install ("More info", then "Run anyway").

PostgreSQL will not run as root on Linux, so run the smoke test as an ordinary user. On Windows, `pg_ctl` drops administrator rights by itself.
