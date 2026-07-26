"""Unit tests for claim extraction: line location, stable IDs, ordering."""

from __future__ import annotations

from liedetector.extract import claim_id, extract_claims, locate_quote

from .conftest import FakeLLM, extraction_response

README = "line one\nthe answer is 42\nrepeat\nrepeat\n"


def test_locate_quote_finds_line() -> None:
    used: dict[str, int] = {}
    assert locate_quote(README, "the answer is 42", used) == (2, 0)


def test_locate_quote_handles_repeats() -> None:
    used: dict[str, int] = {}
    assert locate_quote(README, "repeat", used) == (3, 0)
    assert locate_quote(README, "repeat", used) == (4, 1)


def test_locate_missing_quote_returns_none() -> None:
    assert locate_quote(README, "absent", {}) is None


# READMEs are hard-wrapped, so a model quoting prose that spans a line break
# returns it with a space where the file has a newline. Matching on raw bytes
# discarded those quotes even though they were, as prose, exactly right - which
# silently emptied the claim set for any well-formatted README.
WRAPPED = (
    "# Title\n\n"
    "Aletheia scans your entire GitHub account, scores every repository,\n"
    "and tells you exactly which ones need attention.\n\n"
    "Requires **Python 3.11+**.\n\n"
    "It writes `reports/portfolio.json`   with   odd   spacing.\n"
)


def test_locate_quote_spanning_a_hard_line_wrap() -> None:
    used: dict[str, int] = {}
    assert locate_quote(
        WRAPPED,
        "scores every repository, and tells you exactly which ones need attention",
        used,
    ) == (3, 0)


def test_locate_quote_ignores_markdown_emphasis() -> None:
    used: dict[str, int] = {}
    assert locate_quote(WRAPPED, "Requires Python 3.11+", used) == (6, 0)


def test_locate_quote_collapses_runs_of_whitespace() -> None:
    used: dict[str, int] = {}
    assert locate_quote(WRAPPED, "with odd spacing", used) == (8, 0)


def test_locate_quote_still_rejects_invented_text() -> None:
    """Normalization must not loosen matching into accepting anything - a quote
    the model made up has to stay rejected."""
    assert locate_quote(WRAPPED, "scores every repository and emails your boss", {}) is None
    assert locate_quote(WRAPPED, "A CLAIM THAT IS NOT PRESENT AT ALL", {}) is None


def test_locate_quote_prefers_exact_match_for_line_number() -> None:
    """An exact hit must win, so line numbers stay right when a normalized
    match would have found an earlier occurrence."""
    text = "alpha beta\nfiller\nalpha  beta\n"
    used: dict[str, int] = {}
    assert locate_quote(text, "alpha  beta", used) == (3, 0)


def test_locate_quote_normalized_repeats_advance() -> None:
    text = "wrapped one\ntwo here\nwrapped one\ntwo here\n"
    used: dict[str, int] = {}
    assert locate_quote(text, "wrapped one two here", used) == (1, 0)
    assert locate_quote(text, "wrapped one two here", used) == (3, 1)


def test_locate_quote_empty_after_normalization_returns_none() -> None:
    assert locate_quote(WRAPPED, "   ", {}) is None
    assert locate_quote(WRAPPED, "***", {}) is None


def test_claim_ids_are_stable_and_deterministic() -> None:
    assert claim_id("q", 0) == claim_id("q", 0)
    assert claim_id("q", 0) != claim_id("q", 1)
    assert claim_id("q", 0).startswith("clm-")


def test_extract_drops_quotes_not_in_readme() -> None:
    llm = FakeLLM(
        [
            extraction_response(
                [
                    {"quote": "the answer is 42", "claim_type": "deterministic",
                     "hypothesis": "returns 42"},
                    {"quote": "hallucinated quote", "claim_type": "deterministic",
                     "hypothesis": "nope"},
                ]
            )
        ]
    )
    claims = extract_claims(llm, README)
    assert len(claims) == 1
    assert claims[0].source.line == 2


def test_extract_orders_by_readme_position() -> None:
    llm = FakeLLM(
        [
            extraction_response(
                [
                    {"quote": "repeat", "claim_type": "deterministic", "hypothesis": "b"},
                    {"quote": "line one", "claim_type": "deterministic", "hypothesis": "a"},
                ]
            )
        ]
    )
    claims = extract_claims(llm, README)
    assert [c.source.line for c in claims] == [1, 3]


def test_extract_is_reproducible() -> None:
    payload = extraction_response(
        [{"quote": "the answer is 42", "claim_type": "deterministic", "hypothesis": "h"}]
    )
    first = extract_claims(FakeLLM([payload]), README)
    second = extract_claims(FakeLLM([payload]), README)
    assert [c.id for c in first] == [c.id for c in second]
