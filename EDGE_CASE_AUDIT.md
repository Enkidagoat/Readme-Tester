# EDGE_CASE_AUDIT

Read-only audit of the verification pipeline, per `EDGE_CASE_DIRECTIVE.md`.

- **Tool version audited:** 0.2.0 (`liedetector/__init__.py:13`)
- **Commit audited:** `a881d6d` on `claude/service-offerings-audit-test-d61z6g`
- **Date:** 2026-07-27
- **Scope:** audit and report only. No logic was modified in `adjudicate.py`,
  either harness validator, or `badge.py`. No fixes are in this diff.

Findings are marked **[REPRODUCED]** when this audit executed the code and
observed the stated behavior, or **[INSPECTION]** when derived from reading the
source. Nothing here is inferred from assumption alone.

> **Resolution status (2026-07-28).** All nine findings are fixed on
> `claude/edge-case-audit-fixes-jmoxkq`, one commit per finding, each with a
> regression test that fails on the pre-fix code. The audit body below is left
> exactly as written — it is the record of what was true at `a881d6d` — and the
> status column plus the notes marked **[FIXED]** are the only additions.

## Severity ranking (read this first)

Ranked by the directive's rule: silently producing a wrong verdict outranks
everything, because a wrong verdict is the one thing this tool exists to
prevent.

| # | Finding | Section | Class | Status | Test |
|---|---|---|---|---|---|
| 1 | Model error (hallucinated symbol / wrong path) yields `FALSE` at `HIGH` confidence | 1 | **Silent wrong verdict** | **Fixed / verified** | [`test_hallucinated_symbol_is_never_false`, `test_wrong_path_in_node_harness_is_never_false`](tests/test_adjudicate.py) · [`test_hallucinated_symbol_never_reaches_false_through_the_pipeline`](tests/test_pipeline.py) |
| 2 | JS control assertion cannot detect a broken install, yet gates `FALSE` | 1 | **Silent wrong verdict** | **Fixed / verified** | [`tests/test_js_runner.py`](tests/test_js_runner.py) (8 cases, real `node`) |
| 3 | Deterministic environment faults repeat identically and read as `FALSE` | 1 | **Silent wrong verdict** | **Fixed / verified** (partial — see note) | [`test_sandbox_faults_are_never_false`](tests/test_adjudicate.py) |
| 4 | Python validator bypassable four ways | 2 | Safety boundary | **Fixed / verified** | [`test_validator_bypasses_are_closed`](tests/test_synthesize.py) |
| 5 | JS validator bypassable three ways | 2 | Safety boundary | **Fixed / verified** | [`test_js_validator_bypasses_are_closed`](tests/test_synthesize.py) |
| 6 | Over-strict bans block legitimate claims (`asyncio`, `urllib`, `process.env`, …) | 2 | Capability false-negative | **Fixed / verified** | [`test_relaxed_imports_are_allowed`, `test_js_relaxed_constructs_are_allowed`](tests/test_synthesize.py) |
| 7 | `UNTESTABLE` invisible in badge; unexecuted claims render green | 3 | Misleading signal | **Fixed / verified** | [`test_untestable_only_claim_set_is_distinguishable_from_silence`](tests/test_badge.py) |
| 8 | `0 proven + N inconclusive` renders lightgrey, not yellow | 3 | Cosmetic | **Fixed / verified** | [`test_all_inconclusive_is_yellow_not_lightgrey`](tests/test_badge.py) |
| 9 | Untested fallback paths (`UNKNOWN` fall-through, timeout, TOML decode) | 4 | Coverage gap | **Fixed / verified** | [`tests/test_fallback_paths.py`](tests/test_fallback_paths.py) (16 cases) |

**Note on #3.** The fix covers the sandbox-caused faults this audit named
(`--network none`, read-only mounts, absent system libraries), which are now
`ENVIRONMENT_FAILURE` → `INCONCLUSIVE`. A deterministic fault from outside that
list — a locale- or timezone-dependent assertion — still repeats identically
and still earns `HIGH`. Confidence policy itself is unchanged.

### Verified against a real repository

