"""Fallback paths that had no test, per EDGE_CASE_AUDIT section 4.

Each of these is a branch the pipeline takes only when something has already
gone wrong: a container that hangs, a Docker daemon that is not there, a
manifest that will not parse, an artifact missing from a receipt bundle. They
were correct by inspection and unexercised by the suite, which is exactly the
state in which a later refactor breaks one silently.

Unlike the other fixes on this branch these are coverage, not behaviour
changes: they pass against the pre-fix source too. That is the point -- they
pin behaviour that was previously only asserted by reading the code.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from liedetector import ecosystem
from liedetector.adjudicate import adjudicate
from liedetector.cli import main
from liedetector.ecosystem import Ecosystem, package_name
from liedetector.executor import DockerExecutor, docker_available
from liedetector.models import (
    Claim,
    ClaimType,
    Confidence,
    Evaluation,
    FailureCategory,
    Source,
    Verdict,
)
from liedetector.receipt import build_receipt
from liedetector.report import render_report

from .conftest import make_run


def _claim() -> Claim:
    return Claim(
        id="clm-x",
        source=Source(file="README.md", line=1, quote="q"),
        claim_type=ClaimType.DETERMINISTIC,
        hypothesis="h",
        interpretation_notes="n",
        confidence="high",
    )


# --- adjudicate.py: the UNKNOWN fall-through ---------------------------------


def test_unknown_category_when_claim_never_reported_a_result() -> None:
    """Both runs fail, the control passes, and test_claim reported nothing.

    The only verdict-producing branch the audit found with no test at all.
    ``claim_passed is None`` means the harness was collected but the claim
    test never ran, so there is nothing to attribute either way.
    """
    runs = [
        make_run(i, exit_code=1, stdout="", control_passed=True, claim_passed=None)
        for i in (1, 2)
    ]
    ev = adjudicate(_claim(), "harness", runs, "toylib")
    assert ev.failure_category == FailureCategory.UNKNOWN
    assert ev.verdict != Verdict.FALSE
    assert ev.verdict == Verdict.INCONCLUSIVE


# --- executor.py: the hard timeout -------------------------------------------


def test_container_timeout_is_reported_and_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 120s cap is a stated safety guarantee and was never exercised.

    A real hang cannot be provoked hermetically, so ``subprocess.run`` is made
    to raise the exception a real hang raises.
    """
    killed: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "kill"] or cmd[:3] == ["docker", "rm", "-f"]:
            killed.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise subprocess.TimeoutExpired(cmd, timeout=120, output=b"partial", stderr=b"")

    monkeypatch.setattr("liedetector.executor.subprocess.run", fake_run)

    ex = DockerExecutor("python:3.12-slim@sha256:" + "a" * 64)
    ex._env_dir = None
    result = ex.install(tmp_path)
    assert result.ok is False
    assert any(cmd[:2] == ["docker", "kill"] for cmd in killed), killed
    assert any(cmd[:3] == ["docker", "rm", "-f"] for cmd in killed), killed
    ex._env_dir = None


def test_timed_out_run_never_reaches_false() -> None:
    """The guarantee the timeout path exists to provide."""
    runs = [
        make_run(i, exit_code=-1, timed_out=True, control_passed=None, claim_passed=None)
        for i in (1, 2)
    ]
    ev = adjudicate(_claim(), "harness", runs, "toylib")
    assert ev.verdict != Verdict.FALSE
    assert ev.failure_category == FailureCategory.TIMEOUT


# --- executor.py: docker_available error handling ----------------------------


def test_docker_available_reports_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("No such file or directory: 'docker'")

    monkeypatch.setattr("liedetector.executor.subprocess.run", fake_run)
    ok, detail = docker_available()
    assert ok is False
    assert "docker" in detail


def test_docker_available_reports_hung_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd, timeout=30)

    monkeypatch.setattr("liedetector.executor.subprocess.run", fake_run)
    ok, detail = docker_available()
    assert ok is False
    assert detail


