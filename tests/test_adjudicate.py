"""Unit tests for the conservative adjudicator and failure taxonomy."""

from __future__ import annotations

import pytest

from liedetector.adjudicate import adjudicate, adjudicate_install_failure
from liedetector.models import (
    Claim,
    ClaimType,
    Confidence,
    ExecutionRun,
    FailureCategory,
    Source,
    Verdict,
)

from .conftest import make_run

HARNESS = "def test_control():\n    pass\n"


def _claim() -> Claim:
    return Claim(
        id="clm-x",
        source=Source(file="README.md", line=1, quote="q"),
        claim_type=ClaimType.DETERMINISTIC,
        hypothesis="h",
        interpretation_notes="n",
        confidence="high",
    )


def test_pass_pass_is_proven_high_confidence() -> None:
    runs = [make_run(1), make_run(2)]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.verdict == Verdict.PROVEN
    assert ev.verdict_confidence == Confidence.HIGH


def test_pass_fail_is_inconclusive() -> None:
    runs = [make_run(1, claim_passed=True), make_run(2, exit_code=1, claim_passed=False)]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.verdict == Verdict.INCONCLUSIVE


def test_fail_fail_in_target_is_false() -> None:
    tb = 'File "/repo/toylib/__init__.py", line 3\nAssertionError'
    runs = [
        make_run(1, exit_code=1, stdout=tb, control_passed=True, claim_passed=False),
        make_run(2, exit_code=1, stdout=tb, control_passed=True, claim_passed=False),
    ]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.verdict == Verdict.FALSE
    assert ev.failure_category == FailureCategory.TARGET_FAILURE
    assert ev.verdict_confidence == Confidence.HIGH


def test_fail_fail_with_failed_control_is_inconclusive_never_false() -> None:
    runs = [
        make_run(1, exit_code=1, control_passed=False, claim_passed=None),
        make_run(2, exit_code=1, control_passed=False, claim_passed=None),
    ]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.verdict != Verdict.FALSE
    assert ev.verdict == Verdict.INCONCLUSIVE


def test_timeout_is_inconclusive_never_false() -> None:
    runs = [
        make_run(1, timed_out=True, control_passed=None, claim_passed=None),
        make_run(2, timed_out=True, control_passed=None, claim_passed=None),
    ]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.verdict == Verdict.INCONCLUSIVE
    assert ev.failure_category == FailureCategory.TIMEOUT


def test_import_failure_categorised() -> None:
    tb = "ModuleNotFoundError: No module named 'toylib'"
    runs = [
        make_run(1, exit_code=1, stdout=tb, control_passed=False, claim_passed=None),
        make_run(2, exit_code=1, stdout=tb, control_passed=False, claim_passed=None),
    ]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.failure_category == FailureCategory.IMPORT_FAILURE
    assert ev.verdict == Verdict.INCONCLUSIVE


def test_resource_limit_categorised() -> None:
    runs = [
        make_run(1, exit_code=137, control_passed=True, claim_passed=False, stderr="Killed"),
        make_run(2, exit_code=137, control_passed=True, claim_passed=False, stderr="Killed"),
    ]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.failure_category == FailureCategory.RESOURCE_LIMIT
    assert ev.verdict == Verdict.INCONCLUSIVE


def test_install_failure_is_inconclusive() -> None:
    ev = adjudicate_install_failure(_claim())
    assert ev.verdict == Verdict.INCONCLUSIVE
    assert ev.failure_category == FailureCategory.INSTALL_FAILURE


# --- EDGE_CASE_AUDIT finding #1: model error must never read as a false claim ---
#
# Traceback shapes copied from EDGE_CASE_AUDIT.md section 1b, which reproduced
# both against the real adjudicate() and observed FALSE/TARGET_FAILURE/HIGH.

#: A Python harness calling an API symbol the model invented.
HALLUCINATED_SYMBOL_TB = """\
=================================== FAILURES ===================================
__________________________________ test_claim __________________________________

    def test_claim():
        import toylib
>       assert toylib.no_such_function("a") == "b"
E       AttributeError: module 'toylib' has no attribute 'no_such_function'

/harness/clm-x.py:11: AttributeError
=========================== short test summary info ============================
FAILED /harness/clm-x.py::test_claim - AttributeError: module 'toylib' has no \
attribute 'no_such_function'
"""

