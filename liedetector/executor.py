"""Execution sandbox: containerised install phase plus double execution.

Security model (one execution path, no demo mode):

- image pinned by digest, never by tag; the resolved digest is recorded in
  the receipt,
- non-root user, read-only repository mount, tmpfs writable directory,
- network enabled only during dependency installation, disabled during
  execution (``--network none``),
- 1 CPU, 1 GB RAM, PID limit, all Linux capabilities dropped,
  ``no-new-privileges``; no privileged mode, no Docker socket, no host
  networking, no mounted secrets,
- hard 120 second timeout per execution -> ``INCONCLUSIVE``, never ``FALSE``,
- containers and temporary filesystems are always cleaned up.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Protocol

from . import DOCKER_IMAGE, DOCKER_IMAGE_NODE
from .ecosystem import Ecosystem, detect_ecosystem
from .models import ExecutionRun, InstallResult
from .utils import LieDetectorError

log = logging.getLogger("liedetector.executor")

EXECUTION_TIMEOUT_S = 120
INSTALL_TIMEOUT_S = 600

_RESULT_LINE = re.compile(r"::(test_control|test_claim)\b.*\b(PASSED|FAILED|ERROR)")

#: Where the install phase puts the npm-installed copy of the repository.
JS_APP_ROOT = "/env/app"

#: Static, versioned ESM runner for Node harnesses.  It is written by the
#: tool, never by the model: the model-generated harness only *exports*
#: ``test_control`` and ``test_claim``; this runner imports and executes them,
#: emitting the same ``<name>::test_control PASSED`` result lines pytest -v
#: produces so one parser serves both ecosystems.
#:
#: The control assertion is the entire basis for trusting a ``FALSE``, so the
#: runner owns it rather than the model.  ``environmentHealthCheck`` is
#: tool-authored and unconditional; the harness's own ``test_control`` runs
#: after it and can only ever make the control stricter, never weaker.  This
#: is what stops a broken ``npm install`` from producing a passing control.
JS_RUNNER_NAME = "_runner.mjs"
JS_RUNNER_SOURCE = """\
// Lie Detector Node harness runner (static, versioned; not model-generated).
const harnessPath = process.argv[2];
const appRoot = process.argv[3] || "/env/app";

// The entry point the manifest declares, or null when it declares none.
function entryPoint(pkg) {
  const exp = pkg.exports;
  if (typeof exp === "string") return exp;
  if (exp && typeof exp === "object") {
    const dot = exp["."] !== undefined ? exp["."] : exp;
    if (typeof dot === "string") return dot;
    if (dot && typeof dot === "object") {
      for (const key of ["import", "module", "default", "require"]) {
        if (typeof dot[key] === "string") return dot[key];
      }
    }
  }
  if (typeof pkg.main === "string") return pkg.main;
  return null;
}

// Tool-authored control: prove the *installed application* is healthy, not
// merely that the read-only repo mount is readable.  Mirrors what `import
// PACKAGE` proves on the Python path.
async function environmentHealthCheck() {
  const fs = await import("node:fs/promises");
  const path = await import("node:path");

  // 1. The install phase copied the repository into the volume.
  const pkg = JSON.parse(
    await fs.readFile(path.join(appRoot, "package.json"), "utf8")
  );

  // 2. If the manifest declares dependencies, npm install must have
  //    populated them. A missing or empty node_modules is a broken tree.
  const declared = Object.keys({
    ...(pkg.dependencies || {}),
    ...(pkg.devDependencies || {}),
  });
  if (declared.length > 0) {
    let installed;
    try {
      installed = await fs.readdir(path.join(appRoot, "node_modules"));
    } catch (err) {
      throw new Error(
        `${declared.length} dependencies declared but node_modules is ` +
          `unreadable: ${err.message}`
      );
    }
    if (installed.filter((name) => !name.startsWith(".")).length === 0) {
      throw new Error(
        `${declared.length} dependencies declared but node_modules is empty`
      );
    }
  }

  // 3. The declared entry point must exist and import.
  const entry = entryPoint(pkg);
  if (entry) {
    const target = path.join(appRoot, entry);
    await fs.stat(target);
    await import(target);
  }
}

let mod;
try {
  mod = await import(harnessPath);
} catch (err) {
  console.log(`${harnessPath}::test_control ERROR`);
  console.log(`${harnessPath}::test_claim ERROR`);
  console.error("harness import failed:", (err && err.stack) || err);
  process.exit(2);
}

