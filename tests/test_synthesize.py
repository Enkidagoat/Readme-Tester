"""Unit tests for harness static validation and synthesis repair loop."""

from __future__ import annotations

import pytest

from liedetector.ecosystem import Ecosystem
from liedetector.llm import LLMError
from liedetector.models import Claim, ClaimType, Source
from liedetector.synthesize import (
    synthesize_harness,
    validate_harness_code,
    validate_js_harness_code,
)

from .conftest import PASSING_HARNESS_JS, UNSAFE_HARNESS, FakeLLM, harness_response

GOOD = '''"""Verifies: the package imports."""


def test_control():
    import toylib  # control assertion
    assert toylib is not None


def test_claim():
    import toylib
    assert toylib.add(2, 3) == 5
'''


def _claim() -> Claim:
    return Claim(
        id="clm-x",
        source=Source(file="README.md", line=1, quote="q"),
        claim_type=ClaimType.DETERMINISTIC,
        hypothesis="toylib.add(2, 3) == 5",
        interpretation_notes="n",
        confidence="high",
    )


def test_good_harness_has_no_errors() -> None:
    assert validate_harness_code(GOOD) == []


def test_syntax_error_detected() -> None:
    assert validate_harness_code("def test_control(: pass")


def test_missing_control_detected() -> None:
    code = "def test_claim():\n    assert True\n"
    errors = validate_harness_code(code)
    assert any("test_control" in e for e in errors)


@pytest.mark.parametrize(
    "snippet",
    [
        "import socket",
        "import subprocess",
        "from urllib import request",
        "import ctypes",
    ],
)
def test_forbidden_imports_detected(snippet: str) -> None:
    code = (
        f"{snippet}\n\ndef test_control():\n    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    assert True\n"
    )
    assert any("forbidden import" in e for e in validate_harness_code(code))


def test_forbidden_os_call_detected() -> None:
    code = (
        "import os\n\ndef test_control():\n    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    os.system('rm -rf /')\n"
    )
    assert any("forbidden call" in e for e in validate_harness_code(code))


def test_forbidden_eval_detected() -> None:
    code = (
        "def test_control():\n    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    eval('1+1')\n"
    )
    assert any("forbidden builtin" in e for e in validate_harness_code(code))


# --- EDGE_CASE_AUDIT finding #4: bypasses of the Python validator ---
#
# Every snippet below was run through the real validator by the audit
# (section 2c) and returned NO errors.  Each must now be rejected.

BYPASS_SNIPPETS = {
    "from-import defeats attribute matching": (
        "from os import system\n\ndef test_control():\n"
        "    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    system('id')\n"
    ),
    "alias defeats the os.system name check": (
        "import os as o\n\ndef test_control():\n"
        "    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    o.system('id')\n"
    ),
    "computed attribute name defeats literal matching": (
        "import os\n\ndef test_control():\n"
        "    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    getattr(os, 'sys' + 'tem')('id')\n"
    ),
    "importlib dynamically imports a banned module": (
        "import importlib\n\ndef test_control():\n"
        "    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    importlib.import_module('socket')\n"
    ),
    "runtime rebinding of a forbidden call": (
        "import os\n\ndef test_control():\n"
        "    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    run = os.system\n    run('id')\n"
    ),
    "aliased from-import of a dynamic importer": (
        "from importlib import import_module as load\n\ndef test_control():\n"
        "    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    load('socket')\n"
    ),
}


@pytest.mark.parametrize("code", BYPASS_SNIPPETS.values(), ids=list(BYPASS_SNIPPETS))
def test_validator_bypasses_are_closed(code: str) -> None:
    assert validate_harness_code(code), "harness passed validation but must not"


def test_direct_os_system_control_still_caught() -> None:
    """The audit's control row: the literal form was already rejected."""
    code = (
        "import os\n\ndef test_control():\n    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    os.system('id')\n"
    )
    assert any("forbidden call" in e for e in validate_harness_code(code))


