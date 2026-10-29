"""Mutation scores per package (§11.5, T10): mutmut over one package at a time, scores to JSON.

    uv run python tools/mutation.py --out ../reports/mutation/python.json [target ...]

Each target copies its package's ``src`` and the tests that exercise it into a scratch directory,
because mutmut expects a single ``src`` layout and a uv workspace spreads ``sdp`` over several.
The score is (killed + timeout) / (killed + timeout + survived + no_tests) in percent, the same
accounting as typescript/tools/stryker-scores.mjs: a mutant that hangs the tests is detected, and one
no test reaches counts against the score, so an untested line lowers it. A run that reaches no
mutant at all is an error, never a score of 0. spec-harness ratchet compares the scores with the
recorded high-water marks.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Target:
    name: str
    package: str
    only_mutate: tuple[str, ...]
    tests: tuple[str, ...]
    do_not_mutate: tuple[str, ...] = ()


#: The v0.4.0 integration cores (association, the acquisition planner, the product model) are scored
#: by the integration work's own mutation targets, so the main train leaves them out.
INTEGRATION = (
    "src/sdp/kernel/domain/association.py",
    "src/sdp/kernel/domain/integration.py",
    "src/sdp/kernel/science/association.py",
)


#: The science packages are scored in shards of modules, each a CI job of its own. Every mutant runs
#: the package's end-to-end protocol tests (about a minute per suite), so one job per package ran past
#: the 180-minute limit: v0.1.0 reached 2640 of 3593 stellar mutants and 489 of 2734 relativity ones.
#: Each shard still runs the whole test suite, so a shard's score means what the package score meant.
#: The groups are balanced by source size; every module, subpackages included, must appear in exactly
#: one shard, and the Mutation workflow takes its job list from ``--list``, so a new shard is scheduled.
RELATIVITY_SHARDS = (
    ("analysis", "dossier"),
    ("fitter", "reduction"),
    ("protocol", "comparison", "constants"),
    ("observations", "synthetic", "geometry"),
    ("models", "measurement", "transcription", "oracles"),
)
STELLAR_SHARDS = (
    ("analysis", "benchmark", "cmd"),
    ("protocol", "features", "quality", "ranking"),
    ("synthetic", "inject", "search", "membership", "dossier", "null"),
)


def modules(package: str, names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"src/{package}/{name}.py" for name in names)


TARGETS = {
    t.name: t
    for t in (
        Target(
            "sdp-kernel-domain",
            "sdp-kernel",
            ("src/sdp/kernel/domain/*",),
            ("tests/unit/kernel", "tests/property"),
            INTEGRATION,
        ),
        Target(
            "sdp-kernel-science",
            "sdp-kernel",
            ("src/sdp/kernel/science/*",),
            ("tests/unit/kernel", "tests/property"),
            INTEGRATION,
        ),
        Target(
            "sdp-kernel-application",
            "sdp-kernel",
            ("src/sdp/kernel/application/*",),
            ("tests/unit/kernel", "tests/usecase", "tests/contract"),
        ),
        *(
            Target(f"sdp-relativity-{i}", "sdp-relativity", modules("sdp_relativity", group), ("tests/relativity",))
            for i, group in enumerate(RELATIVITY_SHARDS, 1)
        ),
        *(
            Target(f"sdp-stellar-{i}", "sdp-stellar", modules("sdp_stellar", group), ("tests/stellar",))
            for i, group in enumerate(STELLAR_SHARDS, 1)
        ),
    )
}

SHARED = ("contracts", "typescript", "experiments", "requirements")

PYTEST_INI = """[pytest]
addopts = -x -q -p no:cacheprovider --import-mode=importlib -m "not integration and not live and not gpu and not slow"
markers =
    req(id): requirement
    tier(name): tier
    gate(id): gate
    scientific: T8
    integration: T5
    live: T11
    gpu: CUDA
    slow: long-running
