# Using Claude in this repo (CLAUDE.md)

Claude is excellent for planning, refactoring, and generating well-structured Python. Use it with the following guardrails:

Quick-start

- Share only the minimum context required. Paste function(s) or file excerpts instead of entire secrets or config.
- Redact any secrets: DISCORD_TOKEN, DATABASE_URL credentials, OWNER_ID, etc.
- Tell Claude the constraints up front (Python 3.12, discord.py 2.x, psycopg, Docker, Unicode via `regex`/`\\X`).

Prompt template

- You are helping with NamelessNameSanitizerBot (Python 3.12, discord.py 2.x, Postgres/psycopg). Keep changes minimal and avoid behavior changes unless asked. Respect Unicode grapheme clusters using `regex` and `\\X`. Do not add heavy deps. Provide isolated diffs and a short validation plan.

Good tasks for Claude

- Designing or refactoring Discord slash commands and handlers
- Reasoning about async flows, rate limits, and cooldown logic
- Suggesting small migrations for psycopg/psycopg_pool usage
- Producing clear docs and changelog entries

Validation checklist before opening a PR

- Code compiles: `python -m compileall bot` passes locally
- Imports resolve: `python -c "import bot.main"` runs without ImportError
- Behavior unchanged unless intentionally modified (call out differences)
- Updated README/AGENTS.md if developer-facing behavior changed

Anti-patterns to avoid

- Adding frameworks or heavy dependencies for trivial tasks
- Changing table schemas without migration notes
- Reliance on privileged intents or message content (not needed)
- Over-sanitizing Unicode (we aim for grapheme-aware leading sanitization, not blanket stripping)

Notes

- Claude may prefer complete files. If so, include only public parts and mock sensitive bits. Keep prompts small and specific.

See `AGENTS.md` for general agent guidelines and `COPILOT.md` for VS Code-specific tips.
