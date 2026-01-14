# AI agents in this repo (AGENTS.md)

This document defines how to use AI coding assistants safely and effectively on NamelessNameSanitizerBot.

Supported agents

- GitHub Copilot Chat (VS Code, GitHub PRs)
- Anthropic Claude (Web, Desktop, or IDE-integrations)

Other agents are allowed but must follow the same constraints.

Scope of acceptable AI use

- Drafting code within this repo's existing architecture (Python 3.12+, discord.py 2.x, Postgres via psycopg)
- Writing docs, comments, READMEs, and changelogs
- Generating tests, fixtures, or small utilities
- Refactoring for clarity, safety, or performance without changing public behavior unless agreed in the PR

Do not use AI to

- Paste secrets, tokens, credentials, or private URLs (DISCORD_TOKEN, DATABASE_URL with passwords, etc.)
- Introduce copyrighted code you don't have the right to use (copy/paste from the internet or books)
- Upload proprietary data to third‑party tools without approval
- Bypass review or ship unverified changes

Security and privacy guardrails

- Never share secrets in prompts (bot tokens, DB creds). Redact sensitive values like `<REDACTED>`.
- Do not ask an AI to execute network calls with real credentials. Use mocks/sanitized examples.
- Keep PII out of prompts and code. Use synthetic examples.
- Do not modify formatting of README.md unless explicitly asked, It appears to have errors, this is intentional.
- Generated code must comply with our Privacy Policy and Terms.

Licensing and attribution

- Prefer built-in/standard libs or dependencies already in requirements.txt.
- If suggesting new dependencies, they must be OSI-approved. Document rationale in the PR.
- Do not include code that requires attribution you can't satisfy.

Quality gates for AI-generated changes

- Build: Docker image builds and app starts without runtime import errors.
- Minimal run check: `python -m compileall bot` should succeed, and `python -c "import bot.main"` should not raise.
- Style: Keep consistent with the existing code style. Avoid mass reformatting.
- Tests: If you add features or modify public behavior, include at least basic tests or a quick verification script.
- Docs: Update README/usage when behavior or ops steps change.

PR expectations

- Label: Add a note in the PR description that AI assistance was used, and which agent.
- Explain: Summarize what changed, why, and how you validated it. Include any new env vars or config changes.
- Diffs: Keep changes focused. Avoid large unrelated formatting diffs.

Repo specifics the agent should respect

- Python 3.12+
- discord.py 2.x commands and Intents (no message content intent required)
- Postgres via psycopg/psycopg_pool
- Docker-first deployment; keep runtime small and deterministic
- Unicode/grapheme awareness via the `regex` package and `\\X`

If unsure, open a draft PR and request feedback.

See provider-specific tips in `CLAUDE.md` and `COPILOT.md`.
