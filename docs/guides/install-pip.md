# Install the `sdp` command with pip

**Time:** about 10 minutes · **Needs:** Python 3.12 or newer, a PostgreSQL 16 database, and about 2 GB
free

Use this route if you already work in Python and only want the `sdp` command in an environment of your
own. It installs the command line and, optionally, the API, without the app.

## Not on PyPI yet

SDP's packages are **not published on PyPI**, so `pip install "sdp-cli[science]"` on its own fails with
"No matching distribution". Install them from a copy of the repository instead, as below. You can take
the copy with Git, or download it as a zip from the repository's green **Code** button
(<https://github.com/lgriffin/SDP>) and unpack it.

`sdp-cli` is the command. Its `[science]` extra adds the science modules: the Investigate tests
(`sdp-investigate`), the cluster study (`sdp-stellar`) and the Einstein study (`sdp-relativity`).
Without the extra the command runs, but any profile or experiment that needs one of those is refused
before it starts.

## Steps

**Windows (Command Prompt):**

```bat
git clone https://github.com/lgriffin/SDP.git D:\code\SDP
cd /d D:\code\SDP\python\packages
py -3.12 -m venv D:\SDP\venv
D:\SDP\venv\Scripts\activate
pip install .\sdp-contracts .\sdp-kernel .\sdp-adapters .\sdp-stellar .\sdp-investigate .\sdp-relativity ".\sdp-cli[science]"
```

**Linux and macOS:**

```sh
git clone https://github.com/lgriffin/SDP.git
cd SDP/python/packages
python3.12 -m venv ~/sdp-venv
source ~/sdp-venv/bin/activate
pip install ./sdp-contracts ./sdp-kernel ./sdp-adapters ./sdp-stellar ./sdp-investigate ./sdp-relativity "./sdp-cli[science]"
```

Every SDP package is named on the line, because pip cannot find SDP's own packages anywhere else. Add
`"./sdp-api[science]"` to the same line if you also want the API server (`sdp-api`).

**Success:** `sdp --help` lists the commands, and `sdp archive sources` prints the archive sources
(2MASS, Gaia DR3, PS1 DR2) with their licences, without needing a database.

## Then

pip gives you the command, not the database or the settings. Follow the
[developer tools guide](install-dev-tools.md) from **step 3, the database**: make the database, write
the settings file, and run `sdp db migrate` and `sdp integration register`. Leave out the `uv run` in
front of each command: with your environment active, `sdp` is the command itself.

Point `SDP_REPO_ROOT` and `SDP_PROFILES_DIR` at your copy of the repository, so SDP finds the shipped
test profiles and records which code version ran.

## Differences from the uv route

- pip picks the newest versions of SDP's dependencies (numpy, astropy and so on) that fit, rather than
  the exact versions the repository locks. Runs therefore may not reproduce bit for bit against
  someone else's. For results you intend to share, use the [developer tools](install-dev-tools.md),
  whose `uv sync --frozen` installs exactly the locked versions.
- To update, `git pull` and run the same `pip install` line again.