`pallets/click` @ `00e592cea702e0b2caa0dee42489fdb1c22cd845`, real Docker
sandbox, same harnesses run against both trees:

| | pre-fix (`main`) | post-fix |
|---|---|---|
| `click.lazy_subcommand_loader()` (invented symbol) | `FALSE` / `TARGET_FAILURE` / `HIGH` | `INCONCLUSIVE` / `HARNESS_ERROR` / `LOW` |
| Badge | `red` — "2 proven, 1 false" | `yellow` — "2 proven, 0 false, 1 inconclusive, 1 untestable" |
| Receipt verification | 17/17 | 17/17 |

A real, honest, widely used project was publicly branded a liar at maximum
confidence because the model invented an API symbol. It no longer is.

---

## 1. Adjudication logic (`adjudicate.py`)

### 1a. Every branch, in plain English

`_classify_failure` (`adjudicate.py:71-86`), evaluated in order:

| Location | Trigger condition | Current behavior | Is this correct? | Risk if wrong |
|---|---|---|---|---|
| `:73` | `run.timed_out` — harness exceeded the 120s cap | `TIMEOUT` | **Yes.** Never `FALSE`; a slow machine is not a lying README. | Low |
| `:75` | `MemoryError`/`Killed`/`OOM`/`JavaScript heap out of memory` in output, or exit 137 | `RESOURCE_LIMIT` | **Yes**, but see 1d — a repo whose legitimate work exceeds 1 GB is indistinguishable from a runaway harness. | Low |
| `:77` | Control did **not** pass **and** output contains an import-failure sign | `IMPORT_FAILURE` | **Yes.** Package never imported; nothing about the claim was tested. | Low |
| `:79` | Control did not pass, no import sign | `HARNESS_FAILURE` | **Yes.** Blames the harness, not the repo. | Low |
| `:81` | Traceback frames resolve inside the target package or `/repo` (`/env/app` for Node) | `TARGET_FAILURE` → eligible for `FALSE` | **Mostly.** Strongest available evidence, but see 1b. | High |
| `:83` | **Fall-through:** `claim_passed is False` with the traceback *outside* the target | `TARGET_FAILURE` → eligible for `FALSE` | **No — this is finding #1.** | **Critical** |
| `:86` | Everything else (e.g. `claim_passed is None`) | `UNKNOWN` → `INCONCLUSIVE` | Yes, correctly conservative. Untested (see §4). | Low |

`adjudicate()` (`adjudicate.py:130-195`):

