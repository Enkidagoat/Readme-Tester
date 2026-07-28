"""Ecosystem detection: which install/execution path a repository needs.

Detection is deterministic and manifest-based:

- ``python``: a ``pyproject.toml``, ``setup.py`` or ``setup.cfg`` at the
  repository root.  Python wins when both ecosystems are present because it
  has the deepest execution support (pip install + pytest).
- ``node``: a ``package.json`` at the repository root.  Installed with
  ``npm install`` and executed with the bundled ESM runner (no pytest).
- ``None``: neither manifest exists.  The pipeline warns before spending any
  model calls on harness synthesis and adjudicates every executable claim as
  ``INCONCLUSIVE`` — the sandbox has no way to install the repository.
"""

from __future__ import annotations

import enum
import json
import tomllib
from pathlib import Path


class Ecosystem(enum.StrEnum):
    """Supported install/execution ecosystems."""

    PYTHON = "python"
    NODE = "node"


#: Harness file extension per ecosystem (``.py`` -> pytest, ``.mjs`` -> node).
HARNESS_EXTENSION = {
    Ecosystem.PYTHON: "py",
    Ecosystem.NODE: "mjs",
}

#: Harness-synthesis prompt id per ecosystem.  Superseded prompt files stay
#: in the package so receipts that recorded them remain resolvable.
HARNESS_PROMPT = {
    Ecosystem.PYTHON: "harness-v2",
    Ecosystem.NODE: "harness-js-v2",
}


def detect_ecosystem(repo_path: Path) -> Ecosystem | None:
    """Detect the repository's ecosystem from its root manifests."""
    if any((repo_path / name).is_file() for name in ("pyproject.toml", "setup.py", "setup.cfg")):
        return Ecosystem.PYTHON
    if (repo_path / "package.json").is_file():
        return Ecosystem.NODE
    return None


def package_name(repo_path: Path, ecosystem: Ecosystem | None) -> str:
    """Best-effort installable/import package name for the repository."""
    if ecosystem is Ecosystem.PYTHON:
        pyproject = repo_path / "pyproject.toml"
        if pyproject.is_file():
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                name = data.get("project", {}).get("name")
                if isinstance(name, str) and name:
                    return name.replace("-", "_")
            except tomllib.TOMLDecodeError:
                pass
        return repo_path.name.replace("-", "_")
    if ecosystem is Ecosystem.NODE:
        manifest = repo_path / "package.json"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            name = data.get("name")
            if isinstance(name, str) and name:
                return name
        except (OSError, json.JSONDecodeError):
            pass
    return repo_path.name.replace("-", "_")
