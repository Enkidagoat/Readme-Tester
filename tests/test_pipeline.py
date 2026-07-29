"""Integration tests across stages, plus golden receipt/report snapshots.

The pipeline is driven with a scripted FakeLLM and FakeExecutor so it is
byte-stable given fixed inputs.  The whole flow runs without Docker or a live
model; the Docker executor itself is exercised only by the opt-in
``test_e2e_demo`` when a daemon is available.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from liedetector.cli import run_pipeline
from liedetector.receipt import verify_receipt

from .conftest import (
    FAILING_HARNESS,
    HALLUCINATING_HARNESS,
    PASSING_HARNESS,
    FakeExecutor,
    FakeLLM,
    extraction_response,
    harness_response,
)

# Claims that mirror the toy repo README: one true, one false, one env-bound,
# one behavioral-proxy, one aspirational.
TOY_CLAIMS = [
    {"quote": "`toylib.add(2, 3)` returns `5`.", "claim_type": "deterministic",
     "hypothesis": "toylib.add(2, 3) == 5"},
    {"quote": '`toylib.count_words("a  b")` returns `2`.', "claim_type": "deterministic",
     "hypothesis": "count_words('a  b') == 2"},
    {"quote": "pip install toylib", "claim_type": "environment-bound",
     "hypothesis": "the package installs via pip"},
    {"quote": "toylib is blazing fast, processing millions of strings per second.",
     "claim_type": "behavioral-proxy", "hypothesis": "throughput is high",
     "suggested_strategy": "benchmark it"},
    {"quote": "Rust bindings are planned for a future release.",
     "claim_type": "aspirational", "hypothesis": "rust bindings will exist",
     "suggested_strategy": "check roadmap"},
]


def _scripted_llm() -> FakeLLM:
    # Extraction, then one harness per executable claim (3 of them): add
    # passes, count_words fails, install/env-bound passes.
    return FakeLLM(
        [
            extraction_response(TOY_CLAIMS),
            harness_response(PASSING_HARNESS.format(package="toylib", hypothesis="add")),
            harness_response(FAILING_HARNESS.format(package="toylib", hypothesis="count")),
            harness_response(PASSING_HARNESS.format(package="toylib", hypothesis="install")),
        ]
    )


def _run(tmp_path: Path, toy_repo_dir: Path) -> tuple[Path, dict[str, Any]]:
    result = run_pipeline(
        str(toy_repo_dir),
        llm=_scripted_llm(),
        executor=FakeExecutor(),
        receipts_dir=tmp_path / "receipts",
        reports_dir=tmp_path / "reports",
        harnesses_dir=tmp_path / "harnesses",
        allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    return result.receipt_path, receipt


def test_pipeline_produces_expected_tally(tmp_path: Path, toy_repo_dir: Path) -> None:
    _, receipt = _run(tmp_path, toy_repo_dir)
    tally = receipt["verdict_tally"]
    assert tally["PROVEN"] == 2  # add + install
    assert tally["FALSE"] == 1  # count_words
    assert tally["UNTESTABLE"] == 2  # behavioral-proxy + aspirational
    assert tally["INCONCLUSIVE"] == 0


def test_hallucinated_symbol_never_reaches_false_through_the_pipeline(
    tmp_path: Path, toy_repo_dir: Path
) -> None:
    """EDGE_CASE_AUDIT finding #1, driven through the real ``run_pipeline``.

    The model writes a harness against an API symbol that does not exist. The
    repository is blameless, so no claim may be published FALSE; the run is
    recorded as a harness defect instead.
    """
    llm = FakeLLM(
        [
            extraction_response(TOY_CLAIMS[:1]),
            harness_response(
                HALLUCINATING_HARNESS.format(package="toylib", hypothesis="add")
            ),
        ]
    )
    result = run_pipeline(
        str(toy_repo_dir), llm=llm, executor=FakeExecutor(),
        receipts_dir=tmp_path / "receipts", reports_dir=tmp_path / "reports",
        harnesses_dir=tmp_path / "harnesses", allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert receipt["verdict_tally"]["FALSE"] == 0
    assert receipt["verdict_tally"]["INCONCLUSIVE"] == 1
    (entry,) = receipt["claims"]
    assert entry["failure_category"] == "HARNESS_ERROR"
    assert entry["verdict_confidence"] == "LOW"


def test_pipeline_receipt_verifies(tmp_path: Path, toy_repo_dir: Path) -> None:
    receipt_path, _ = _run(tmp_path, toy_repo_dir)
    checks = verify_receipt(receipt_path)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_pipeline_is_byte_stable(tmp_path: Path, toy_repo_dir: Path) -> None:
    """Same commit + same scripted inputs -> byte-identical receipt bytes."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    r1 = run_pipeline(
        str(toy_repo_dir), llm=_scripted_llm(), executor=FakeExecutor(),
        receipts_dir=a / "receipts", reports_dir=a / "reports",
        harnesses_dir=a / "harnesses", allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    r2 = run_pipeline(
        str(toy_repo_dir), llm=_scripted_llm(), executor=FakeExecutor(),
        receipts_dir=b / "receipts", reports_dir=b / "reports",
        harnesses_dir=b / "harnesses", allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    assert r1.receipt_path.read_bytes() == r2.receipt_path.read_bytes()
    assert r1.receipt_hash == r2.receipt_hash


def test_report_embeds_receipt_hash(tmp_path: Path, toy_repo_dir: Path) -> None:
    result = run_pipeline(
        str(toy_repo_dir), llm=_scripted_llm(), executor=FakeExecutor(),
        receipts_dir=tmp_path / "receipts", reports_dir=tmp_path / "reports",
        harnesses_dir=tmp_path / "harnesses", allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    html = result.report_path.read_text(encoding="utf-8")
    assert result.receipt_hash in html
    assert "Truth Report" in html


@pytest.mark.skipif(
    shutil.which("wkhtmltopdf") is None, reason="wkhtmltopdf not installed"
)
def test_render_pdf_true_produces_a_pdf_file(tmp_path: Path, toy_repo_dir: Path) -> None:
    result = run_pipeline(
        str(toy_repo_dir), llm=_scripted_llm(), executor=FakeExecutor(),
        receipts_dir=tmp_path / "receipts", reports_dir=tmp_path / "reports",
        harnesses_dir=tmp_path / "harnesses", allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z", render_pdf=True,
    )
    assert result.pdf_path is not None
    assert result.pdf_path.is_file()
    assert result.pdf_path.suffix == ".pdf"
    assert result.pdf_path.read_bytes().startswith(b"%PDF")


def test_render_pdf_false_by_default(tmp_path: Path, toy_repo_dir: Path) -> None:
    result = run_pipeline(
        str(toy_repo_dir), llm=_scripted_llm(), executor=FakeExecutor(),
        receipts_dir=tmp_path / "receipts", reports_dir=tmp_path / "reports",
        harnesses_dir=tmp_path / "harnesses", allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    assert result.pdf_path is None


def test_install_failure_makes_claims_inconclusive(tmp_path: Path, toy_repo_dir: Path) -> None:
    result = run_pipeline(
        str(toy_repo_dir), llm=_scripted_llm(), executor=FakeExecutor(install_ok=False),
        receipts_dir=tmp_path / "receipts", reports_dir=tmp_path / "reports",
        harnesses_dir=tmp_path / "harnesses", allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    assert result.verdict_tally["INCONCLUSIVE"] == 3  # the three executable claims
    assert result.verdict_tally["PROVEN"] == 0
