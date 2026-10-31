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
import fnmatch
import json
import re
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
    #: the directory holding ``src`` and the tests, relative to ``python/``; packages/<package> by default
    root: str = ""
    #: how many times its tests' measured time a mutant may run before mutmut stops it as a timeout
    timeout_multiplier: float = 5


#: The v0.4.0 integration cores (association and the product model) are scored as a target of their
#: own, so the domain and science targets leave them out.
INTEGRATION = (
    "src/sdp/kernel/domain/association.py",
    "src/sdp/kernel/domain/integration.py",
    "src/sdp/kernel/science/association.py",
)

#: The largest kernel modules are carved into targets of their own, each a CI job: on the first seeding
#: run the domain and application jobs were still running after 135 of their 180 minutes, because most
#: kernel suites cover those modules and each surviving mutant runs minutes of tests. A carved module is
#: left out of its layer's target, so every kernel module is still scored by exactly one target.
INVESTIGATION = ("src/sdp/kernel/domain/investigation.py",)
INVESTIGATE = ("src/sdp/kernel/application/investigate.py",)
PLANNING = ("src/sdp/kernel/application/planning.py", "src/sdp/kernel/application/acquisition.py")


#: The science packages are scored in shards of modules, each a CI job of its own. Every mutant runs
#: the package's end-to-end protocol tests (about a minute per suite), so one job per package ran past
#: the 180-minute limit: v0.1.0 reached 2640 of 3593 stellar mutants and 489 of 2734 relativity ones.
#: Each shard still runs the whole test suite, so a shard's score means what the package score meant.
#: The groups are balanced by source size; every module, subpackages included, must appear in exactly
#: one shard, and the Mutation workflow takes its job list from ``--list``, so a new shard is scheduled.
#: ``analysis`` stands alone: most of its mutants make the protocol suite hang until mutmut's timeout,
#: and on v0.2.0 the analysis-and-dossier shard reached 595 of 755 mutants (405 of them timeouts) in
#: 180 minutes, so the job limit is 300 minutes as well.
RELATIVITY_SHARDS = (
    ("analysis",),
    ("dossier", "fitter", "reduction"),
    ("protocol", "comparison", "constants"),
    ("observations", "synthetic", "geometry"),
    ("models", "measurement", "transcription", "oracles"),
    ("paper1920",),
    ("einstein1921",),
)
#: On v0.4.0 the analysis shard still reached only 557 of its 740 mutants in 300 minutes, 388 of them
#: timeouts at about two minutes each and none surviving. A hang is stopped at 2x its tests' measured time
#: instead of 5x: a timeout counts as detected either way, so the score is unchanged and the shard fits.
ANALYSIS_TIMEOUT = 2
STELLAR_SHARDS = (
    ("analysis", "benchmark", "cmd"),
    ("protocol", "features", "quality", "ranking"),
    ("synthetic", "inject", "search", "membership", "dossier", "null", "sweep"),
)

#: The Gaia DataLink adapters (INT-022): the shared DR3 facts, the gateway and inventory, the VOTable
#: reader and the Gaia dialect, scored on the suites that drive them with real recorded products.
DATALINK_ADAPTERS = (
    "src/sdp/adapters/gaia_dr3.py",
    "src/sdp/adapters/products/votable.py",
    "src/sdp/adapters/products/gaia.py",
    "src/sdp/adapters/archives/gaia_datalink.py",
)
DATALINK_TESTS = ("tests/products", "tests/archives")
#: The WISE and NEOWISE light-curve adapters (INT-023), on the same suites.
WISE_ADAPTERS = (
    "src/sdp/adapters/wise.py",
    "src/sdp/adapters/archives/irsa_wise.py",
    "src/sdp/adapters/products/wise.py",
)

#: Every suite that drives the kernel. A mutant no selected test reaches counts against the score, so a
#: kernel target selects every suite that exercises its modules: on v0.1.0 the science target ran only
#: the unit and property suites and scored 12.65%, because the investigation, product and planner
#: modules are exercised from the use-case, investigate, archive and product suites. mutmut runs only
#: the tests that cover a mutant, so a wider selection costs one baseline run, not one run per mutant.
KERNEL_TESTS = (
    "tests/unit/kernel",
    "tests/property",
    "tests/usecase",
    "tests/contract",
    "tests/investigate",
    "tests/association",
    "tests/products",
    "tests/archives",
    "tests/bdd",
    "tests/api",
)
#: Directories the selected tests read but that hold no tests of their own.
DATA = ("tests/cassettes",)


def modules(package: str, names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"src/{package}/{name}.py" for name in names)


