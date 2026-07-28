"""The Node runner's control assertion, driven by executing the real runner.

EDGE_CASE_AUDIT finding #2: the control assertion is the entire basis for
trusting a ``FALSE``, and on the Node path it only read ``/repo/package.json``.
That passes against a completely broken ``npm install`` -- it is a mount
check, not a health check -- yet ``adjudicate()`` accepts it as proof the
environment is healthy and unlocks ``FALSE``.

These tests run ``JS_RUNNER_SOURCE`` under the real ``node`` binary against
fixture application trees, so they exercise the JavaScript the container
actually executes rather than a Python re-description of it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from liedetector.executor import (
    JS_RUNNER_NAME,
    JS_RUNNER_SOURCE,
    parse_pytest_results,
)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node binary not available"
)

#: The control pattern the harness prompt specifies: reads the repo mount and
#: nothing else.  This is exactly the harness the audit showed was too weak.
HARNESS = """\
// Verifies: the package exposes greet()
export async function test_control() {
  const fs = await import("node:fs/promises");
  JSON.parse(await fs.readFile(REPO_MANIFEST, "utf8"));
}

export async function test_claim() {
  const assert = await import("node:assert/strict");
  assert.ok(true);
}
"""


def _run_harness(tmp_path: Path, app_root: Path, repo_manifest: Path) -> tuple[
    bool | None, bool | None, str
]:
    """Execute the real runner over a harness; return parsed control/claim."""
    harness_dir = tmp_path / "harness"
    harness_dir.mkdir(exist_ok=True)
    (harness_dir / JS_RUNNER_NAME).write_text(JS_RUNNER_SOURCE, encoding="utf-8")
    harness = harness_dir / "clm-x.mjs"
    harness.write_text(
        HARNESS.replace("REPO_MANIFEST", json.dumps(str(repo_manifest))),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", str(harness_dir / JS_RUNNER_NAME), str(harness), str(app_root)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    control, claim = parse_pytest_results(proc.stdout)
    return control, claim, proc.stderr


def _repo(tmp_path: Path) -> Path:
    """A pristine read-only-style repo mount; always intact in these tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(
        json.dumps({"name": "toyapp", "version": "1.0.0"}), encoding="utf-8"
    )
    return repo / "package.json"


def _app(tmp_path: Path, *, manifest: dict[str, object], entry: str | None) -> Path:
    """An installed application tree at a stand-in for ``/env/app``."""
    app = tmp_path / "app"
    app.mkdir()
    (app / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    if entry is not None:
        target = app / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export function greet() { return 'hi'; }\n", encoding="utf-8")
    return app


def _install_dependency(app: Path, name: str = "left-pad") -> None:
    """Populate node_modules the way a successful npm install would."""
    package = app / "node_modules" / name
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"name": name, "version": "1.0.0"}), encoding="utf-8"
    )


def test_healthy_install_passes_the_control(tmp_path: Path) -> None:
    app = _app(
        tmp_path,
        manifest={
            "name": "toyapp",
            "type": "module",
            "main": "index.js",
            "dependencies": {"left-pad": "^1.0.0"},
        },
        entry="index.js",
    )
    _install_dependency(app)
    control, claim, stderr = _run_harness(tmp_path, app, _repo(tmp_path))
    assert control is True, stderr
    assert claim is True


def test_empty_node_modules_fails_the_control(tmp_path: Path) -> None:
    """npm install exited 0 but populated nothing: the tree is broken."""
    app = _app(
        tmp_path,
        manifest={
            "name": "toyapp",
            "type": "module",
            "main": "index.js",
            "dependencies": {"left-pad": "^1.0.0"},
        },
        entry="index.js",
    )
    (app / "node_modules").mkdir()
    control, claim, stderr = _run_harness(tmp_path, app, _repo(tmp_path))
    assert control is False, stderr
    assert "node_modules is empty" in stderr


def test_missing_node_modules_fails_the_control(tmp_path: Path) -> None:
    app = _app(
        tmp_path,
        manifest={
            "name": "toyapp",
            "type": "module",
            "main": "index.js",
            "dependencies": {"left-pad": "^1.0.0"},
        },
        entry="index.js",
    )
    control, _, stderr = _run_harness(tmp_path, app, _repo(tmp_path))
    assert control is False, stderr
    assert "node_modules is unreadable" in stderr


def test_missing_entry_point_fails_the_control(tmp_path: Path) -> None:
    """The manifest declares an entry point the install never produced."""
    app = _app(
        tmp_path,
        manifest={"name": "toyapp", "type": "module", "main": "dist/index.js"},
        entry=None,
    )
    control, _, stderr = _run_harness(tmp_path, app, _repo(tmp_path))
    assert control is False, stderr
    assert "environment health check failed" in stderr


def test_unimportable_entry_point_fails_the_control(tmp_path: Path) -> None:
    """The entry point exists but does not load -- same as a Python control
    failing on a package whose ``__init__`` raises."""
    app = _app(
        tmp_path,
        manifest={"name": "toyapp", "type": "module", "main": "index.js"},
        entry="index.js",
    )
    (app / "index.js").write_text("throw new Error('boom');\n", encoding="utf-8")
    control, _, stderr = _run_harness(tmp_path, app, _repo(tmp_path))
    assert control is False, stderr
    assert "boom" in stderr


def test_missing_app_root_fails_the_control(tmp_path: Path) -> None:
    """Nothing was ever copied into the volume."""
    control, _, stderr = _run_harness(tmp_path, tmp_path / "nope", _repo(tmp_path))
    assert control is False, stderr


def test_entry_point_resolved_from_exports_map(tmp_path: Path) -> None:
    app = _app(
        tmp_path,
        manifest={
            "name": "toyapp",
            "type": "module",
            "exports": {".": {"import": "./lib/main.js"}},
        },
        entry="lib/main.js",
    )
    control, _, stderr = _run_harness(tmp_path, app, _repo(tmp_path))
    assert control is True, stderr


def test_broken_install_is_never_false_end_to_end(tmp_path: Path) -> None:
    """The finding, stated as the verdict it produces.

    A broken install with a failing claim must adjudicate INCONCLUSIVE. Before
    the runner owned the control this same shape yielded a passing control,
    which is what unlocked FALSE.
    """
    from liedetector.adjudicate import adjudicate
    from liedetector.models import Claim, ClaimType, Source, Verdict

    app = _app(
        tmp_path,
        manifest={
            "name": "toyapp",
            "type": "module",
            "main": "index.js",
            "dependencies": {"left-pad": "^1.0.0"},
        },
        entry="index.js",
    )
    (app / "node_modules").mkdir()
    control, claim, stderr = _run_harness(tmp_path, app, _repo(tmp_path))
    assert control is False, stderr

    from .conftest import make_run

    runs = [
        make_run(i, exit_code=1, stderr=stderr, control_passed=control, claim_passed=False)
        for i in (1, 2)
    ]
    claim_record = Claim(
        id="clm-x",
        source=Source(file="README.md", line=1, quote="q"),
        claim_type=ClaimType.DETERMINISTIC,
        hypothesis="h",
        interpretation_notes="n",
        confidence="high",
    )
    ev = adjudicate(claim_record, HARNESS, runs, "toyapp")
    assert ev.verdict != Verdict.FALSE
    assert ev.verdict == Verdict.INCONCLUSIVE
