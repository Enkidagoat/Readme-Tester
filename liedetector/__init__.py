"""The Lie Detector: verify factual claims in a repository's README.

The package implements a deterministic pipeline:

    Clone -> Freeze Commit -> Extract Claims -> Classify -> Refine ->
    Generate Harness -> Execute (x2) -> Adjudicate ->
    verification_receipt.json -> Truth Report (HTML)

The canonical artifact is ``verification_receipt.json``; the HTML Truth
Report is a derived view rendered from the receipt plus stored logs.
"""

TOOL_VERSION = "0.2.0"
SCHEMA_VERSION = "1.1"
RECEIPT_VERSION = "1"

PROMPT_VERSIONS = {
    "extraction": "extract-v1",
    "harness": "harness-v1",
    "harness_js": "harness-js-v1",
}

#: Execution sandbox image for Python repositories, pinned by digest (never by tag).
DOCKER_IMAGE = (
    "python:3.12-slim"
    "@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
)

#: Execution sandbox image for Node repositories, pinned by digest (never by tag).
DOCKER_IMAGE_NODE = (
    "node:22-slim"
    "@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3"
)

#: Default model for the OpenAI-compatible provider (used with --provider openai).
DEFAULT_OPENAI_MODEL = "gpt-4o"
