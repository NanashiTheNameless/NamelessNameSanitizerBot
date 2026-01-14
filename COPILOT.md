# Using GitHub Copilot in this repo (COPILOT.md)

This guide focuses on Copilot Chat in VS Code and GitHub PRs.

Set expectations in your prompt

- Language/runtime: Python 3.12, discord.py 2.x
- DB: Postgres via psycopg/psycopg_pool
- Unicode: use `regex` with `\\X` for grapheme clusters
- Ops: Docker-first; avoid heavy dependencies

Effective workflows

- Inline chat: Ask Copilot to explain a function, propose a minimal fix, or generate tests. Keep the scope tight.
- PR review: Use "/explain" and "/tests" style prompts to generate summaries and test ideas; verify results manually.
- Task lists: Provide a short checklist and ask Copilot to apply edits file-by-file.

Safety and compliance

- Never paste secrets (DISCORD_TOKEN, DATABASE_URL). Redact values.
- Don't accept large sweeping refactors in one go. Prefer small, reviewable diffs.
- Stick to licenses compatible with MIT; avoid code pastes from random sources.

Validation before commit

- Run a quick compile/import check locally.
- Ensure commands and permissions remain correct in Discord handlers.
- If behavior changes, update README and provide notes in the PR.

When in doubt

- Open a draft PR and request feedback.

See also: `AGENTS.md` for general guidelines and `CLAUDE.md` for provider tips.