def test_docker_available_reports_daemon_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, "", "Cannot connect to the daemon\n")

    monkeypatch.setattr("liedetector.executor.subprocess.run", fake_run)
    ok, detail = docker_available()
    assert ok is False
    assert detail == "Cannot connect to the daemon"


# --- ecosystem.py: malformed manifests ---------------------------------------


def test_malformed_pyproject_falls_back_to_directory_name(tmp_path: Path) -> None:
    """A bad package name propagates into ``_traceback_in_target`` and weakens
    FALSE detection, so the fallback is worth pinning."""
    repo = tmp_path / "my-project"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project\nname = broken", encoding="utf-8")
    assert package_name(repo, Ecosystem.PYTHON) == "my_project"


def test_pyproject_without_a_name_falls_back(tmp_path: Path) -> None:
    repo = tmp_path / "my-project"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[build-system]\nrequires = []\n", encoding="utf-8")
    assert package_name(repo, Ecosystem.PYTHON) == "my_project"


# --- report.py: artifact missing from the bundle -----------------------------


def _minimal_receipt(with_harness: bool) -> dict[str, Any]:
    """A real receipt, built by the real builder so it cannot drift."""
    evaluation = Evaluation(
        claim=_claim(),
        harness_code="def test_control():\n    pass\n" if with_harness else None,
        verdict=Verdict.PROVEN,
        verdict_confidence=Confidence.HIGH,
        rationale="r",
    )
    return build_receipt(
        repo_url="https://example.invalid/r",
        commit_sha="0" * 40,
        timestamp_utc="2026-01-01T00:00:00Z",
        readme_sha256="0" * 64,
        install=None,
        evaluations=[evaluation],
    )


def test_report_renders_placeholder_for_a_missing_artifact(tmp_path: Path) -> None:
    """A receipt bundle whose harness file was deleted must still render.

    The report is a derived view; refusing to render would hide the rest of
    the evidence over one absent file.
    """
    html = render_report(_minimal_receipt(with_harness=True), "f" * 64, tmp_path)
    assert "missing artifact: harnesses/clm-x.py" in html
    assert "Truth Report" in html


def test_report_renders_when_no_harness_was_recorded(tmp_path: Path) -> None:
    html = render_report(_minimal_receipt(with_harness=False), "f" * 64, tmp_path)
    assert "Truth Report" in html


# --- cli.py: top-level error handling ----------------------------------------


def test_cli_reports_error_and_exits_two(tmp_path: Path, capsys: Any) -> None:
    """Any LieDetectorError reaching main() prints `error:` and exits 2."""
    code = main(["verify", str(tmp_path / "does-not-exist.json")])
    assert code == 2
    assert capsys.readouterr().err.startswith("error:")


def test_cli_reports_invalid_receipt_json(tmp_path: Path, capsys: Any) -> None:
    bad = tmp_path / "verification_receipt.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["verify", str(bad)]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_badge_reports_missing_receipt(tmp_path: Path, capsys: Any) -> None:
    assert main(["badge", str(tmp_path / "nope.json")]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_run_rejects_unsupported_repo(tmp_path: Path, capsys: Any) -> None:
    """Pre-flight scope check: no supported manifest, no model spend."""
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / "README.md").write_text("# plain\n", encoding="utf-8")
    (repo / "package.json").unlink(missing_ok=True)
    assert ecosystem.detect_ecosystem(repo) is None
    assert package_name(repo, None) == "plain"


def test_badge_json_written_by_cli_is_valid(tmp_path: Path) -> None:
    receipt = tmp_path / "verification_receipt.json"
    receipt.write_text(json.dumps(_minimal_receipt(with_harness=False)), encoding="utf-8")
    assert main(["badge", str(receipt)]) == 0
    badge = json.loads((tmp_path / "badge.json").read_text(encoding="utf-8"))
    assert badge["schemaVersion"] == 1
    assert badge["message"] == "1 proven, 0 false"
