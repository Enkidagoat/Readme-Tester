Badge workflow and OpenAI-compatible provider configuration
=========================================================

This repository's GitHub Actions `badge.yml` workflow can run the self-audit
and publish the generated `badge.json` + Truth Report to the `badges` branch.

Provider selection
------------------
- **Anthropic (default)**: set the `ANTHROPIC_API_KEY` repository secret.
- **OpenAI-compatible (Featherless, OpenAI, OpenRouter, etc.)**: set
  `OPENAI_API_KEY` or `FEATHERLESS_API_KEY`, and when required, set
  `OPENAI_BASE_URL` to point at the provider's API endpoint (for example,
  `https://api.featherless.ai/v1`). The workflow will detect either Anthropic
  or an OpenAI-compatible key and pass `--provider openai` to `liedetector`.

Secrets to add to the repo (Settings -> Secrets):

- `ANTHROPIC_API_KEY` — for Anthropic provider (optional)
- `OPENAI_API_KEY` — for OpenAI-compatible providers (optional)
- `FEATHERLESS_API_KEY` — alternative key name supported by the CLI
- `OPENAI_BASE_URL` — required when using Featherless or non-OpenAI hosts
 - `OPENAI_MODEL` — optional: set to a model name your provider supports when
   the default (`gpt-4o`) is not available (for example, `gpt-3.5-turbo` or a
   provider-specific model name).

Notes
-----
- If no provider secret is present, the `badge.yml` workflow will skip the
  self-audit and emit a warning.
- Locally you can run the same flow with the CLI:

```bash
# with Anthropic
liedetector run "https://github.com/<owner>/<repo>"

# with OpenAI-compatible provider
OPENAI_API_KEY=... OPENAI_BASE_URL=... liedetector run "https://github.com/<owner>/<repo>" --provider openai
```

This file documents the minimal steps needed to enable the badge self-audit
using OpenAI-compatible providers (Featherless). If you'd like, I can add a
short paragraph to `README.md` or `RUN_GUIDE.md` linking to this file.
