# Run SDP in containers

**Time:** 20 to 40 minutes the first time, mostly building images · **Needs:** Podman 4 or newer with
`podman compose` (or Docker with `docker compose`), Git, OpenSSL, and about 10 GB free

Containers package each piece of SDP with everything it needs, so nothing is installed on your computer
itself. This route starts the **developer stack**: PostgreSQL, SDP's API, the app in your browser, and
the Prometheus and Grafana dashboards. Everything listens on your own computer only (`127.0.0.1`).

If "container" is a new word, take the [desktop app](install-desktop.md) or the
[developer tools](install-dev-tools.md) instead: they need less setting up.

The commands below are for a Linux or macOS terminal, or WSL on Windows. With Docker, type `docker`
wherever it says `podman`.

## 1. Get the code

```sh
git clone https://github.com/lgriffin/SDP.git
cd SDP
```

## 2. Make the passwords (once)

The stack reads its passwords from files that are never committed:

```sh
mkdir -p infrastructure/compose/secrets
openssl rand -hex 16 > infrastructure/compose/secrets/postgres_password
printf 'postgresql://sdp:%s@postgres:5432/sdp' "$(cat infrastructure/compose/secrets/postgres_password)" \
  > infrastructure/compose/secrets/database_url
openssl rand -hex 16 > infrastructure/compose/secrets/grafana_admin_password
```

## 3. Build and start

```sh
podman compose -f infrastructure/compose/compose.yaml up --build -d
```

The first build takes 10 to 30 minutes. Later starts take seconds. The stack creates the database
tables by itself (the `migrate` service) before the API starts.

**Success:**

- `podman compose -f infrastructure/compose/compose.yaml ps` shows `postgres`, `api`, `ui`,
  `prometheus`, `grafana` and `node-exporter` running, and `migrate` exited with code 0.
- <http://127.0.0.1:3000> opens SDP's home page.
- <http://127.0.0.1:8000/health> answers with a short JSON status.
- <http://127.0.0.1:3001> opens Grafana (user `admin`, the password in
  `infrastructure/compose/secrets/grafana_admin_password`).

## 4. Typing `sdp` commands

The kernel image's command is `sdp`, so any `sdp` command runs as a one-off container beside the stack.
Make a short alias for this terminal:

```sh
alias sdp='podman compose -f infrastructure/compose/compose.yaml run --rm -v "$PWD/profiles:/opt/sdp/profiles:ro,Z" migrate'
```

The `-v` part lends the container the repository's test profiles, which the kernel image does not
contain. (On Docker, drop `,Z`.) Then, once:

```sh
sdp integration register
```

and from then on, for example:

```sh
sdp investigate --region "icrs;circle(56.75,24.1167,0.2)" --profile /opt/sdp/profiles/first-look.yaml --label "Pleiades warm-up"
```

Profiles are given by their path **inside** the container, `/opt/sdp/profiles/…`. Everything SDP keeps
lives in two named volumes, `sdp_postgres` and `sdp_sdp-data`, which survive restarts.

## Stopping and starting

```sh
podman compose -f infrastructure/compose/compose.yaml stop     # stop, keep everything
podman compose -f infrastructure/compose/compose.yaml up -d    # start again
podman compose -f infrastructure/compose/compose.yaml down     # remove the containers; the volumes stay
```

`just stack up -d` and `just stack stop` do the same, if you have `just`.

## Differences from the other routes

- Runs are recorded unsigned (`SDP_ALLOW_UNSIGNED=1`), as in the desktop app.
- The free-space floor is the server default of 50 GB (`SDP_MIN_FREE_BYTES`). On a laptop, add
  `SDP_MIN_FREE_BYTES: "2147483648"` under `x-kernel.environment` in
  `infrastructure/compose/compose.yaml`, or acquisition will be refused.
- Your own frames (`sdp local`) need their folder mounted into the container too; the
  [developer tools](install-dev-tools.md) are simpler for that.

## On a server

The same images run as rootless systemd services (Quadlet) on the DGX Spark, with real secrets and a
nightly database backup. That is a different setup, described in
[infrastructure/README.md](../../infrastructure/README.md). Each release attaches
`quadlet-pinned.tar.gz`, the service files with every image pinned to its exact digest.

## If it goes wrong

- **`secret … not found` or `no such file`.** Step 2 was skipped or run from another folder. Run it
  from the repository's top folder.
- **The home page says the API cannot be reached.** `podman compose -f infrastructure/compose/compose.yaml logs api`
  shows why. Most often the `migrate` service failed first; its log says why.
- **`permission denied` on the profiles folder.** On SELinux systems (Fedora, RHEL) keep the `,Z` in
  the alias.