TARGETS = {
    t.name: t
    for t in (
        Target(
            "sdp-kernel-domain",
            "sdp-kernel",
            ("src/sdp/kernel/domain/*",),
            KERNEL_TESTS,
            (*INTEGRATION, *INVESTIGATION),
        ),
        Target(
            "sdp-kernel-science",
            "sdp-kernel",
            ("src/sdp/kernel/science/*",),
            KERNEL_TESTS,
            INTEGRATION,
        ),
        Target(
            "sdp-kernel-application",
            "sdp-kernel",
            ("src/sdp/kernel/application/*",),
            KERNEL_TESTS,
            (*INVESTIGATE, *PLANNING),
        ),
        Target("sdp-kernel-integration", "sdp-kernel", INTEGRATION, KERNEL_TESTS),
        Target("sdp-kernel-investigation", "sdp-kernel", INVESTIGATION, KERNEL_TESTS),
        Target("sdp-kernel-investigate", "sdp-kernel", INVESTIGATE, KERNEL_TESTS),
        Target("sdp-kernel-planning", "sdp-kernel", PLANNING, KERNEL_TESTS),
        Target("sdp-kernel-ports", "sdp-kernel", ("src/sdp/kernel/ports/*",), KERNEL_TESTS),
        Target("sdp-adapters-datalink", "sdp-adapters", DATALINK_ADAPTERS, DATALINK_TESTS),
        Target("sdp-adapters-wise", "sdp-adapters", WISE_ADAPTERS, DATALINK_TESTS),
        Target(
            "sdp-investigate-exposures",
            "sdp-investigate",
            ("src/sdp_investigate/exposures.py",),
            ("tests/investigate", "tests/local"),
        ),
        *(
            Target(
                f"sdp-relativity-{i}",
                "sdp-relativity",
                modules("sdp_relativity", group),
                ("tests/relativity",),
                timeout_multiplier=ANALYSIS_TIMEOUT if group == ("analysis",) else 5,
            )
            for i, group in enumerate(RELATIVITY_SHARDS, 1)
        ),
        *(
            Target(f"sdp-stellar-{i}", "sdp-stellar", modules("sdp_stellar", group), ("tests/stellar",))
            for i, group in enumerate(STELLAR_SHARDS, 1)
        ),
    )
}

#: The known-weak fixture (TST-009), run by ``--fixture`` and never scored.
FIXTURE = Target("mutation-fixture", "", ("src/weak/*",), ("tests",), root="tools/mutation_fixture")

SHARED = ("contracts", "typescript", "experiments", "requirements", "fixtures", "profiles", "capabilities", "docs")

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
        # these suites; 5x still separates a hang from a slow pass and keeps timeouts affordable
        f"timeout_multiplier = {target.timeout_multiplier:g}\n"
        + (f"do_not_mutate =\n    {skip}\n" if (skip := "\n    ".join(target.do_not_mutate)) else "")
    )


def score(stats: dict[str, int]) -> float:
    detected = stats.get("killed", 0) + stats.get("timeout", 0)
    reached = detected + stats.get("survived", 0) + stats.get("no_tests", 0)
    if not reached:
        raise RuntimeError(f"mutmut reached no mutants ({stats}); the baseline tests or the run failed")
    return round(100.0 * detected / reached, 2)


#: A test module that imports a helper from another suite, as ``from tests.local.synthetic import ...``.
SUITE_IMPORT = re.compile(r"^\s*(?:from|import)\s+tests\.(\w+)", re.MULTILINE)


def copy_imported_suites(tests_root: Path, work: Path) -> None:
    """Copy the suites the selected tests import from, without selecting them.

    A kernel suite that imports a helper from an unselected suite (tests/investigate from tests/local)
    failed every kernel job at collection, so imported suites follow the tests that need them.
    """
    while missing := {
        suite
        for module in (work / "tests").rglob("*.py")
        for suite in SUITE_IMPORT.findall(module.read_text())
        if not (work / "tests" / suite).exists() and (tests_root / "tests" / suite).is_dir()
    }:
        for suite in sorted(missing):
            shutil.copytree(
                tests_root / "tests" / suite, work / "tests" / suite, ignore=shutil.ignore_patterns("__pycache__")
            )