"""


def setup_cfg(target: Target) -> str:
    only = "\n    ".join(target.only_mutate)
    tests = "\n    ".join(target.tests)
    return (
        "[mutmut]\n"
        "source_paths = src\n"
        f"only_mutate =\n    {only}\n"
        f"pytest_add_cli_args_test_selection =\n    {tests}\n"
        "also_copy =\n    tests\n    pytest.ini\n"
        # mutmut's default stops a hanging mutant at 30x its tests' CPU time, about five minutes for
        # these suites; 10x still separates a hang from a slow pass and keeps timeouts affordable
        "timeout_multiplier = 5\n"
        + (f"do_not_mutate =\n    {skip}\n" if (skip := "\n    ".join(target.do_not_mutate)) else "")
    )


def score(stats: dict[str, int]) -> float:
    detected = stats.get("killed", 0) + stats.get("timeout", 0)
    reached = detected + stats.get("survived", 0) + stats.get("no_tests", 0)
    if not reached:
        raise RuntimeError(f"mutmut reached no mutants ({stats}); the baseline tests or the run failed")
    return round(100.0 * detected / reached, 2)


def run(target: Target, children: int) -> tuple[float, dict[str, int]]:
    with tempfile.TemporaryDirectory(prefix=f"mutmut-{target.name}-") as tmp:
        work = Path(tmp) / "work"
        work.mkdir()
        # tests find repository files relative to themselves; whether they run from work/tests or
        # work/mutants/tests, those lookups land in tmp or work, so both point at the repository
        for where in (Path(tmp), work):
            for shared in SHARED:
                (where / shared).symlink_to(ROOT.parent / shared)
        shutil.copytree(ROOT / "packages" / target.package / "src", work / "src")
        (work / "tests").mkdir()
        shutil.copy2(ROOT / "tests" / "conftest.py", work / "tests" / "conftest.py")
        for path in target.tests:
            shutil.copytree(ROOT / path, work / path, ignore=shutil.ignore_patterns("__pycache__"))
        (work / "pytest.ini").write_text(PYTEST_INI)
        (work / "setup.cfg").write_text(setup_cfg(target))
        # mutmut's output streams straight into the job log, so a failed baseline is visible as it
        # happens; its exit status is non-zero whenever mutants survive, so score() judges the run
        subprocess.run([sys.executable, "-m", "mutmut", "run", "--max-children", str(children)], cwd=work, check=False)
        subprocess.run([sys.executable, "-m", "mutmut", "export-cicd-stats"], cwd=work, check=True)
        stats = json.loads((work / "mutants" / "mutmut-cicd-stats.json").read_text())
    return score(stats), stats


def check_shards() -> None:
    """Every module of a sharded package is scored by exactly one shard, so a new module is never skipped."""
    for package, shards in (("sdp_relativity", RELATIVITY_SHARDS), ("sdp_stellar", STELLAR_SHARDS)):
        src = ROOT / "packages" / package.replace("_", "-") / "src" / package
        files = sorted(
            p.relative_to(src).with_suffix("").as_posix() for p in src.rglob("*.py") if p.name != "__init__.py"
        )
        listed = sorted(name for group in shards for name in group)
        if files != listed:
            raise SystemExit(f"{package} shards list {listed}, but the package has {files}")


def main(argv: list[str] | None = None) -> int:
    check_shards()
    parser = argparse.ArgumentParser(prog="tools/mutation.py")
    parser.add_argument("targets", nargs="*", default=sorted(TARGETS))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--list", action="store_true", help="print every target name as JSON, for the CI matrix")
    parser.add_argument("--children", type=int, default=4)
    args = parser.parse_args(argv)
    if args.list:
        print(json.dumps(sorted(TARGETS)))
        return 0
    if args.out is None:
        parser.error("--out is required")
    scores: dict[str, float] = {}
    details: dict[str, dict[str, int]] = {}
    for name in args.targets:
        scores[f"python/{name}"], details[name] = run(TARGETS[name], args.children)
        print(f"{name}: {scores[f'python/{name}']}% {details[name]}", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(scores, indent=2, sort_keys=True) + "\n")
    args.out.with_suffix(".details.json").write_text(json.dumps(details, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
