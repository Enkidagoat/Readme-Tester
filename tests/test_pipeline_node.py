"""Hermetic pipeline tests for the Node ecosystem path.

Same scripted FakeLLM + FakeExecutor approach as ``test_pipeline``: no
Docker, no live model.  The real Node sandbox is exercised by the opt-in
``test_e2e_node`` when a daemon is available.
"""

from __future__ import annotations

import json
from pathlib import Path

from liedetector.cli import run_pipeline
from liedetector.receipt import verify_receipt

from .conftest import (
    FAILING_HARNESS_JS,
    PASSING_HARNESS_JS,
    FakeExecutor,
    FakeLLM,
    extraction_response,
    git_init_repo,
    harness_response,
)

TOY_CLAIMS_JS = [
    {"quote": "`add(2, 3)` returns `5`.", "claim_type": "deterministic",
     "hypothesis": "add(2, 3) === 5"},
    {"quote": "The repository ships a LICENSE file at its root.",
     "claim_type": "deterministic",
     "hypothesis": "a file named LICENSE exists at the repository root"},
    {"quote": "toylib-js is blazing fast, processing millions of strings per second.",
     "claim_type": "behavioral-proxy", "hypothesis": "throughput is high",
     "suggested_strategy": "benchmark it"},
]


def _scripted_llm() -> FakeLLM:
    return FakeLLM(
        [
            extraction_response(TOY_CLAIMS_JS),
            harness_response(PASSING_HARNESS_JS.format(hypothesis="add")),
            harness_response(FAILING_HARNESS_JS.format(hypothesis="license")),
        ]
    )


def _run(tmp_path: Path, repo_dir: Path) -> tuple[Path, dict]:
    result = run_pipeline(
        str(repo_dir),
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


def test_node_pipeline_tally_and_ecosystem(tmp_path: Path, toy_repo_js_dir: Path) -> None:
    _, receipt = _run(tmp_path, toy_repo_js_dir)
    assert receipt["ecosystem"] == "node"
    assert receipt["runtime"] == "node-22"
    tally = receipt["verdict_tally"]
    assert tally["PROVEN"] == 1
    assert tally["FALSE"] == 1
    assert tally["UNTESTABLE"] == 1
    assert tally["INCONCLUSIVE"] == 0


def test_node_pipeline_writes_mjs_harnesses(tmp_path: Path, toy_repo_js_dir: Path) -> None:
    receipt_path, receipt = _run(tmp_path, toy_repo_js_dir)
    harness_paths = [c["harness_path"] for c in receipt["claims"] if c["harness_path"]]
    assert harness_paths
    assert all(p.endswith(".mjs") for p in harness_paths)
    for rel in harness_paths:
        assert (receipt_path.parent / rel).is_file()


def test_node_pipeline_receipt_verifies(tmp_path: Path, toy_repo_js_dir: Path) -> None:
    receipt_path, _ = _run(tmp_path, toy_repo_js_dir)
    checks = verify_receipt(receipt_path)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_unsupported_repo_skips_synthesis(tmp_path: Path) -> None:
    """No manifest at all: claims survive, harness synthesis is never attempted."""
    repo = tmp_path / "norepo"
    repo.mkdir()
    (repo / "README.md").write_text(
        "# norepo\n\n`add(2, 3)` returns `5`.\n", encoding="utf-8"
    )
    git_init_repo(repo)
    llm = FakeLLM(
        [
            extraction_response(
                [{"quote": "`add(2, 3)` returns `5`.", "claim_type": "deterministic",
                  "hypothesis": "add(2, 3) == 5"}]
            )
        ]
    )
    result = run_pipeline(
        str(repo),
        llm=llm,
        executor=FakeExecutor(),
        receipts_dir=tmp_path / "receipts",
        reports_dir=tmp_path / "reports",
        harnesses_dir=tmp_path / "harnesses",
        allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    # Extraction is the only model call; no harness synthesis was attempted.
    assert len(llm.calls) == 1
    assert result.verdict_tally["INCONCLUSIVE"] == 1
    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert receipt["ecosystem"] == "unsupported"
    checks = verify_receipt(result.receipt_path)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