def run(target: Target, children: int, keep: Path | None = None) -> tuple[float, dict[str, int]]:
    with tempfile.TemporaryDirectory(prefix=f"mutmut-{target.name}-") as scratch:
        # --keep leaves the run behind, so `mutmut results` and `mutmut show <mutant>` can be read there
        tmp = str(keep / target.name) if keep else scratch
        if keep:
            shutil.rmtree(tmp, ignore_errors=True)
            Path(tmp).mkdir(parents=True)
        work = Path(tmp) / "work"
        work.mkdir()
        # tests find repository files relative to themselves; whether they run from work/tests or
        # work/mutants/tests, those lookups land in tmp or work, so both point at the repository
        for where in (Path(tmp), work):
            for shared in SHARED:
                (where / shared).symlink_to(ROOT.parent / shared)
        root = ROOT / (target.root or f"packages/{target.package}")
        shutil.copytree(root / "src", work / "src")
        tests_root = root if target.root else ROOT
        for path in (*target.tests, *(() if target.root else DATA)):
            shutil.copytree(tests_root / path, work / path, ignore=shutil.ignore_patterns("__pycache__"))
        if not target.root:
            copy_imported_suites(tests_root, work)
        shutil.copy2(ROOT / "tests" / "conftest.py", work / "tests" / "conftest.py")
        (work / "pytest.ini").write_text(PYTEST_INI)
        (work / "setup.cfg").write_text(setup_cfg(target))
        # mutmut's output streams straight into the job log, so a failed baseline is visible as it
        # happens; its exit status is non-zero whenever mutants survive, so score() judges the run
        subprocess.run([sys.executable, "-m", "mutmut", "run", "--max-children", str(children)], cwd=work, check=False)
        subprocess.run([sys.executable, "-m", "mutmut", "export-cicd-stats"], cwd=work, check=True)
        stats = json.loads((work / "mutants" / "mutmut-cicd-stats.json").read_text())
    return score(stats), stats


def fixture_problems(stats: dict[str, int]) -> list[str]:
    """What is wrong with a fixture run: it must lose a mutant to a test and leave one alive (TST-009)."""
    problems = []
    if not stats.get("killed", 0) + stats.get("timeout", 0):
        problems.append("no fixture mutant was killed: the run cannot detect a fault")
    if not stats.get("survived", 0) + stats.get("no_tests", 0):
        problems.append("no fixture mutant survived: the run cannot show a weak test")
    return problems


def fixture(children: int) -> int:
    """Prove the pipeline can go red on the known-weak fixture before any score is trusted."""
    percent, stats = run(FIXTURE, children)
    print(f"mutation fixture: {percent}% {stats}", flush=True)
    problems = fixture_problems(stats)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    return 1 if problems else 0


def check_shards() -> None:
    """Every module of a sharded package, and every kernel module, is scored by exactly one target, so a
    new module is never skipped and a carved one never scored twice."""
    for package, shards in (("sdp_relativity", RELATIVITY_SHARDS), ("sdp_stellar", STELLAR_SHARDS)):
        src = ROOT / "packages" / package.replace("_", "-") / "src" / package
        files = sorted(
            p.relative_to(src).with_suffix("").as_posix() for p in src.rglob("*.py") if p.name != "__init__.py"
        )
        listed = sorted(name for group in shards for name in group)
        if files != listed:
            raise SystemExit(f"{package} shards list {listed}, but the package has {files}")
    kernel = ROOT / "packages" / "sdp-kernel"
    targets = [t for t in TARGETS.values() if t.package == "sdp-kernel"]
    for path in sorted((kernel / "src" / "sdp" / "kernel").rglob("*.py")):
        if path.name == "__init__.py":
            continue
        name = path.relative_to(kernel).as_posix()
        scoring = [t.name for t in targets if _mutates(t, name)]
        if len(scoring) != 1:
            raise SystemExit(f"{name} is scored by {scoring or 'no target'}; every kernel module needs exactly one")


def _mutates(target: Target, path: str) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in target.only_mutate) and path not in target.do_not_mutate


def main(argv: list[str] | None = None) -> int:
    check_shards()
    parser = argparse.ArgumentParser(prog="tools/mutation.py")
    parser.add_argument("targets", nargs="*", default=sorted(TARGETS))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--list", action="store_true", help="print every target name as JSON, for the CI matrix")
    parser.add_argument("--children", type=int, default=4)
    parser.add_argument("--keep", type=Path, help="keep each target's mutmut directory under this path")
    parser.add_argument("--fixture", action="store_true", help="run the known-weak fixture and check it (TST-009)")
    args = parser.parse_args(argv)
    if args.fixture:
        return fixture(args.children)
    if args.list:
        print(json.dumps(sorted(TARGETS)))
        return 0
    if args.out is None:
        parser.error("--out is required")
    scores: dict[str, float] = {}
    details: dict[str, dict[str, int]] = {}
    for name in args.targets:
        scores[f"python/{name}"], details[name] = run(TARGETS[name], args.children, args.keep)
        print(f"{name}: {scores[f'python/{name}']}% {details[name]}", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(scores, indent=2, sort_keys=True) + "\n")
    args.out.with_suffix(".details.json").write_text(json.dumps(details, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