let controlOk = true;
try {
  await environmentHealthCheck();
} catch (err) {
  controlOk = false;
  console.error("environment health check failed:", (err && err.stack) || err);
}
if (controlOk) {
  try {
    if (typeof mod.test_control !== "function") {
      throw new Error("harness does not export a function named test_control");
    }
    await mod.test_control();
  } catch (err) {
    controlOk = false;
    console.error("test_control failed:", (err && err.stack) || err);
  }
}
console.log(`${harnessPath}::test_control ${controlOk ? "PASSED" : "FAILED"}`);

let claimOk = true;
try {
  if (typeof mod.test_claim !== "function") {
    throw new Error("harness does not export a function named test_claim");
  }
  await mod.test_claim();
} catch (err) {
  claimOk = false;
  console.error("test_claim failed:", (err && err.stack) || err);
}
console.log(`${harnessPath}::test_claim ${claimOk ? "PASSED" : "FAILED"}`);

process.exit(controlOk && claimOk ? 0 : 1);
"""


class Executor(Protocol):
    """Interface the pipeline uses; tests inject a fake implementation."""

    @property
    def image_digest(self) -> str: ...

    def install(self, repo_path: Path) -> InstallResult:
        """Install the repository package into the sandbox environment."""
        ...

    def run_harness(self, harness_path: Path, run_index: int) -> ExecutionRun:
        """Execute one harness once inside the locked-down sandbox."""
        ...

    def cleanup(self) -> None:
        """Remove any temporary environment state."""
        ...


def parse_pytest_results(stdout: str) -> tuple[bool | None, bool | None]:
    """Parse harness output into (control_passed, claim_passed).

    Matches the ``<file>::test_control PASSED`` lines that both ``pytest -v``
    and the Node runner emit.  ``None`` means the corresponding test never
    reported a result (e.g. a collection error), which adjudication treats
    conservatively.
    """
    control: bool | None = None
    claim: bool | None = None
    for match in _RESULT_LINE.finditer(stdout):
        outcome = match.group(2) == "PASSED"
        if match.group(1) == "test_control":
            control = outcome
        else:
            claim = outcome
    return control, claim


class DockerExecutor:
    """Real sandbox backed by Docker with the pinned-digest image."""

    def __init__(self, image: str | None = None) -> None:
        self._image_override = image
        self.image = image or DOCKER_IMAGE
        self.ecosystem: Ecosystem | None = None
        self._env_dir: tempfile.TemporaryDirectory[str] | None = None
        self._repo_path: Path | None = None

    @property
    def image_digest(self) -> str:
        return self.image.split("@", 1)[1]

    def _docker(
        self, args: list[str], timeout: int
    ) -> tuple[int, str, str, bool]:
        name = f"liedetector-{uuid.uuid4().hex[:12]}"
        cmd = ["docker", "run", "--name", name, *args]
        log.debug("docker run", extra={"data": {"args": args[:8]}})
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout, proc.stderr, False
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "kill", name], capture_output=True)
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return -1, stdout, stderr, True
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    def _proxy_install_args(self) -> list[str]:
        """Opt-in proxy/CA accommodation for the network-enabled install phase.

        Off by default.  When ``LIEDETECTOR_INSTALL_HOST_NETWORK=1`` is set the
        install container uses host networking so a loopback egress proxy is
        reachable; ``HTTP(S)_PROXY``/``NO_PROXY`` are forwarded, and
        ``LIEDETECTOR_CA_BUNDLE`` (if set) is mounted read-only and pointed at
        with ``PIP_CERT``/``SSL_CERT_FILE``.  The execution phase never uses
        any of this - it always runs with ``--network none``.
        """
        args: list[str] = []
        if os.environ.get("LIEDETECTOR_INSTALL_HOST_NETWORK") == "1":
            args += ["--network", "host"]
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
                    "https_proxy", "http_proxy", "no_proxy"):
            value = os.environ.get(var)
            if value:
                args += ["-e", f"{var}={value}"]
        ca = os.environ.get("LIEDETECTOR_CA_BUNDLE")
        if ca and Path(ca).is_file():
            args += [
                "-v", f"{Path(ca).resolve()}:/ca/bundle.crt:ro",
                "-e", "PIP_CERT=/ca/bundle.crt",
                "-e", "SSL_CERT_FILE=/ca/bundle.crt",
                "-e", "NODE_EXTRA_CA_CERTS=/ca/bundle.crt",
                "-e", "npm_config_cafile=/ca/bundle.crt",
            ]
        return args

    def install(self, repo_path: Path) -> InstallResult:
        """Install the repository into a persistent volume (network on).

        The ecosystem is detected from the repository's root manifests and
        selects both the sandbox image and the install command:

        - python: create a venv in the volume, ``pip install`` the repo +
          pytest from a tmpfs copy (in-tree ``*.egg-info`` never touches the
          read-only source mount).
        - node: copy the repo into the volume and ``npm install`` there, so
          ``node_modules`` survives into the (read-only) execution phase.
        """
        self._repo_path = repo_path.resolve()
        self.ecosystem = detect_ecosystem(self._repo_path)
        if self._image_override is None:
            self.image = DOCKER_IMAGE_NODE if self.ecosystem is Ecosystem.NODE else DOCKER_IMAGE
        if self.ecosystem is Ecosystem.NODE:
            install_cmd = (
                "cp -r /repo /env/app && cd /env/app && "
                "npm install --no-audit --no-fund --loglevel=error"
            )
        else:
            install_cmd = (
                "cp -r /repo /tmp/src && python -m venv /env/venv && "
                "/env/venv/bin/pip install --no-cache-dir --quiet /tmp/src pytest"
            )
        self._env_dir = tempfile.TemporaryDirectory(
            prefix="liedetector-env-", ignore_cleanup_errors=True
        )
        env_path = Path(self._env_dir.name)
        env_path.chmod(0o777)
        code, stdout, stderr, timed_out = self._docker(
            [
                "--rm",
                *self._proxy_install_args(),
                "--user", "1000:1000",
                "-e", "HOME=/tmp",
                "--tmpfs", "/tmp:rw,size=512m",
                "-v", f"{self._repo_path}:/repo:ro",
                "-v", f"{env_path}:/env:rw",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--pids-limit", "256",
                self.image,
                "sh", "-c",
                install_cmd,
            ],
            timeout=INSTALL_TIMEOUT_S,
        )
        log_text = f"# install exit_code={code} timed_out={timed_out}\n{stdout}\n{stderr}"
        return InstallResult(ok=code == 0 and not timed_out, exit_code=code, log=log_text)

    def run_harness(self, harness_path: Path, run_index: int) -> ExecutionRun:
        """Run one harness with the network disabled and the sandbox locked."""
        if self._env_dir is None or self._repo_path is None:
            raise LieDetectorError("executor.install() must succeed before run_harness()")
        env_path = Path(self._env_dir.name)
        harness_dir = harness_path.resolve().parent
        if harness_path.suffix == ".mjs":
            runner_path = harness_dir / JS_RUNNER_NAME
            if not runner_path.is_file():
                runner_path.write_bytes(JS_RUNNER_SOURCE.encode("utf-8"))
                runner_path.chmod(0o644)
            entry_cmd = [
                "node", f"/harness/{JS_RUNNER_NAME}", f"/harness/{harness_path.name}",
                JS_APP_ROOT,
            ]
        else:
            entry_cmd = [
                "/env/venv/bin/python", "-m", "pytest", "-v", "-p", "no:cacheprovider",
                f"/harness/{harness_path.name}",
            ]
        code, stdout, stderr, timed_out = self._docker(
            [
                "--rm",
                "--network", "none",
                "--user", "1000:1000",
                "-e", "HOME=/tmp",
                "--read-only",
                "--tmpfs", "/tmp:rw,size=256m",
                "--cpus", "1",
                "--memory", "1g",
                "--pids-limit", "128",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "-v", f"{self._repo_path}:/repo:ro",
                "-v", f"{env_path}:/env:ro",
                "-v", f"{harness_dir}:/harness:ro",
                "-w", "/tmp",
                self.image,
                *entry_cmd,
            ],
            timeout=EXECUTION_TIMEOUT_S,
        )
        control, claim = parse_pytest_results(stdout)
        return ExecutionRun(
            run_index=run_index,
            exit_code=code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            control_passed=control,
            claim_passed=claim,
        )

    def cleanup(self) -> None:
        if self._env_dir is not None:
            env_path = Path(self._env_dir.name)
            # `python -m venv` creates POSIX symlinks inside the venv (e.g.
            # venv/lib64 -> venv/lib). On a Windows host bind-mounting this
            # directory into the container, those symlinks materialise as
            # reparse points that Windows' own filesystem APIs cannot open
            # or traverse (WinError 1920), so a host-side rmtree crashes.
            # Let the container remove its own tree with POSIX semantics
            # first; ignore_cleanup_errors=True above is a defense-in-depth
            # fallback if Docker itself is unavailable at this point.
            subprocess.run(
                ["docker", "run", "--rm", "-v", f"{env_path}:/env:rw", self.image,
                 "sh", "-c", "rm -rf /env/venv /env/app"],
                capture_output=True,
            )
            self._env_dir.cleanup()
            self._env_dir = None


def docker_available() -> tuple[bool, str]:
    """Check whether a Docker daemon is reachable (used by ``doctor``)."""
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, proc.stderr.strip()
    return True, proc.stdout.strip()
