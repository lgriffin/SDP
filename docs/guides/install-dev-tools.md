# Install with the developer tools

**Time:** 30 to 45 minutes the first time · **Needs:** Windows, Linux or macOS; about 5 GB free for
the tools, plus room for your data

This route runs SDP straight from a copy of the repository. Use it when you want the newest code the
moment it merges, when your data should live on another drive (such as `D:\SDP`), or when you are not
on Windows. It is how SDP is run on Leigh's PC for the data-pull issues
([#62](https://github.com/lgriffin/SDP/issues/62) onwards).

You will set up four things:

1. the tools: Git, uv (which brings Python), and optionally Node.js with pnpm;
2. a copy of the repository;
3. a PostgreSQL database;
4. one small settings file that tells SDP where everything is.

The Windows commands below assume the code lives in `D:\code\SDP` and the data in `D:\SDP`. Change
both if you like, but keep them apart: the data folder must not be inside the code folder.

## 1. The tools

| Tool | What it is for | Install |
|---|---|---|
| Git | Fetches the code and its updates | <https://git-scm.com/downloads> |
| uv | Installs the right Python and every package SDP needs, exactly as locked | Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"`; Linux and macOS: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| PostgreSQL 16 | The database SDP keeps its records in | Step 3 below |
| Node.js 22 and pnpm (optional) | The app in a browser, and the independent fitter in the [Einstein journey](einstein-1919.md) | <https://nodejs.org> (the LTS installer, version 22 or newer), then `corepack enable` |

Close and reopen your terminal after installing, so it finds the new commands. Check with:

```
git --version
uv --version
```

Each prints a version number.

## 2. The code

```bat
mkdir D:\code
cd /d D:\code
git clone https://github.com/lgriffin/SDP.git
cd SDP\python
uv sync --frozen
```

On Linux or macOS, clone wherever you like and run the same last three lines with `/` instead of `\`.

`uv sync --frozen` installs Python 3.12 or newer if you do not have it, then every package at the exact
version the repository locks. It takes a minute or two.

**Success:** `uv run sdp --help` lists the commands (`investigate`, `plan`, `archive`, `demo` and the
rest). Every `sdp` command in these guides is typed as `uv run sdp …` from the `python` folder on this
route.

## 3. The database

SDP needs one PostgreSQL 16 database. Pick whichever of these suits you.

**Windows, the simplest way.** Download the PostgreSQL 16 installer from
<https://www.postgresql.org/download/windows/> and run it. Keep the default port (5432). It asks you to
choose a password for the `postgres` user: use letters and digits only, and write it down. Leave
Stack Builder unticked at the end. Then create SDP's database:

```bat
"C:\Program Files\PostgreSQL\16\bin\createdb.exe" -h 127.0.0.1 -U postgres sdp
```

It asks for the password and then prints nothing, which means it worked. Your database address is
`postgresql://postgres:YOURPASSWORD@127.0.0.1:5432/sdp`.

**Windows, without installing a service.** `pnpm install` in the repository brings the same portable
PostgreSQL 16 the desktop app uses, under
`typescript\apps\sdp-desktop\node_modules\@embedded-postgres\windows-x64\native\bin`. Issue
[#62](https://github.com/lgriffin/SDP/issues/62#issuecomment-6505224629) records that route on port 5544
(that build has no `createdb.exe`, so the database is created with `psql` or psycopg). You start it
yourself with `pg_ctl` each time.

**Linux (Debian or Ubuntu).**
```sh
sudo apt install postgresql-16          # or the version your distribution ships, 16 or newer
sudo -u postgres createuser --pwprompt sdp
sudo -u postgres createdb --owner sdp sdp
```
The address is `postgresql://sdp:YOURPASSWORD@127.0.0.1:5432/sdp`.

**macOS.** `brew install postgresql@16`, `brew services start postgresql@16`, then `createdb sdp`. The
address is `postgresql://YOURNAME@127.0.0.1:5432/sdp` (no password).

## 4. The settings file

SDP reads its settings from environment variables. Keep them in one file you load before working.

**Windows.** Create `D:\SDP\sdp-env.cmd` in Notepad with these lines, putting in your password:

```bat
set "SDP_DATA=D:\SDP"
set "SDP_DATABASE_URL=postgresql://postgres:YOURPASSWORD@127.0.0.1:5432/sdp"
set "SDP_ARTIFACT_ROOT=D:\SDP\artifacts"
set "SDP_LEDGER_DIR=D:\SDP\ledger"
set "SDP_REPO_ROOT=D:\code\SDP"
set "SDP_PROFILES_DIR=D:\code\SDP\profiles"
set "SDP_ALLOW_UNSIGNED=1"
set "SDP_MIN_FREE_BYTES=2147483648"
set "PYTHONUTF8=1"
```

Then, in every new Command Prompt:

```bat
call D:\SDP\sdp-env.cmd
cd /d D:\code\SDP\python
```

**Linux and macOS.** Create `~/sdp-env.sh` with the same settings as `export NAME=value` lines (for
example `export SDP_DATA=$HOME/sdp-data`), and run `source ~/sdp-env.sh` in each new terminal.

What each setting means:

| Setting | Meaning |
|---|---|
| `SDP_DATA` | SDP's data folder. Your own frames' index, plate solutions and the sky sweep go here. |
| `SDP_DATABASE_URL` | The database address from step 3. |
| `SDP_ARTIFACT_ROOT` | Where every downloaded and made file is kept. This is what grows. |
| `SDP_LEDGER_DIR` | Where the ledger (who froze and approved what) is kept. |
| `SDP_REPO_ROOT` | The code folder. SDP records which code version ran, and finds the shipped profiles here. |
| `SDP_PROFILES_DIR` | The test profiles. Point it at a copy if you want to edit them. |
| `SDP_ALLOW_UNSIGNED=1` | Lets runs be recorded without your signature, as the desktop app does. Fine for learning. |
| `SDP_MIN_FREE_BYTES` | SDP refuses to download when free space would drop below this. The default is 50 GB, meant for a server; 2 GB (2147483648) suits a PC. |
| `PYTHONUTF8=1` | Windows only: makes Python read and write files as UTF-8, as SDP expects. |

Other settings you may meet later: `SDP_GAIA_SOURCE` (where Gaia rows come from once you have the bulk
copy, see the [cluster hunt](discovery-search.md)), `SDP_ARCHIVE_MIRRORS=0` (never fail over to a
mirror archive), and `SDP_PLAN_APPROVAL_THRESHOLD_BYTES` (the plan size above which approval needs your
signature; 500 MB by default). They are all read in
[`sdp/adapters/settings.py`](../../python/packages/sdp-adapters/src/sdp/adapters/settings.py).

## 5. Prepare the database (once)

```bat
uv run sdp db migrate
uv run sdp integration register
```

- `db migrate` creates SDP's tables. **Success:** it lists the migrations it applied (four on a new
  database) and finishes in under a minute.
- `integration register` tells the database what each archive offers, so plans can be drafted. It
  reads MAST's description live from the Virtual Observatory, so it takes about 3 minutes.
  **Success:** a JSON list of registered sources, including `gaia`, `2mass`, `ps1` and `mast`.

You are ready: go to [your first look at a field](first-look.md).

## Keeping up to date

```bat
cd /d D:\code\SDP
git pull
cd python
uv sync --frozen
uv run sdp db migrate
```

Run `uv run sdp integration register` again when a new archive is added (the release notes say so).

## The app in a browser (optional)

The same pages the desktop app shows, served from your copy. You need Node.js and pnpm (step 1). In one
Command Prompt (after `call D:\SDP\sdp-env.cmd`):

```bat
cd /d D:\code\SDP\python
uv run sdp-api
```

It serves on `http://127.0.0.1:8000`. Leave it running. In a second Command Prompt (also after
`call D:\SDP\sdp-env.cmd`):

```bat
cd /d D:\code\SDP
pnpm install
pnpm --filter @sdp/evidence-ui dev
```

Open the address it prints (usually <http://localhost:5173>). If you have `just` and Git Bash, `just api`
and `just ui` do the same.

## A signing key (only for signed steps)

Exploring never needs a signature. Freezing a predeclaration, approving a verdict, or approving a plan
over 500 MB does. SDP signs with an Ed25519 SSH key **without a passphrase**:

```bat
mkdir D:\SDP\keys
ssh-keygen -t ed25519 -N "" -C leigh -f D:\SDP\keys\sdp_ledger
```

Add yourself to the list of allowed signers in the ledger folder (`D:\SDP\ledger\allowed_signers`): one
line, your name, a space, then the contents of `D:\SDP\keys\sdp_ledger.pub`, for example

```
leigh ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...
```

Then add to `sdp-env.cmd`:

```bat
set "SDP_SIGNING_KEY=D:\SDP\keys\sdp_ledger"
set "SDP_SIGNER=leigh"
```

Keep the key file private: anyone with it can sign as you. `uv run sdp ledger verify` checks every
signature and the chain; it exits with code 1 and names the entry if anything is wrong.

## If it goes wrong

- **`missing environment variables: SDP_DATABASE_URL`.** This window has not loaded the settings: run
  `call D:\SDP\sdp-env.cmd`.
- **`connection refused` or `could not connect to server`.** PostgreSQL is not running. On Windows,
  open **Services** and start `postgresql-x64-16`.
- **`password authentication failed`.** The password in `SDP_DATABASE_URL` does not match the one you
  chose at install.
- **A plan says "No archive offers …".** Run `uv run sdp integration register`.
- **Acquisition refused for free space.** Free some space, or lower `SDP_MIN_FREE_BYTES`.
