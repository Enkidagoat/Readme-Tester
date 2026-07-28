# Security model

The Lie Detector executes model-generated code against untrusted repositories.
Every design decision below exists to keep that safe, auditable, and
reproducible.

## Untrusted inputs

- **Repository contents are untrusted data, never instructions.** README text
  and claim records are always delimited inside `<readme_data>` / `<claim_data>`
  markers in prompt templates. No instruction originating inside a repository
  may change extraction, harness generation, or execution behaviour. The test
  suite includes a prompt-injection README and asserts the pipeline ignores it
  (`tests/test_fixtures.py::test_prompt_injection_readme_is_ignored`).
- **Model output is validated before use.** Every structured model response is
  validated against a fixed JSON schema (`Generate -> Validate -> Repair ->
  Validate -> Fail`). Malformed output is never executed.
- **Model-reported line numbers are never trusted.** Claim locations are found
  by exact string match against the README.

## Harness generation

**The validator is not the security boundary — the sandbox is.** A harness is
model-authored arbitrary code that this tool executes on purpose, so no static
check over it can be load-bearing. Static validation earns its place as
defense-in-depth and as steering: it keeps the model producing well-formed
harnesses, and it catches the accidental case. Read the bans below with that
in mind, and do not weaken a sandbox constraint because a validator rule
appears to cover it.

Each harness is statically validated before it can run
(`liedetector/synthesize.py`). Python harnesses are checked against the AST:

- they must parse and define exactly `test_control` and `test_claim`;
- forbidden imports (`socket`, `subprocess`, `ctypes`, `multiprocessing`, ...),
  forbidden paths (`os.system`, `os.remove`, `importlib.import_module`, ...)
  and forbidden builtins (`eval`, `exec`, `__import__`, ...) are rejected;
- forbidden references are matched on the **resolved** dotted path, so
  `from os import system`, `import os as o` and `run = os.system` are all
  caught rather than only the literal `os.system` spelling;
- `getattr`/`setattr`/`delattr` with a computed attribute name are rejected,
  because a static checker cannot resolve them.

Node harnesses have no parser available in the Python stdlib, so they are
scanned as text against a normalized copy in which non-substituting template
literals, single-quoted strings and adjacent literal concatenations have been
folded to one form. Equivalent spellings of a module specifier — `` `node:x` ``,
`"node:" + "x"` — therefore collapse to the spelling the pattern list matches.
Property access named `constructor` is rejected, since it reaches the
`Function` constructor without naming it.

Bans that the sandbox already neutralises are deliberately **not** kept:
`asyncio`, `urllib.parse`, `http.HTTPStatus`, `shutil`, the Node network
modules and `process.env` are all available to harnesses. Execution runs
`--network none` with only `HOME=/tmp` in the environment, so none of them
grants a capability; banning them only made whole classes of honest claims
untestable.

A claim whose harness cannot be repaired fails gracefully and is never run.

### The control assertion

The control assertion is the entire basis for trusting a `FALSE`: if the
control fails, the verdict is `INCONCLUSIVE`, never `FALSE`. It must therefore
prove the *installed application* is healthy, not merely that a mount is
readable.

- Python: `import PACKAGE` — the package installed and imports.
- Node: the control is owned by the tool's static runner
  (`executor.py::JS_RUNNER_SOURCE`), not by the model. It requires
  `/env/app/package.json` to parse, `node_modules` to be populated whenever the
  manifest declares dependencies, and the declared entry point to import. The
  harness's own `test_control` runs afterwards and can only add strictness.

## Execution sandbox

Harnesses run in Docker with the image pinned **by digest, not tag**
(`liedetector/__init__.py`; the resolved digest is recorded in the receipt):

- non-root user (`1000:1000`), read-only repository mount, `tmpfs` writable
  scratch, read-only root filesystem during execution;
- **network enabled only during dependency installation; disabled
  (`--network none`) during execution**;
- 1 CPU, 1 GB RAM, PID limit, all Linux capabilities dropped,
  `no-new-privileges`;
- no privileged mode, no Docker socket mount, no host networking during
  execution, no mounted secrets or SSH keys;
- hard 120s timeout per execution -> `INCONCLUSIVE`, never `FALSE`;
- containers and temporary filesystems are always cleaned up.

The repository mount is treated as immutable input: the package is built from a
copy inside the container's writable `tmpfs`, so build artifacts never touch
the read-only source tree. There is no rollback logic; cleanup on exit is
sufficient.

### Proxied environments (opt-in)

For CI or corporate environments behind a TLS-terminating egress proxy, the
**install phase only** can be pointed at a proxy:

- `LIEDETECTOR_INSTALL_HOST_NETWORK=1` uses host networking for install so a
  loopback proxy is reachable;
- `HTTP(S)_PROXY` / `NO_PROXY` are forwarded to the install container;
- `LIEDETECTOR_CA_BUNDLE=/path/to/ca.pem` is mounted read-only and used as
  `PIP_CERT` / `SSL_CERT_FILE`.

The **execution phase is never affected** — it always runs with
`--network none`. These variables are off by default.

## Evidence integrity

The verification receipt is the root of trust. All hashes are SHA-256. The
canonical receipt (sorted keys, fixed separators, UTF-8) is hashed into a
sidecar `verification_receipt.sha256`. The HTML report embeds the receipt hash;
the receipt never references the report. `liedetector verify` recomputes every
artifact hash and the receipt hash from stored artifacts and fails on any
mismatch.

## Logging

Logs are structured JSON. Secrets, credentials, and tokens are never logged.

## Credentials

`liedetector` reads provider credentials (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `FEATHERLESS_API_KEY`) from the environment, optionally
loaded from a `.env` file in the current directory (`.env` is git-ignored and
never overrides a variable already set in the shell). `.env.example` is
tracked and must **only ever contain placeholder values** — never a real key,
even temporarily. If you touch `.env.example`, diff it before committing.

## Reporting a vulnerability

Open a private security advisory on the repository, or contact the maintainers
directly. Please do not file public issues for undisclosed vulnerabilities.
