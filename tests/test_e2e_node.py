"""End-to-end Node test against a real Docker sandbox.

Skipped automatically when no Docker daemon is reachable.  When Docker is
present this exercises the real npm install phase, the static ESM runner,
double execution, adjudication and receipt verification against the bundled
Node toy repository, with a scripted LLM (no live API credential needed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from liedetector.cli import run_pipeline
from liedetector.executor import DockerExecutor, docker_available
from liedetector.receipt import verify_receipt

from .conftest import FakeLLM, extraction_response, harness_response

pytestmark = pytest.mark.docker

ADD_HARNESS_JS = """\
// Verifies: add(2, 3) === 5.

export async function test_control() {
  const fs = await import("node:fs/promises");
  JSON.parse(await fs.readFile("/repo/package.json", "utf8"));
}

export async function test_claim() {
  const assert = await import("node:assert/strict");
  const lib = await import("/env/app/index.mjs");
  assert.equal(lib.add(2, 3), 5);
}
"""

LICENSE_HARNESS_JS = """\
// Verifies: a file named LICENSE exists at the repository root.

export async function test_control() {
  const fs = await import("node:fs/promises");
  JSON.parse(await fs.readFile("/repo/package.json", "utf8"));
}

export async function test_claim() {
  const fs = await import("node:fs/promises");
  await fs.access("/repo/LICENSE");
}
"""


@pytest.fixture(autouse=True)
def _require_docker() -> None:
    ok, detail = docker_available()
    if not ok:
        pytest.skip(f"docker unavailable: {detail}")


def test_e2e_node_real_sandbox(tmp_path: Path, toy_repo_js_dir: Path) -> None:
    llm = FakeLLM(
        [
            extraction_response(
                [
                    {"quote": "`add(2, 3)` returns `5`.",
                     "claim_type": "deterministic", "hypothesis": "add(2,3)===5"},
                    {"quote": "The repository ships a LICENSE file at its root.",
                     "claim_type": "deterministic",
                     "hypothesis": "a LICENSE file exists at the repo root"},
                ]
            ),
            harness_response(ADD_HARNESS_JS),
            harness_response(LICENSE_HARNESS_JS),
        ]
    )
    result = run_pipeline(
        str(toy_repo_js_dir),
        llm=llm,
        executor=DockerExecutor(),
        receipts_dir=tmp_path / "receipts",
        reports_dir=tmp_path / "reports",
        harnesses_dir=tmp_path / "harnesses",
        allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    # add(2,3)===5 is genuinely true; the LICENSE file genuinely does not exist.
    assert result.verdict_tally["PROVEN"] == 1
    assert result.verdict_tally["FALSE"] == 1
    checks = verify_receipt(result.receipt_path)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
