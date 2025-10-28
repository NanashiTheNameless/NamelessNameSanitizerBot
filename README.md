# discord-sanitizer-bot

Discord bot that keeps member nicknames clean and consistent, with Unicode-aware sanitization, per-server policies, and admin controls. Built for Docker and backed by PostgreSQL.

## Highlights

- Grapheme-aware sanitization using the `regex` package and `\X` clusters
- Per-guild policy: length limits, space handling, emoji toggle, and more
- Admin model with owner controls; per-guild bot admins stored in DB
- Opt-in per-guild: enable/disable the bot with a simple command
- Optional logging channel for every nickname change
- Optional bypass role so trusted members aren’t modified
- Docker- and compose-friendly deployment with Postgres
- Optional enforcement for bot accounts (disabled by default)

## Requirements

- Python 3.12+ (tested on 3.12)
- Discord bot token (with Bot scope; recommended intents: Server Members)
- PostgreSQL (Docker Compose includes a service)

## Environment variables (.env)

Required

- DISCORD_TOKEN: Discord bot token

Recommended

- DATABASE_URL: e.g., `postgresql://bot:bot@db:5432/bot` (matches the included docker-compose)
- OWNER_ID: Discord user ID of the bot owner (can manage bot admins and global actions). If unset, the bot uses a built-in fallback owner ID.
- APPLICATION_ID: Discord Application (Client) ID; optional at runtime. When set, the bot prints an invite URL on startup.

Policy defaults (used until changed per-guild via commands)

- CHECK_LENGTH: integer, default 0 — number of leading grapheme clusters to sanitize
- MIN_NICK_LENGTH: integer, default 2 — minimum allowed nickname length
- MAX_NICK_LENGTH: integer, default 32 — maximum allowed nickname length
- PRESERVE_SPACES: true|false, default true — keep or normalize spaces
- COOLDOWN_SECONDS: integer, default 60 — cooldown between edits per user
- SANITIZE_EMOJI: true|false, default true — if true, emoji are removed
- ENFORCE_BOTS: true|false, default false — default toggle for enforcing nickname rules on other bot accounts. The bot never sanitizes its own account.

Runtime

- SWEEP_INTERVAL_SEC: integer, default 120 — periodic sweep interval seconds
- SWEEP_BATCH: integer, default 256 — reserved; currently no effect
- LOG_LEVEL: DEBUG|INFO|WARNING|ERROR — overrides default logging level (INFO)

### Invite URL

Use your Application (Client) ID to invite the bot:

```text
https://discord.com/oauth2/authorize?client_id=<YOUR_APP_ID>&scope=bot%20applications.commands&permissions=134217728
```

Replace `YOUR_APP_ID` with your APPLICATION_ID. Adjust `permissions` as needed, or manage via roles.

## Run with Docker Compose

1) Copy `.env.example` to `.env` and update values. Ensure at minimum `DISCORD_TOKEN` is set. For Compose, the default `DATABASE_URL` already matches the provided Postgres service.

2) Start the stack:

```bash
docker compose up -d --build
```

This brings up Postgres and the bot. The bot syncs slash commands on startup; allow a few minutes for Discord to propagate global commands.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then edit values
python -m bot.main
```

## Permissions and intents

- Bot requires the “Manage Nicknames” permission to edit nicknames.
- For automatic sweeps and join handling, enable the “Server Members Intent”.

## How it works

The bot sanitizes the leading part of nicknames using Unicode-aware rules:

- Removes controls, format characters, and combining marks (Cf, Cc, Mn, Me)
- Optionally strips emoji; when disabled, emoji sequences are preserved
- Respects grapheme clusters so combined glyphs aren’t split
- Applies length and spacing policies

By default, other bots are not targeted. If you set `enforce_bots` to true for a guild, the bot will include bot accounts in sanitization actions. It will never attempt to change its own nickname.

Policies are stored per guild in Postgres; defaults are derived from `.env` until you run commands to set them for a guild. The bot is disabled by default per guild; a bot admin must enable it in each server.

## Command reference

Owner-only

- /add-bot-admin user:Member — add a per-guild bot admin
- /remove-bot-admin user:Member — remove a per-guild bot admin
- /nuke-bot-admins — remove all bot admins in the current server
- /global-bot-disable — disable the bot across all servers
- /global-nuke-bot-admins — remove all bot admins across all servers
- /global-reset-settings — reset sanitizer settings to defaults across all servers

Bot admin

- /enable-sanitizer — enable the bot for this server
- /disable-sanitizer — disable the bot for this server
- /set-policy key:key [value:value] [pairs:"k=v k=v ..."] — view or set policy; supports multi-update
- /set-check-count [value:int]
- /set-min-length [value:int]
- /set-max-length [value:int]
- /set-keep-spaces [value:bool]
- /set-cooldown-seconds [value:int]
- /set-emoji-sanitization [value:bool]
- /set-enforce-bots [value:bool]
- /set-logging-channel [channel:#channel] — set or view logging channel
- /clear-logging-channel — clear logging channel
- /set-bypass-role [role:@Role] — set or view bypass role
- /clear-bypass-role — clear bypass role
- /set-fallback-label [value:str] — set or view the fallback nickname used when a name is fully illegal (1–20 characters: letters, numbers, spaces, or dashes)
- /clear-fallback-label — clear the fallback nickname
- /reset-settings — reset this server’s sanitizer settings to defaults

Guild/Server Admin

- /sanitize-user member:Member — sanitize someone immediately (requires Manage Nicknames, or to be a bot admin)

Notes

- All command output is ephemeral.
- /set-policy without a value shows the current value.
- /set-policy pairs accepts keys: `check_length, min_nick_length, max_nick_length, cooldown_seconds, preserve_spaces, sanitize_emoji, logging_channel_id, bypass_role_id, fallback_label, enforce_bots`.
- Boolean inputs for commands accept true/false, yes/no, on/off, and 1/0 (case-insensitive).
- Protected (cannot be set via commands): `OWNER_ID, DISCORD_TOKEN, SWEEP_BATCH, APPLICATION_ID`.
- You can modify settings while the bot is disabled; changes will apply once you run `/enable-sanitizer` in the server.

## Troubleshooting

- Commands don’t appear
  - Allow several minutes for Discord to propagate global slash commands after startup sync
  - Ensure the bot has application.commands scope and correct permissions

- Bot not changing nicknames
  - Verify /enable-sanitizer was run in the server
  - Check the bot’s “Manage Nicknames” permission and role hierarchy
  - Confirm SWEEP_INTERVAL_SEC and that the member isn’t on cooldown
  - Ensure the user doesn’t have the bypass role and logging indicates attempts

- Database issues
  - Check DATABASE_URL and that the Postgres container is healthy
  - The bot creates/updates tables on startup; review logs for errors

## Security & privacy

- The bot does not log message content and doesn’t require the Message Content intent.
- Logging channel (if set) only receives a short notice when a nickname is changed.

Related policies:

- [Privacy Policy](<./PrivacyPolicy.md>)
- [Terms of Service](<./TermsOfService.md>)

## License & Credits

See `license.md`.

[All Major Contributors](<https://github.com/NanashiTheNameless/discord-sanitizer-bot/blob/main/CONTRIBUTORS.md>)

[All Other Contributors](<https://github.com/NanashiTheNameless/discord-sanitizer-bot/graphs/contributors>)
