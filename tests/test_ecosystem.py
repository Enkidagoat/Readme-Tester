"""Unit tests for manifest-based ecosystem detection and package naming."""

from __future__ import annotations

from pathlib import Path

from liedetector.ecosystem import Ecosystem, detect_ecosystem, package_name


def test_detects_python_from_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "my-lib"\n', encoding="utf-8")
    assert detect_ecosystem(tmp_path) is Ecosystem.PYTHON
    assert package_name(tmp_path, Ecosystem.PYTHON) == "my_lib"


def test_detects_python_from_setup_py(tmp_path: Path) -> None:
    (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup()\n", encoding="utf-8")
    assert detect_ecosystem(tmp_path) is Ecosystem.PYTHON


def test_detects_node_from_package_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "my-app"}', encoding="utf-8")
    assert detect_ecosystem(tmp_path) is Ecosystem.NODE
    assert package_name(tmp_path, Ecosystem.NODE) == "my-app"


def test_python_wins_when_both_manifests_exist(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    assert detect_ecosystem(tmp_path) is Ecosystem.PYTHON


def test_no_manifest_detects_nothing(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    assert detect_ecosystem(tmp_path) is None


def test_node_package_name_falls_back_to_dir_name(tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "package.json").write_text("not json", encoding="utf-8")
    assert package_name(repo, Ecosystem.NODE) == "my_repo"