@pytest.mark.parametrize(
    "snippet",
    [
        # Presence checks against the target API: the single most common way
        # to verify a "supports X" claim.  A literal attribute name is
        # statically checkable, so it stays allowed.
        "assert getattr(toylib, 'add')(2, 3) == 5",
        "import importlib.metadata\n    assert importlib.metadata.version('toylib')",
        "from importlib.metadata import version\n    assert version('toylib')",
    ],
)
def test_legitimate_reflection_is_not_blocked(snippet: str) -> None:
    code = (
        "import toylib\n\ndef test_control():\n    import toylib\n    assert toylib\n\n"
        f"def test_claim():\n    {snippet}\n"
    )
    assert validate_harness_code(code) == []


def test_synthesize_returns_valid_harness() -> None:
    llm = FakeLLM([harness_response(GOOD)])
    code = synthesize_harness(llm, _claim(), "toylib")
    assert "test_control" in code
    assert len(llm.calls) == 1


def test_synthesize_repairs_unsafe_harness() -> None:
    unsafe = UNSAFE_HARNESS.format(package="toylib")
    llm = FakeLLM([harness_response(unsafe), harness_response(GOOD)])
    code = synthesize_harness(llm, _claim(), "toylib")
    assert "socket" not in code
    assert len(llm.calls) == 2


def test_synthesize_fails_when_repair_still_unsafe() -> None:
    unsafe = UNSAFE_HARNESS.format(package="toylib")
    llm = FakeLLM([harness_response(unsafe), harness_response(unsafe)])
    with pytest.raises(LLMError, match="after repair"):
        synthesize_harness(llm, _claim(), "toylib")


GOOD_JS = PASSING_HARNESS_JS.format(hypothesis="add(2, 3) === 5")


def test_good_js_harness_has_no_errors() -> None:
    assert validate_js_harness_code(GOOD_JS) == []


def test_js_missing_exports_detected() -> None:
    errors = validate_js_harness_code("export async function test_claim() {}")
    assert any("test_control" in e for e in errors)


def test_js_extra_export_detected() -> None:
    code = GOOD_JS + "\nexport function helper() {}\n"
    assert any("exactly two" in e for e in validate_js_harness_code(code))


def test_js_default_export_detected() -> None:
    code = GOOD_JS + "\nexport default {};\n"
    assert any("default" in e for e in validate_js_harness_code(code))


@pytest.mark.parametrize(
    "snippet",
    [
        'const cp = await import("node:child_process");',
        'const net = require("net");',
        'await import("node:http");',
        "eval('1+1');",
        "const fn = new Function('return 1');",
        'await fetch("https://example.com");',
        "const key = process.env.SECRET;",
    ],
)
def test_js_forbidden_constructs_detected(snippet: str) -> None:
    code = GOOD_JS.replace("assert.ok(true); // EXPECT_PASS", snippet)
    assert validate_js_harness_code(code)


def test_js_urls_in_strings_are_not_flagged() -> None:
    code = GOOD_JS.replace(
        "assert.ok(true); // EXPECT_PASS",
        'assert.ok("see https://example.com/docs for details");',
    )
    assert validate_js_harness_code(code) == []


def test_synthesize_node_uses_js_validator() -> None:
    llm = FakeLLM([harness_response(GOOD_JS)])
    code = synthesize_harness(llm, _claim(), "toylib-js", Ecosystem.NODE)
    assert "export async function test_control" in code
    assert len(llm.calls) == 1
    system_prompt = llm.calls[0][0]
    assert "Node.js" in system_prompt


def test_synthesize_node_rejects_python_harness() -> None:
    llm = FakeLLM([harness_response(GOOD), harness_response(GOOD)])
    with pytest.raises(LLMError, match="after repair"):
        synthesize_harness(llm, _claim(), "toylib-js", Ecosystem.NODE)