| Location | Trigger condition | Current behavior | Is this correct? | Risk if wrong |
|---|---|---|---|---|
| `:137` | `len(runs) != 2` | `ValueError` | Yes — enforces the locked double-execution policy. | Low |
| `:144` | Either run timed out | `INCONCLUSIVE`/`TIMEOUT` | Yes. | Low |
| `:151` | Both runs passed (incl. control) | `PROVEN`; `HIGH` if identical else `MEDIUM` | Yes, given the control is meaningful — which on Node it is not (finding #2). | High |
| `:157` | Runs disagree (PASS vs FAIL) | `INCONCLUSIVE`/`UNKNOWN`, `MEDIUM` | Yes — nondeterminism is not proof. | Low |
| `:168` | Both failed, control failed in either run | `INCONCLUSIVE` | Yes — the explicit "failed control is never FALSE" rule. | Low |
| `:178` | Both failed, control passed, category is `TARGET_FAILURE` | **`FALSE`**, `HIGH` if identical | Only as sound as `_classify_failure`. Findings #1–#3 all land here. | **Critical** |
| `:195` | Both failed, control passed, any other category | `INCONCLUSIVE` | Yes. | Low |

### 1b. Finding #1 — model error is indistinguishable from a false claim **[REPRODUCED]**

The fall-through at `:83` treats *any* failed `test_claim` as a target failure
once the control passed, **even when the traceback proves the failure happened
inside the harness**. Two realistic model errors were driven through the real
`adjudicate()` path:

| Scenario | Traceback origin | Verdict produced |
|---|---|---|
| Python harness calls a hallucinated symbol (`AttributeError: module 'toylib' has no attribute 'no_such_function'`), frame in `/harness/clm-x.py` | harness | `FALSE` / `TARGET_FAILURE` / **HIGH** |
| JS harness reads a guessed-wrong path (`ENOENT … '/repo/src/wrong-path.ts'`), frame `at async test_claim (file:///harness/clm-x.mjs)` | harness | `FALSE` / `TARGET_FAILURE` / **HIGH** |

Both are **false accusations at maximum confidence**, published to a red badge
and a receipt-backed report. The repository is branded as lying because the
model invented an API or mistyped a path. The `_traceback_in_target` check at
`:81` exists precisely to prevent this, and `:83` then overrides it.

This is not hypothetical for the Node path. The JS prompt instructs harnesses
to verify claims by reading files from `/repo`, so a wrong path is a *routine*
model error, and every one becomes a `FALSE`.

**Note on the one real FALSE observed in production** (creator-ai-hub-v2 run 3,
the `safety-guards.ts` claim): that verdict reached `FALSE` through this same
`:83` fall-through — a regex assertion failed inside the harness. It happened
to be *correct* on the merits, but it was produced by the unsound path, not the
sound one. Correct verdicts from an unsound mechanism are luck, not evidence.

### 1c. Finding #2 — `control_passed=True` for the wrong reason **[INSPECTION]**

The control assertion is the entire basis for trusting a `FALSE`. Its strength
differs sharply by ecosystem:

| Ecosystem | Control assertion | What it actually proves |
|---|---|---|
| Python | `import <package>` (`prompts/harness-v1.txt`) | Package installed and importable — a genuine environment check. |
| Node | `JSON.parse(readFile("/repo/package.json"))` (`prompts/harness-js-v1.txt`) | Only that the read-only repo mount works. |

The Node control **never touches `/env/app`**, so it passes when `npm install`
produced a broken tree, when the entry point is missing, and when every module
in the app fails to import. It is closer to a mount check than a health check,
yet `adjudicate.py:168` accepts it as proof the environment is healthy and
unlocks `FALSE`. A Node claim can therefore be adjudicated `FALSE` in an
environment that was never demonstrated to work.

### 1d. Finding #3 — deterministic shared causes repeat identically **[INSPECTION]**

Double execution is designed to catch flakiness, but it only filters
*nondeterministic* faults. Any environment fault that is deterministic occurs in
both runs, satisfies `_identical()` (`:46`), and is rewarded with `HIGH`
confidence — the same confidence a genuine reproducible defect earns. Examples
that would repeat identically: a missing optional system library, a hardcoded
absolute path outside the mounts, a locale- or timezone-dependent assertion,
`--network none` breaking a claim that legitimately needs a network. Repetition
is being read as corroboration when it is really just determinism.

### 1e. `FailureCategory` coverage matrix

Directive asks whether each enum value is reached via the real adjudication
path, not a direct unit call.

| Category | Reached via real path? | Evidence |
|---|---|---|
| `TIMEOUT` | Yes | `test_timeout_is_inconclusive_never_false` → `adjudicate()` |
| `RESOURCE_LIMIT` | Yes | `test_resource_limit_categorised` → `adjudicate()` |
| `IMPORT_FAILURE` | Yes | `test_import_failure_categorised` → `adjudicate()` |
| `TARGET_FAILURE` | Yes | `test_fail_fail_in_target_is_false` → `adjudicate()` |
| `HARNESS_FAILURE` | Yes | `test_broken_harness_fails_gracefully` → full `run_pipeline` |
| `INSTALL_FAILURE` | Yes | `test_install_failure_makes_claims_inconclusive` → full `run_pipeline` |
| `UNKNOWN` | **No** | Only the `adjudicate()` disagreement branch is exercised, and that test does not assert the category. The `_classify_failure` fall-through at `:86` has **no test**. |

**[FIXED]** `UNKNOWN` is now reached through `adjudicate()` by
`test_unknown_category_when_claim_never_reported_a_result`. Two categories were
added by the fixes and both are reached the same way: `HARNESS_ERROR`
(finding #1) and `ENVIRONMENT_FAILURE` (finding #3).

**[NEW FINDING — reported, not fixed]** Surfaced while verifying finding #1
against `pallets/click` on real Docker, and outside the scope this directive
authorises, so it is recorded here rather than patched.

`_traceback_in_target` matches Python frames as `File "…site-packages/<pkg>…"`,
but the executor runs `pytest -v`, whose default traceback style emits
`path:line: ExcType` and **no `File "…"` frames at all**. A real run's log
contains zero of them, so the Python half of that guard effectively never
fires. Confirmed by calling `_traceback_in_target` on the real execution log
from the click run: it returns `False` even though the failing frame is
`/env/venv/lib/python3.12/site-packages/click/__init__.py:144`.

Two consequences:

1. It corroborates §1b — the `:83` fall-through, not the traceback guard, was
   what produced `FALSE` in practice. The click run reached `FALSE` on `main`
   through the fall-through even though a genuine in-target frame was present.
2. Post-fix, evidence path 1 is doing almost no work on the Python side; the
   assertion-only path carries `FALSE`. That is sound but lossy: a target that
   raises a non-assertion exception from its own code is a genuine
   `TARGET_FAILURE` and currently lands `INCONCLUSIVE`. Conservative, but a
   recall gap worth its own scoped fix (widen the pattern to pytest's
   `path:line:` form).

---

## 2. Harness synthesis / validator restrictions (`synthesize.py`)

### 2a. Sandbox constraints that already hold

A ban only earns "protective" status if removing it would create exposure
*given these*, all verified in `executor.py`:

- Execution runs `--network none` (`:238`) — no egress of any kind.
- Execution env is **only** `HOME=/tmp` (`:251`); proxy/CA vars exist solely in
  `_proxy_install_args()` for the install phase. **No secrets are present.**
- Repo mounted read-only; `/env` read-only during execution; container
  `--read-only` with a `tmpfs` scratch.
- `--cap-drop ALL`, `--security-opt no-new-privileges`, `--pids-limit 128`,
  1 CPU, 1 GB, image pinned by digest.

**Framing that follows from this:** the harness is *itself model-authored
arbitrary code*, executed on purpose. The validator cannot be the security
boundary — the sandbox is. The validator's honest role is defense-in-depth plus
steering the model toward well-formed harnesses.

### 2b. Python bans (`synthesize.py:27-57`)

| Ban | Protective or over-strict? | Rationale | Blocks legitimate claims? |
|---|---|---|---|
| `socket`, `urllib`, `http`, `requests`, `httpx`, `ftplib`, `telnetlib`, `smtplib` | **Over-strict** | `--network none` already makes egress impossible. | **Yes** — `urllib.parse` (pure string parsing) and `http.HTTPStatus` (constants) are common, safe, and banned by root-module matching. |
| `asyncio` | **Over-strict** | Grants no capability the sandbox doesn't already contain. | **Yes, severely** — async-first libraries cannot have any claim tested. |
| `multiprocessing` | Weakly protective | `--pids-limit 128` already caps fork bombs. | Minor |
| `ctypes` | **Protective** | Arbitrary memory/syscall access; worth keeping despite `cap-drop ALL`. | Rare |
| `shutil` | **Over-strict** | Repo is read-only; `/tmp` is scratch. `shutil.which`/`copy` are safe. | Some |
| `pty`, `signal` | Weakly protective | Low value either way. | Rare |
| `subprocess` | **Protective** | Keeps harnesses from shelling out; also the main thing the JS side bans. | Some (claims about CLI entry points) |
| `os.system/popen/remove/unlink/rmdir/fork/kill/execv/execve` | Mixed | Deletion targets are read-only or scratch. | Minor |
| `eval`, `exec`, `compile`, `__import__` | **Theatrical** | The harness is already arbitrary code; `eval` adds no capability. | Minor |
| `breakpoint`, `input` | **Protective** | Would hang the run until timeout. | No |

### 2c. Python validator bypasses **[REPRODUCED]**

`validate_harness_code` only matches `os.system(...)` as a literal
`ast.Attribute` on a `Name` node (`:91`). Each of the following was run through
the real validator and returned **no errors**:

| Bypass | Result |
|---|---|
| `from os import system` then `system("id")` | **BYPASS** — `os` is not in `FORBIDDEN_IMPORTS`, and the call is a bare `Name` |
| `import os as o` then `o.system("id")` | **BYPASS** — alias defeats the `node.value.id == "os"` check |
| `getattr(os, "sys" + "tem")("id")` | **BYPASS** — no literal attribute access |
| `importlib.import_module("socket")` | **BYPASS** — `importlib` is not banned |
| `import os; os.system("id")` (control) | CAUGHT |

### 2d. JS bans (`synthesize.py:99-118`)

| Ban | Protective or over-strict? | Rationale |
|---|---|---|
| `child_process`, `worker_threads`, `cluster` | **Protective** | Process spawning is the main capability worth denying. |
| `net`, `http`, `https`, `http2`, `tls`, `dgram`, `dns` | **Over-strict** | `--network none` already blocks all of it; `http.STATUS_CODES` is a safe constant table. |
| `vm`, `repl` | **Theatrical** | The harness is already arbitrary JS. |
| `eval`, `new Function` | **Theatrical** | Same. |
| `fetch`, `WebSocket`, `XMLHttpRequest` | **Over-strict** | No network exists. |
| `process.env` | **Over-strict** — confirmed in production | Execution env holds only `HOME=/tmp`; no secrets. Directly caused a permanent `INCONCLUSIVE` on the `validateEnvironment()` claim in creator-ai-hub-v2 run 3. |
| `process.binding` | **Protective** | Reaches internal bindings; cheap to keep. |

### 2e. JS validator bypasses **[REPRODUCED]**

The JS checks are regex over source text (no parser), matching only
quote-delimited specifiers (`synthesize.py:100-103`):

| Bypass | Result |
|---|---|
| ``await import(`node:child_process`)`` (template literal) | **BYPASS** — regex covers `'` and `"` only |
| `await import("node:child" + "_process")` | **BYPASS** — concatenation defeats literal matching |
| `({}).constructor.constructor("return 1")()` | **BYPASS** — `Function` gadget without the `new Function` token |
| `createRequire("/")("child_process")` | CAUGHT (specifier string still matches) |
| `await import("node:child_process")` (control) | CAUGHT |

**Over-strict candidates named for a later, separately-approved fix:**
`asyncio`, `urllib`/`http` stdlib utilities, `shutil`, `process.env`, and the
network-module bans on both sides. No change is made here.

---

## 3. Badge status derivation (`badge.py`)

### 3a. Decision table exactly as coded **[REPRODUCED]**

Evaluation order is `false` → `proven == 0` → `inconclusive` → else
(`badge.py:53-60`). Output observed by calling `build_badge` directly:

| P | F | I | U | Color | Message |
|---|---|---|---|---|---|
| 5 | 0 | 0 | 0 | `brightgreen` | `5 proven, 0 false` |
| 1 | 0 | 0 | 50 | **`brightgreen`** | `1 proven, 0 false` |
| 5 | 0 | 1 | 0 | `yellow` | `5 proven, 0 false, 1 inconclusive` |
| 1 | 0 | 20 | 0 | `yellow` | `1 proven, 0 false, 20 inconclusive` |
| 0 | 0 | 5 | 0 | **`lightgrey`** | `0 proven, 0 false, 5 inconclusive` |
| 0 | 0 | 0 | 7 | `lightgrey` | `0 proven, 0 false` |
| 5 | 1 | 0 | 0 | `red` | `5 proven, 1 false` |
| 1 | 9 | 0 | 0 | `red` | `1 proven, 9 false` |
| 5 | 1 | 3 | 0 | `red` | `5 proven, 1 false, 3 inconclusive` |

### 3b. Combinations that are misleading

| Combination | Renders | Why it is misleading | Class |
|---|---|---|---|
| `1 proven, 0 false, 50 untestable` | **brightgreen**, message omits untestable entirely | A README where 50 of 51 claims were **never executed** is indistinguishable from one fully verified. `UNTESTABLE` appears in neither color nor message. | **Misleading signal** |
| `0 proven, 0 false, 5 inconclusive` | **lightgrey**, not yellow | The `proven == 0` test precedes the `inconclusive` test, so "nothing demonstrated" wins. Defensible, but inconsistent with the documented "any INCONCLUSIVE → yellow". | Cosmetic |
| `1 proven, 0 false, 20 inconclusive` | yellow — same as 1 inconclusive | Color does not encode magnitude; only the message does. | Cosmetic |
| `1 false` vs `9 false` | both red | Color does not encode severity (message does). Reasonable for a badge. | Cosmetic |

The directive's stated concern — that a real `FALSE` and an unrelated tool
limitation "both render identically yellow" — **is not what the code does**:
`false` is tested first (`:53`), so any `FALSE` renders red regardless of
inconclusive count (row 9 above). The genuine masking problem is the inverse
one: `UNTESTABLE` is invisible in both color and message.

### 3c. Alternative policies (options only — no change made)

1. **Surface `UNTESTABLE` in the message** (e.g. `4 proven, 0 false, 12 untestable`). Smallest change; closes the biggest honesty gap.
2. **Coverage-aware color** — require executed claims to be some proportion of total before brightgreen; otherwise yellow.
3. **Split badges** — a verdict badge plus a separate coverage badge.
4. **Weighted severity** — distinguish `1 false` from `many false` by shade.
5. **Reorder `proven == 0` and `inconclusive`** so all-inconclusive renders yellow.

---

## 4. Pipeline-wide untested paths

| Location | Trigger condition | Current behavior | Tested? | Risk if wrong |
|---|---|---|---|---|
| `adjudicate.py:86` | Both runs fail, control passes, traceback outside target, `claim_passed is None` | `UNKNOWN` → `INCONCLUSIVE` | **[FIXED]** yes | Low — conservative, but the only untested verdict-producing branch |
| `executor.py:138` | `subprocess.TimeoutExpired` — container exceeds 120s/600s | Kills container, returns `timed_out=True` → `TIMEOUT` | **[FIXED]** yes (`subprocess.run` made to raise what a real hang raises) | Medium — the timeout path is a core safety guarantee, never exercised |
| `executor.py:308` | `OSError`/`TimeoutExpired` from `docker info` | `docker_available` returns `(False, str(exc))` | **[FIXED]** yes (missing binary, hung daemon, daemon error) | Low — `doctor` output only |
| `ecosystem.py:62` | Malformed `pyproject.toml` | Falls back to directory name | **[FIXED]** yes | Low — but a bad package name propagates into `_traceback_in_target`, weakening `FALSE` detection |
| `ecosystem.py:72` | Malformed/unreadable `package.json` | Falls back to directory name | Yes (`test_node_package_name_falls_back_to_dir_name`) | — |
| `llm.py:166` | Provider rejects `json_schema` response format | Demotes to `json_object` + schema in prompt | Yes (`test_providers.py`) | — |
| `llm.py:205` | Model returns non-JSON | `SchemaValidationError` → one repair | Yes | — |
| `extract.py:74` | Quote normalizes to empty (whitespace/emphasis only) | Claim dropped | Partially — `test_malformed_readme_yields_no_claims` | Low |
| `extract.py:89` | Quote absent from README even after normalization | Claim dropped with warning | Yes | Low — **but see note below** |
| `report.py:98-101` | Referenced artifact missing on disk | Renders placeholder instead of failing | **[FIXED]** yes | Low — report-only |
| `badge.py:86` | Receipt file is not valid JSON | `LieDetectorError` | Yes (`test_invalid_json_raises`) | — |
| `cli.py:503` | Any `LieDetectorError` reaches top level | Prints `error:`, exit 2 | **[FIXED]** yes, across three commands | Low |

**Note on `extract.py:89` (silent recall loss).** The drop path is tested, but
its *consequence* is unmeasured: in creator-ai-hub-v2 run 3 the claim
`"backend/ … Jest - 267 tests, ~97% statement coverage"` was discarded because
the model stitched a quote across an ASCII directory tree. The guard is correct
to reject paraphrase, but claims inside tree diagrams and code blocks are
systematically unquotable, and nothing counts or surfaces how many claims were
lost this way. The receipt records only what survived.

---

## Recommended next steps

*Separate from the findings above, per the directive. Each needs its own scoped
approval; none is applied here.*

> **[FIXED] All six shipped on `claude/edge-case-audit-fixes-jmoxkq`.** Step 1
> took the second of the two options offered — gating the fall-through on the
> failure being an assertion — rather than dropping it outright. Dropping it
> makes every behavioral `FALSE` unreachable, including the bundled demo's
> `count_words` claim, because a plain `assert target.f(x) == y` anchors its
> traceback in the harness frame; verified by reproducing the real pytest
> output. Step 4 was taken as "honestly demote": `SECURITY.md` now states that
> the sandbox is the boundary and the validator is defense-in-depth, and both
> validators were hardened as well.

1. **Close the false-accusation path (finding #1).** Make `TARGET_FAILURE`
   require positive evidence — i.e. drop the `:83` fall-through, or gate it on
   the failure being an assertion rather than an `AttributeError`/`ENOENT`/
   `TypeError` originating in the harness frame. Highest value: it protects the
   verdict that carries all the reputational weight.
2. **Strengthen the Node control assertion (finding #2).** Have it import the
   installed entry point from `/env/app`, so a broken install fails the control
   and yields `INCONCLUSIVE` rather than unlocking `FALSE`.
3. **Decide the badge's `UNTESTABLE` policy (finding #7).** Option 1 in §3c is
   a one-line message change and removes the worst honesty gap.
4. **Harden or honestly demote the validators (findings #4–#5).** Either move
   the JS checks to a real parser, or document them as model-steering plus
   defense-in-depth and state that the sandbox is the boundary. The current
   `SECURITY.md` describes only Python harness validation and is stale as of
   v0.2.0.
5. **Relax the named over-strict bans (finding #6),** starting with
   `process.env` and `asyncio`, which block whole classes of testable claims.
6. **Add tests for the untested paths in §4,** prioritizing the executor
   timeout, since it is a stated safety guarantee.

---

## Decision log

Each judgment call about scope, individually reviewable.

| # | Call | Rationale |
|---|---|---|
| 1 | Included the JS control-assertion weakness in §1 rather than §2 | It changes which verdicts are reachable, so it is adjudication risk, not synthesis style. |
| 2 | Included validator *bypasses* although the directive's §2 asks about over-strictness | Same code owns both; a validator that is simultaneously too strict and bypassable is one finding, not two. Flagged separately in the ranking. |
| 3 | Classified network-module bans as over-strict, not protective | Directive's own test: `--network none` already holds, so removing them creates no exposure. |
| 4 | Classified `eval`/`vm` bans as "theatrical" rather than over-strict or protective | They neither add security nor block realistic claims; a third label is more honest than forcing a binary. |
| 5 | Kept `ctypes`, `child_process`, `process.binding`, `input` as protective | Each grants capability the sandbox does not otherwise neutralize, or hangs the run. |
| 6 | Recorded that the directive's stated badge concern is not what the code does | Directive says a `FALSE` and an `INCONCLUSIVE` both render yellow; `false` is checked first, so this is inaccurate. Reporting the code's actual behavior is the point of §3. |
| 7 | Reported the production `safety-guards.ts` FALSE as mechanically unsound though substantively correct | Verdict correctness and mechanism soundness are different properties; conflating them is what hid this class of bug. |
| 8 | Excluded `models.py` / `utils.py` / `receipt.py` from the untested-path sweep | Directive scopes §4 to extraction → synthesis → execution → adjudication → report → badge. Receipt integrity is separately covered by `verify_receipt` and `test_receipt.py`. |
| 9 | Excluded the extraction recall gap from the severity ranking | It loses claims rather than producing wrong verdicts; noted in §4 instead. |
| 10 | Did not fix the stale `SECURITY.md` despite noticing it | Directive forbids "while I'm in here" fixes; recorded as next step 4. |
| 11 | Counted only the `_classify_failure` `UNKNOWN` fall-through as a real coverage gap | `HARNESS_FAILURE` and `INSTALL_FAILURE` are reached through full `run_pipeline` tests, satisfying the "real path" requirement. |
| 12 | Used direct function calls to reproduce findings rather than full pipeline runs | Keeps the audit read-only and free of API spend; every reproduction drives the real production function, not a reimplementation. |
