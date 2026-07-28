"""Adjudication: conservative mapping from execution evidence to verdicts.

Double-execution policy (no exceptions):

- PASS PASS -> PROVEN
- FAIL FAIL -> continue to adjudication (FALSE only under strict conditions)
- PASS FAIL -> INCONCLUSIVE

``FALSE`` requires ALL of: both executions failed, the control assertion
passed, the failure is attributable to the target package, the harness is not
responsible, and the environment is healthy.  Anything else is INCONCLUSIVE.
Confidence is evidence-derived, never invented.

Attribution to the target needs *positive* evidence, never the absence of a
counter-signal.  Exactly two things supply it:

1. a traceback frame that resolves inside the target package, or
2. an assertion-only failure — ``test_claim`` reached its check, so every
   symbol resolved and every path existed, and the value contradicted the
   claim.

A ``test_claim`` that dies of a hallucinated API symbol, a guessed-wrong file
path or a bad call signature supplies neither, and is ``HARNESS_ERROR``: the
model erred, the repository did not, and the run says nothing about the claim.
"""

from __future__ import annotations

import logging
import re

from .models import (
    Claim,
    Confidence,
    Evaluation,
    ExecutionRun,
    FailureCategory,
    Verdict,
)

log = logging.getLogger("liedetector.adjudicate")

_RESOURCE_SIGNS = (
    "MemoryError",
    "Killed",
    "OOM",
    "Cannot allocate memory",
    "JavaScript heap out of memory",
)
_IMPORT_SIGNS = (
    "ModuleNotFoundError",
    "ImportError",
    "ERR_MODULE_NOT_FOUND",
    "Cannot find module",
)

#: A failed assertion is the *only* evidence that ``test_claim`` reached the
#: target and got a contradicting value: every symbol resolved, every path
#: existed, the call returned, and the returned value failed the check.
_ASSERTION_SIGNS = (
    "AssertionError",  # python assert / pytest, and node:assert/strict
    "ERR_ASSERTION",  # node:assert error code
)

#: Signs that ``test_claim`` died of a defect in the *harness* rather than a
#: contradicting value from the target: a hallucinated API symbol, a guessed
#: file path, a wrong call signature.  These are model errors, and a model
#: error must never be published as a repository lying.
_HARNESS_ERROR_SIGNS = (
    # Python
    "AttributeError",
    "NameError",
    "TypeError",
    "ImportError",
    "ModuleNotFoundError",
    "FileNotFoundError",
    "IsADirectoryError",
    "NotADirectoryError",
    "IndexError",
    "KeyError",
    "SyntaxError",
    "IndentationError",
    "UnboundLocalError",
    # Node
    "ReferenceError",
    "ERR_MODULE_NOT_FOUND",
    "Cannot find module",
    "ENOENT",
    "ERR_UNKNOWN_FILE_EXTENSION",
    "ERR_INVALID_MODULE_SPECIFIER",
    "is not a function",
    "is not defined",
)


def _identical(a: ExecutionRun, b: ExecutionRun) -> bool:
    """Same observable outcome across both runs (timing noise ignored)."""
    return (
        a.exit_code == b.exit_code
        and a.timed_out == b.timed_out
        and a.control_passed == b.control_passed
        and a.claim_passed == b.claim_passed
    )


def _traceback_in_target(run: ExecutionRun, package: str) -> bool:
    """Does the failure traceback originate inside the target package?

    Matches Python tracebacks (``File "..."`` frames in site-packages or the
    read-only repo mount) and Node stack traces (``at ...``/``file://`` frames
    inside the installed app at ``/env/app`` or the repo mount).
    """
    text = run.stdout + "\n" + run.stderr
    python_frames = re.compile(
        r"File \"[^\"]*(?:site-packages[/\\]" + re.escape(package) + r"|/repo)[^\"]*\""
    )
    node_frames = re.compile(r"(?:\bat |file://)[^\n]*(?:/env/app|/repo)")
    return bool(python_frames.search(text) or node_frames.search(text))


def _assertion_only_failure(run: ExecutionRun) -> bool:
    """Did ``test_claim`` fail on a plain assertion and nothing else?

    This is the positive evidence ``TARGET_FAILURE`` requires when no
    traceback frame resolves inside the target package.  A bare
    ``AssertionError`` means the harness executed all the way to its check:
    the import resolved, the attribute existed, the call returned — and the
    value contradicted the claim.  That is a statement about the target.

    Any harness-defect sign anywhere in the output disqualifies the run even
    when an ``AssertionError`` is also present, because a harness that half
    worked proves nothing about the repository.
    """
    text = run.stdout + "\n" + run.stderr
    if not any(sign in text for sign in _ASSERTION_SIGNS):
        return False
    return not any(sign in text for sign in _HARNESS_ERROR_SIGNS)


