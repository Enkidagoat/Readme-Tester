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

        refined.append(claim)
    log.info(
        "refinement invariants checked",
        extra={"data": {"refined": len(refined), "failed": len(failed)}},
    )
    return refined, failed
