"""Record the real archive answers SDP still lacks, check they replay, and push them to a branch for review.

    cd python
    uv run python tools/record_live_data.py            # record, check, commit and push a branch
    uv run python tools/record_live_data.py --no-push  # record and check only; nothing leaves this machine

It does what the Archive live smoke workflow does, from wherever it runs:
1. The live smoke (``sdp archive smoke``): every TAP source's schema and a small Pleiades cone
   (Gaia DR3, PS1 DR2, 2MASS), and the Investigate sources. Each answer is saved as a cassette.
2. The CDS X-Match agreement check (DSI-009): the cross-match contract's fixture is sent to the
   live service and its answers are recorded.
3. The replay tests run on this run's recordings alone (``reports/cassettes``), so you see straight
   away what this run covered and what still skips; an older committed cassette never stands in.
4. With pushing on, it copies the recordings into ``tests/cassettes``, commits only that directory
   on a new ``archive-cassettes/<label>`` branch and pushes it. Open a pull request from that branch;
   it is reviewed like code. With ``--no-push`` the working tree is left as it was.

Recordings are always real answers: nothing here writes a cassette by hand. A source that fails is
reported and left out; what did record is still pushed. The exit status is 1 if anything failed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

PYTHON = Path(__file__).resolve().parents[1]
REPO = PYTHON.parent
RECORDINGS = REPO / "reports" / "cassettes"
CASSETTES = PYTHON / "tests" / "cassettes"
#: The venv's own tools, so it works the same under ``uv run`` on Linux, macOS and Windows.
BIN = Path(sys.executable).parent
REPLAY_TESTS = ("tests/archives/test_recorded_responses.py", "tests/archives/test_xmatch_recorded.py")


def step(title: str, command: list[str], env: dict[str, str] | None = None, save: Path | None = None) -> bool:
    print(f"\n== {title}\n$ {' '.join(command)}", flush=True)
    run = subprocess.run(
        command, cwd=PYTHON, env={**os.environ, **(env or {})}, check=False, stdout=subprocess.PIPE if save else None
    )
    if save is not None:
        save.write_bytes(run.stdout)
        sys.stdout.write(run.stdout.decode("utf-8", "replace"))
    print(f"-- {'ok' if run.returncode == 0 else 'FAILED'}: {title}", flush=True)
    return run.returncode == 0


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools/record_live_data.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-push", dest="push", action="store_false", help="record and check, but do not commit")
    parser.add_argument("--label", default=datetime.now(UTC).strftime("local-%Y%m%d-%H%M%S"), help="branch suffix")
    args = parser.parse_args(argv)

    if args.push and git("status", "--porcelain", "--", str(CASSETTES)):
        print(f"{CASSETTES} has uncommitted changes; commit or discard them first", file=sys.stderr)
        return 2

    shutil.rmtree(RECORDINGS, ignore_errors=True)
    RECORDINGS.mkdir(parents=True)
    smoke = step(
        "live smoke: schemas and cones from every archive",
        [str(BIN / "sdp"), "archive", "smoke", "--baseline", "tests/cassettes", "--record", str(RECORDINGS)],
        save=REPO / "reports" / "archive-smoke.json",
    )
    xmatch = step(
        "CDS X-Match agreement on the cross-match fixture (DSI-009)",
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-m",
            "live",
            "tests/archives/test_xmatch_recorded.py",
        ],
        env={"SDP_XMATCH_RECORD": str(RECORDINGS / "xmatch")},
    )

    replay = step(
        "replay this run's recordings",
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs", *REPLAY_TESTS],
        env={"SDP_CASSETTES": str(RECORDINGS)},
    )

    if args.push:
        shutil.copytree(RECORDINGS, CASSETTES, dirs_exist_ok=True)
        git("add", "--", str(CASSETTES))
        if not git("diff", "--cached", "--name-only", "--", str(CASSETTES)):
            print("\nnothing new was recorded; no branch pushed")
        else:
            branch = f"archive-cassettes/{args.label}"
            git("switch", "-c", branch)
            # only the cassettes: anything else already staged stays staged and out of this commit
            git("commit", "-m", f"Archive live recordings ({args.label})", "--", str(CASSETTES))
            git("push", "-u", "origin", branch)
            print(f"\npushed {branch}; open a pull request from it to merge the recordings")

    failed = [name for name, ok in (("live smoke", smoke), ("X-Match", xmatch), ("replay", replay)) if not ok]
    print("\nall recorded" if not failed else f"\nfailed: {', '.join(failed)} (see the output above)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