def _classify_failure(run: ExecutionRun, package: str) -> FailureCategory:
    text = run.stdout + "\n" + run.stderr
    if run.timed_out:
        return FailureCategory.TIMEOUT
    if any(sign in text for sign in _RESOURCE_SIGNS) or run.exit_code == 137:
        return FailureCategory.RESOURCE_LIMIT
    if run.control_passed is not True and any(sign in text for sign in _IMPORT_SIGNS):
        return FailureCategory.IMPORT_FAILURE
    if run.control_passed is not True:
        return FailureCategory.HARNESS_FAILURE
    if _traceback_in_target(run, package):
        return FailureCategory.TARGET_FAILURE
    if run.claim_passed is False:
        # No frame resolves inside the target, so the traceback alone cannot
        # attribute this failure.  TARGET_FAILURE now requires the positive
        # evidence of an assertion-only failure; anything else is a defect in
        # the model-written harness and can never be published as FALSE.
        if _assertion_only_failure(run):
            return FailureCategory.TARGET_FAILURE
        return FailureCategory.HARNESS_ERROR
    return FailureCategory.UNKNOWN


def adjudicate_install_failure(claim: Claim) -> Evaluation:
    """Environment never became healthy: every executable claim is INCONCLUSIVE."""
    return Evaluation(
        claim=claim,
        verdict=Verdict.INCONCLUSIVE,
        failure_category=FailureCategory.INSTALL_FAILURE,
        verdict_confidence=Confidence.LOW,
        rationale="Dependency installation failed; the claim was never executed.",
    )


def adjudicate_unsupported_repo(claim: Claim) -> Evaluation:
    """Pre-flight: the repository has no supported install manifest.

    Adjudicated before any harness is synthesized, so no model spend is wasted
    on claims that could never be executed.
    """
    return Evaluation(
        claim=claim,
        verdict=Verdict.INCONCLUSIVE,
        failure_category=FailureCategory.INSTALL_FAILURE,
        verdict_confidence=Confidence.LOW,
        rationale=(
            "Repository is neither pip-installable (no pyproject.toml/setup.py) nor "
            "npm-installable (no package.json); executable claims cannot be run."
        ),
    )


def adjudicate_harness_failure(claim: Claim, error: str) -> Evaluation:
    """The model could not produce a valid harness: fail gracefully."""
    return Evaluation(
        claim=claim,
        harness_error=error,
        verdict=Verdict.INCONCLUSIVE,
        failure_category=FailureCategory.HARNESS_FAILURE,
        verdict_confidence=Confidence.LOW,
        rationale="No valid harness could be generated; malformed model output is never executed.",
    )


def adjudicate(
    claim: Claim,
    harness_code: str,
    runs: list[ExecutionRun],
    package: str,
) -> Evaluation:
    """Map one claim's two execution runs to a verdict.  Judge conservatively."""
    if len(runs) != 2:
        raise ValueError("double execution requires exactly two runs")
    first, second = runs
    identical = _identical(first, second)

    evaluation = Evaluation(claim=claim, harness_code=harness_code, runs=runs)

    if first.timed_out or second.timed_out:
        evaluation.verdict = Verdict.INCONCLUSIVE
        evaluation.failure_category = FailureCategory.TIMEOUT
        evaluation.verdict_confidence = Confidence.LOW
        evaluation.rationale = "Execution hit the hard 120s timeout; timeouts are never FALSE."
        return evaluation

    if first.passed and second.passed:
        evaluation.verdict = Verdict.PROVEN
        evaluation.verdict_confidence = Confidence.HIGH if identical else Confidence.MEDIUM
        evaluation.rationale = "Both executions passed, including the control assertion."
        return evaluation

    if first.passed != second.passed:
        evaluation.verdict = Verdict.INCONCLUSIVE
        evaluation.failure_category = FailureCategory.UNKNOWN
        evaluation.verdict_confidence = Confidence.MEDIUM
        evaluation.rationale = "Executions disagreed (PASS/FAIL): nondeterministic evidence."
        return evaluation

    # Both runs failed: FALSE requires every strict condition to hold.
    category = _classify_failure(first, package)
    control_ok = first.control_passed is True and second.control_passed is True

    if not control_ok:
        evaluation.verdict = Verdict.INCONCLUSIVE
        evaluation.failure_category = category
        evaluation.verdict_confidence = Confidence.LOW
        evaluation.rationale = (
            "The control assertion failed, so the environment or harness is at "
            "fault; a failed control is never FALSE."
        )
        return evaluation

    if category == FailureCategory.TARGET_FAILURE:
        evaluation.verdict = Verdict.FALSE
        evaluation.failure_category = category
        evaluation.verdict_confidence = Confidence.HIGH if identical else Confidence.MEDIUM
        evaluation.rationale = (
            "Both executions failed, the control assertion passed, and the "
            "failure originates in the target package."
        )
        return evaluation

    evaluation.verdict = Verdict.INCONCLUSIVE
    evaluation.failure_category = category
    evaluation.verdict_confidence = Confidence.LOW
    if category == FailureCategory.HARNESS_ERROR:
        evaluation.rationale = (
            "Both executions failed, but test_claim died of a harness defect "
            "(a missing symbol, a wrong path, a bad call) rather than a failed "
            "assertion, and no traceback frame resolves inside the target. The "
            "claim was never actually tested, so this is never FALSE."
        )
    else:
        evaluation.rationale = (
            f"Both executions failed but the failure ({category.value}) cannot be "
            "attributed to the target package with confidence."
        )
    return evaluation