#: A Node harness reading a path the model guessed wrong.
WRONG_PATH_TB = """\
test_claim failed: Error: ENOENT: no such file or directory, open \
'/repo/src/wrong-path.ts'
    at async open (node:internal/fs/promises:639:25)
    at async test_claim (file:///harness/clm-x.mjs:12:20) {
  errno: -2,
  code: 'ENOENT',
  syscall: 'open',
  path: '/repo/src/wrong-path.ts'
}
"""


def _fail_fail(output: str) -> list[ExecutionRun]:
    """Two identical failing runs with a passing control — the FALSE gate."""
    return [
        make_run(i, exit_code=1, stdout=output, control_passed=True, claim_passed=False)
        for i in (1, 2)
    ]


def test_hallucinated_symbol_is_never_false() -> None:
    ev = adjudicate(_claim(), HARNESS, _fail_fail(HALLUCINATED_SYMBOL_TB), "toylib")
    assert ev.verdict != Verdict.FALSE
    assert ev.verdict == Verdict.INCONCLUSIVE
    assert ev.failure_category == FailureCategory.HARNESS_ERROR
    assert ev.verdict_confidence == Confidence.LOW


def test_wrong_path_in_node_harness_is_never_false() -> None:
    ev = adjudicate(_claim(), HARNESS, _fail_fail(WRONG_PATH_TB), "toyapp")
    assert ev.verdict != Verdict.FALSE
    assert ev.verdict == Verdict.INCONCLUSIVE
    assert ev.failure_category == FailureCategory.HARNESS_ERROR


def test_assertion_only_failure_is_still_false() -> None:
    """The capability the fix must preserve: a real contradicting value.

    This is the bundled demo's genuine FALSE (``count_words("a  b")`` returns
    3, not 2).  Its traceback anchors in the harness frame, so it reaches
    FALSE only through the assertion-only evidence path.
    """
    tb = (
        "    def test_claim():\n"
        "        import toylib\n"
        '>       assert toylib.count_words("a  b") == 2\n'
        "E       AssertionError: assert 3 == 2\n"
        "\n"
        "/harness/clm-x.py:11: AssertionError\n"
    )
    ev = adjudicate(_claim(), HARNESS, _fail_fail(tb), "toylib")
    assert ev.verdict == Verdict.FALSE
    assert ev.failure_category == FailureCategory.TARGET_FAILURE


def test_assertion_mixed_with_harness_error_is_never_false() -> None:
    """A half-working harness proves nothing, even though it did assert."""
    mixed = HALLUCINATED_SYMBOL_TB + "\nE       AssertionError: assert 1 == 2\n"
    ev = adjudicate(_claim(), HARNESS, _fail_fail(mixed), "toylib")
    assert ev.verdict == Verdict.INCONCLUSIVE
    assert ev.failure_category == FailureCategory.HARNESS_ERROR


# --- EDGE_CASE_AUDIT finding #3: deterministic sandbox faults are not proof ---


@pytest.mark.parametrize(
    ("name", "output"),
    [
        (
            "network disabled by --network none",
            'File "/env/venv/lib/python3.12/site-packages/toylib/client.py", line 9\n'
            "OSError: [Errno 101] Network is unreachable\n",
        ),
        (
            "repo mount is read-only",
            'File "/env/venv/lib/python3.12/site-packages/toylib/cache.py", line 4\n'
            "OSError: [Errno 30] Read-only file system: '/repo/cache.db'\n",
        ),
        (
            "system library absent from the slim image",
            'File "/env/venv/lib/python3.12/site-packages/toylib/fast.py", line 1\n'
            "ImportError: libgomp.so.1: cannot open shared object file: "
            "No such file or directory\n",
        ),
    ],
)
def test_sandbox_faults_are_never_false(name: str, output: str) -> None:
    """Each fault reproduces identically in both runs and lands in a frame
    inside the target, so it satisfied every FALSE condition."""
    ev = adjudicate(_claim(), HARNESS, _fail_fail(output), "toylib")
    assert ev.verdict != Verdict.FALSE, name
    assert ev.verdict == Verdict.INCONCLUSIVE
    assert ev.failure_category == FailureCategory.ENVIRONMENT_FAILURE


def test_traceback_in_target_still_false_without_an_assertion() -> None:
    """Evidence path 1 is unchanged: the target's own frame raising."""
    tb = (
        'File "/env/venv/lib/python3.12/site-packages/toylib/__init__.py", line 3\n'
        "ZeroDivisionError: division by zero\n"
    )
    ev = adjudicate(_claim(), HARNESS, _fail_fail(tb), "toylib")
    assert ev.verdict == Verdict.FALSE
    assert ev.failure_category == FailureCategory.TARGET_FAILURE
