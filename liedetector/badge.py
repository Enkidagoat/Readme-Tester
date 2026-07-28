"""Badge emitter: derive a shields.io endpoint badge from a verification receipt.

The badge is a derived view of the receipt, exactly like the HTML report: it
is computed from ``verdict_tally`` and never feeds back into the hash chain.
Anyone holding the receipt can recompute the badge with ``liedetector badge``
and confirm it matches what a repository displays.

The output follows the shields.io endpoint schema
(https://shields.io/badges/endpoint-badge): publish ``badge.json`` at any
public URL and embed it via ``https://img.shields.io/endpoint?url=<url>``.
Only documented schema fields are emitted so shields.io never rejects it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .utils import LieDetectorError

BADGE_NAME = "badge.json"
BADGE_LABEL = "truth report"


def build_badge(receipt: dict[str, Any]) -> dict[str, Any]:
    """Compute shields.io endpoint JSON from a receipt's verdict tally.

    Color policy mirrors the adjudicator's conservatism:

    - any ``FALSE``               -> red (a claim is disproven)
    - any ``INCONCLUSIVE``        -> yellow (evidence is incomplete)
    - no ``PROVEN`` at all        -> lightgrey (nothing was demonstrated)
    - otherwise                   -> brightgreen (proven, nothing false)

    ``INCONCLUSIVE`` is tested before ``proven == 0`` so that a run which
    tried and could not conclude renders yellow rather than grey.  Grey means
    "no evidence either way"; a run with inconclusive results has evidence,
    it just does not settle the question.

    ``UNTESTABLE`` claims never affect the *color*: they were never executed,
    so they can neither strengthen nor weaken the verdict.  They are always
    reported in the *message*, because a badge that omits them cannot be told
    apart from one where those claims were verified.  A README with 1 proven
    and 50 unexecuted claims is not the same result as a README with 1 proven
    claim, and the badge has to say so.
    """
    tally = receipt.get("verdict_tally")
    if not isinstance(tally, dict):
        raise LieDetectorError(
            "receipt has no verdict_tally; is this a verification_receipt.json?"
        )
    proven = int(tally.get("PROVEN", 0))
    false = int(tally.get("FALSE", 0))
    inconclusive = int(tally.get("INCONCLUSIVE", 0))
    untestable = int(tally.get("UNTESTABLE", 0))

    parts = [f"{proven} proven", f"{false} false"]
    if inconclusive:
        parts.append(f"{inconclusive} inconclusive")
    if untestable:
        parts.append(f"{untestable} untestable")
    message = ", ".join(parts)

    if false:
        color = "red"
    elif inconclusive:
        color = "yellow"
    elif proven == 0:
        color = "lightgrey"
    else:
        color = "brightgreen"

    return {
        "schemaVersion": 1,
        "label": BADGE_LABEL,
        "message": message,
        "color": color,
    }


def write_badge(badge: dict[str, Any], out_path: Path) -> Path:
    """Write badge JSON as canonical bytes (sorted keys, LF, UTF-8)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(badge, sort_keys=True, separators=(",", ":")) + "\n"
    out_path.write_bytes(text.encode("utf-8"))
    return out_path


def badge_from_receipt_file(
    receipt_path: Path, out_path: Path | None = None
) -> tuple[Path, dict[str, Any]]:
    """Read a receipt file, build its badge, write it next to the receipt by default."""
    if not receipt_path.is_file():
        raise LieDetectorError(f"receipt not found: {receipt_path}")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LieDetectorError(f"receipt is not valid JSON: {exc}") from exc
    badge = build_badge(receipt)
    target = out_path if out_path is not None else receipt_path.parent / BADGE_NAME
    return write_badge(badge, target), badge
