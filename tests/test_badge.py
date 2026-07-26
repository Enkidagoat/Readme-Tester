"""Unit tests for the shields.io badge emitter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liedetector.badge import (
    BADGE_LABEL,
    BADGE_NAME,
    badge_from_receipt_file,
    build_badge,
    write_badge,
)
from liedetector.utils import LieDetectorError


def _receipt(proven: int = 0, false: int = 0, inconclusive: int = 0, untestable: int = 0) -> dict:
    return {
        "verdict_tally": {
            "PROVEN": proven,
            "FALSE": false,
            "INCONCLUSIVE": inconclusive,
            "UNTESTABLE": untestable,
        }
    }


class TestBuildBadge:
    def test_all_proven_is_brightgreen(self) -> None:
        badge = build_badge(_receipt(proven=3))
        assert badge == {
            "schemaVersion": 1,
            "label": BADGE_LABEL,
            "message": "3 proven, 0 false",
            "color": "brightgreen",
        }

    def test_any_false_is_red(self) -> None:
        badge = build_badge(_receipt(proven=3, false=1))
        assert badge["color"] == "red"
        assert badge["message"] == "3 proven, 1 false"

    def test_inconclusive_is_yellow_and_shown(self) -> None:
        badge = build_badge(_receipt(proven=2, inconclusive=1))
        assert badge["color"] == "yellow"
        assert badge["message"] == "2 proven, 0 false, 1 inconclusive"

    def test_false_outranks_inconclusive(self) -> None:
        badge = build_badge(_receipt(proven=1, false=1, inconclusive=1))
        assert badge["color"] == "red"

    def test_nothing_proven_is_lightgrey(self) -> None:
        badge = build_badge(_receipt(untestable=4))
        assert badge["color"] == "lightgrey"
        assert badge["message"] == "0 proven, 0 false"

    def test_untestable_never_affects_color(self) -> None:
        assert build_badge(_receipt(proven=2, untestable=9))["color"] == "brightgreen"

    def test_missing_tally_raises(self) -> None:
        with pytest.raises(LieDetectorError):
            build_badge({"tool_version": "1.0"})


class TestWriteBadge:
    def test_round_trip_and_byte_stability(self, tmp_path: Path) -> None:
        badge = build_badge(_receipt(proven=1))
        out = write_badge(badge, tmp_path / BADGE_NAME)
        first = out.read_bytes()
        write_badge(badge, out)
        assert out.read_bytes() == first
        assert json.loads(first) == badge
        assert first.endswith(b"\n") and b"\r" not in first

    def test_badge_from_receipt_file(self, tmp_path: Path) -> None:
        receipt_path = tmp_path / "verification_receipt.json"
        receipt_path.write_text(json.dumps(_receipt(proven=2, false=1)), encoding="utf-8")
        written, badge = badge_from_receipt_file(receipt_path)
        assert written == tmp_path / BADGE_NAME
        assert json.loads(written.read_text(encoding="utf-8")) == badge
        assert badge["color"] == "red"

    def test_missing_receipt_raises(self, tmp_path: Path) -> None:
        with pytest.raises(LieDetectorError):
            badge_from_receipt_file(tmp_path / "nope.json")

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "verification_receipt.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(LieDetectorError):
            badge_from_receipt_file(bad)
