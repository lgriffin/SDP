"""The known-weak fixture (TST-009): a correct function whose test is deliberately too weak.

``tools/mutation.py --fixture`` mutates this file and fails unless at least one mutant is killed (the
pipeline can detect a fault) and at least one survives (a weak test is visible). Do not strengthen
the test: a fixture that kills everything proves nothing about the pipeline's ability to go red.
"""


def grade(score: int) -> str:
    if score >= 90:
        return "distinction"
    if score >= 50:
        return "pass"
    return "fail"
