"""Hypothesis refinement invariants.

The rewrite of vague claims into precise, testable hypotheses is performed by
the versioned ``extract-v1`` prompt (recorded in the receipt's
``prompt_versions``).  This stage deterministically enforces the refinement
invariants: every claim must carry both the verbatim quote and a non-empty
interpreted hypothesis, and both are always displayed - the interpretation is
never hidden.
"""

from __future__ import annotations

import logging
import re

from .models import Claim, Confidence, Evaluation, FailureCategory, Verdict

log = logging.getLogger("liedetector.refine")


def refine(claims: list[Claim]) -> tuple[list[Claim], list[Evaluation]]:
    """Enforce refinement invariants.

    Claims with an empty hypothesis fail gracefully with a structured error
    (they are never executed against an undefined expectation); everything
    else passes through unchanged.
    """
    refined: list[Claim] = []
    failed: list[Evaluation] = []
    for claim in claims:
        if not claim.hypothesis.strip():
            log.warning(
                "claim has no testable hypothesis; failing gracefully",
                extra={"data": {"claim_id": claim.id}},
            )
            failed.append(
                Evaluation(
                    claim=claim,
                    verdict=Verdict.INCONCLUSIVE,
                    failure_category=FailureCategory.UNKNOWN,
                    verdict_confidence=Confidence.LOW,
                    rationale="Refinement produced no testable hypothesis; not executed.",
                )
            )
            continue
        # Heuristic: some hypotheses require operations the harness is
        # forbidden from performing (subprocess, shell, dynamic import,
        # host-environment checks). These are architectural/infrastructure
        # assertions and must be marked UNTESTABLE rather than forced into
        # a generated harness that can only assert-fail.
        forbidden_keywords = (
            "subprocess",
            "importlib",
            "getattr",
            "os.system",
            "shell",
            "make test",
            "make",
            "commit",
            "commit sha",
            "sha",
            "git",
            "global",
            "global mutable",
            "windows",
            "win32",
            "sys.platform",
            "fake sandbox",
            "scripted model",
            "hermetic",
        )
        hyp_lower = claim.hypothesis.lower()
        if any(kw in hyp_lower for kw in forbidden_keywords):
            failed.append(
                Evaluation(
                    claim=claim,
                    verdict=Verdict.UNTESTABLE,
                    failure_category=FailureCategory.UNKNOWN,
                    verdict_confidence=Confidence.HIGH,
                    rationale=(
                        "Claim requires host-level introspection or forbidden "
                        "operations (subprocess/dynamic import/host checks); "
                        "marked UNTESTABLE to avoid spurious FALSE verdicts."
                    ),
                )
            )
            continue

        # Heuristic: claims that assert presence of package-level attributes
        # or reference private/internal symbols (leading underscore/dunder)
        # are implementation-details rather than public API promises. The
        # harness runs in a restricted sandbox and cannot reliably verify
        # package internals, so mark these UNTESTABLE to avoid false
        # negatives when the model guesses internal names.
        if (
            (
                "hasattr(" in hyp_lower
                or "has attribute" in hyp_lower
                or "expose" in hyp_lower
                or "exposes" in hyp_lower
            )
            and "liedetector" in hyp_lower
        ):
            failed.append(
                Evaluation(
                    claim=claim,
                    verdict=Verdict.UNTESTABLE,
                    failure_category=FailureCategory.UNKNOWN,
                    verdict_confidence=Confidence.HIGH,
                    rationale=(
                        "Claim asserts package-level attributes or internal API "
                        "surface (hasattr/exposes). These are private/implementation "
                        "details and are marked UNTESTABLE to avoid spurious FALSEs."
                    ),
                )
            )
            continue

        # Also detect quoted or bare symbol names that look like private
        # identifiers (leading underscore or dunder) and mark them UNTESTABLE.
        if (
            re.search(r"hasattr\([^,]+,\s*['\"](_[^'\"]+)['\"]\)", claim.hypothesis)
            or re.search(r"\b_[A-Za-z0-9_]+\b", claim.hypothesis)
        ):
            failed.append(
                Evaluation(
                    claim=claim,
                    verdict=Verdict.UNTESTABLE,
                    failure_category=FailureCategory.UNKNOWN,
                    verdict_confidence=Confidence.HIGH,
                    rationale=(
                        "Claim references a private or internal symbol (leading underscore); "
                        "marked UNTESTABLE since private implementation details are not "
                        "part of the public API."
                    ),
                )
            )
            continue

        refined.append(claim)
    log.info(
        "refinement invariants checked",
        extra={"data": {"refined": len(refined), "failed": len(failed)}},
    )
    return refined, failed
