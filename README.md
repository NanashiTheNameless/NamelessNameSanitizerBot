# NamelessNameSanitizerBot

Discord bot that keeps member nicknames clean and consistent, with Unicode-aware sanitization, per-server policies, and admin controls. Built for Docker and backed by PostgreSQL.

## Self-hosting optimized version

If you want a production-friendly, self-hosting optimized setup (pre-tuned Docker image and Compose stack), see:

- [NamelessNameSanitizerBot-Docker](<https://github.com/NanashiTheNameless/NamelessNameSanitizerBot-Docker>)

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
- COOLDOWN_SECONDS: integer, default 30 — cooldown between edits per user
- SANITIZE_EMOJI: true|false, default true — if true, emoji are removed
- ENFORCE_BOTS: true|false, default false — default toggle for enforcing nickname rules on other bot accounts. The bot never sanitizes its own account.
- COOLDOWN_TTL_SEC: integer, default max(86400, COOLDOWN_SECONDS*10) — retention for per-user cooldown entries; older entries are purged automatically.

Runtime

- SWEEP_INTERVAL_SEC: integer, default 60 — periodic sweep interval seconds
- SWEEP_BATCH: integer, default 256 — reserved; currently no effect
- LOG_LEVEL: DEBUG|INFO|WARNING|ERROR — overrides default logging level (INFO)
- DM_OWNER_ON_GUILD_EVENTS: true|false, default true — if true, the bot will DM the owner on guild join/leave events

### Invite URL

Use your Application (Client) ID to invite the bot:

```text
https://discord.com/oauth2/authorize?client_id=<YOUR_APP_ID>&scope=bot%20applications.commands&permissions=134217728
```

Replace `YOUR_APP_ID` with your APPLICATION_ID. Adjust `permissions` as needed, or manage via roles.

### Install

Click to install the bot to your server:

[Install NamelessNameSanitizerBot](<https://namelessnamesanitizerbot.namelessnanashi.dev/install/>)

## Run with Docker Compose

1) Copy `.env.example` to `.env` and update values. Ensure at minimum `DISCORD_TOKEN` is set. For Compose, the default `DATABASE_URL` already matches the provided Postgres service.

2) Start the stack (using the local Dockerfile build or switch the compose service to use the published image):

```bash
docker compose up -d --build
```

If you want a production-friendly, self-hosting optimized setup (pre-tuned Docker image and Compose stack), see:

- NamelessNameSanitizerBot-Docker: <https://github.com/NanashiTheNameless/NamelessNameSanitizerBot-Docker>

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

Public (most visible)

- /botinfo — shows instance owner, developer, and links to source, terms, and privacy
- /delete-my-data — deletes your stored data in the current server (cooldowns/admin entries)

Guild/Server Admin

- /sanitize-user [member:Member] — sanitize someone immediately (requires Manage Nicknames, or to be a bot admin)

Bot admin (sorted by typical usage)

- /sweep-now — immediately sweep and sanitize members in this server (bot admin only)
- /enable-sanitizer — enable the sanitizer in this server
- /disable-sanitizer — disable the sanitizer in this server
- /set-logging-channel [channel:#channel] — set or view logging channel
- /set-bypass-role [role:@Role] — set or view bypass role
- /set-emoji-sanitization [value:bool]
- /set-keep-spaces [value:bool]
- /set-min-length [value:int]
- /set-max-length [value:int]
- /set-check-count [value:int]
- /set-cooldown-seconds [value:int]
- /set-enforce-bots [value:bool]
- /set-fallback-label [value:str] — set or view the fallback nickname used when a name is fully illegal (1–20 characters: letters, numbers, spaces, or dashes)
- /clear-logging-channel — clear logging channel
- /clear-bypass-role — clear bypass role
- /clear-fallback-label — clear the fallback nickname
- /reset-settings — reset this server’s sanitizer settings to defaults
- /set-policy [key:key] [value:value] [pairs:"k=v k=v ..."] — view or set policy; supports multi-update

Owner-only (placed last)

- /add-bot-admin user:Member — add a bot admin (current server)
- /remove-bot-admin user:Member — remove a bot admin (current server)
- /list-bot-admins — list all bot admins in a server (in DMs, pass server_id)
- /dm-admin-report — DM the owner a report of all servers the bot is in and the bot admins for each
- /global-bot-disable — disable the bot across all servers
- /global-reset-settings — reset sanitizer settings to defaults across all servers
- /nuke-bot-admins — remove all bot admins in the current server
- /global-nuke-bot-admins — remove all bot admins across all servers
- /blacklist-server [server_id:str] [reason:str] [confirm:bool] — add a server to the blacklist; the bot will auto-leave it on join/startup and delete stored data
- /unblacklist-server [server_id:str] [confirm:bool] — remove a server from the blacklist
- /set-blacklist-reason [server_id:str] [reason:str] — set/clear blacklist reason for a server
- /list-blacklisted-servers — list all blacklisted server IDs
- /leave-server [server_id:str] [confirm:bool] — leave the specified server and delete that server’s stored data
- /delete-user-data [user:@User] — delete that user's stored data across all servers (cooldowns/admin entries)
- /global-delete-user-data — delete ALL user data across all servers and announce in configured logging channels

Notes

- All command output is ephemeral.
- Some destructive/owner commands require a confirmation boolean (confirm=true).
- Owner-only server ID autocomplete is enforced. For /unblacklist-server, autocomplete lists only servers that are currently blacklisted (owner-only).
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

See [SECURITY.md](<./SECURITY.md>) in this repo and the policies on the project site:

- The bot does not log message content and doesn’t require the Message Content intent.
- Logging channel (if set) only receives a short notice when a nickname is changed.
- Minimal data storage: per-guild config and per-user cooldown timestamps. Cooldowns are purged automatically after COOLDOWN_TTL_SEC.
- Users can request deletion via /delete-my-data; bot owners can execute /delete-user-data or /global-delete-user-data when legally required.

### Telemetry (opt-out)

This project includes a tiny, privacy‑respecting census to estimate how many people self‑host it. It is enabled by default and can be disabled at any time. The census only sends minimal, non‑identifying data and never includes user content, guild info, or secrets.

What is sent

- A stable, anonymous instance identifier hashed with SHA‑256 (never the raw value)
- The current date (UTC) in `YYYY‑MM‑DD` format
- Python version and OS platform
- Project version (if a local `VERSION` file exists)
- Project name (see below)

Control via environment variables

- `NNSB_TELEMETRY_OPTOUT=1` — disable the census entirely (preferred)
- `TELEMETRY_OPTOUT=1` — alternative opt‑out variable
- `TELEMETRY_ENDPOINT=<https://telemetry.namelessnanashi.dev/census>` — override the POST endpoint
- `PROJECT_NAME=YourProject` — set the project name attached to the payload

Defaults

- Endpoint default: <https://telemetry.namelessnanashi.dev/census>
- Project name default: the name of the repository folder (e.g., `NamelessNameSanitizerBot`)

Behavior

- The send happens once per startup and then every 24 hours, in the background, with a very short timeout
- Network errors fail silently and never affect bot operation
- Data is minimal and cannot be used to identify you or your servers

Related policies:

- [Privacy Policy](<https://namelessnamesanitizerbot.namelessnanashi.dev/PrivacyPolicy/>)
- [Terms of Service](<https://namelessnamesanitizerbot.namelessnanashi.dev/TermsOfService/>)

## License & Credits

See [license.md](<./license.md>).

[All Major Contributors](<./CONTRIBUTORS.md>)

[All Other Contributors](<https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/graphs/contributors>)

## AI agents

If you're using an AI assistant to help with this repo, see:

- [AGENTS.md](<./AGENTS.md>) – general guidance for LLM agents working in this repo
- [CLAUDE.md](<./CLAUDE.md>) – provider-specific notes for Anthropic Claude
- [COPILOT.md](<./COPILOT.md>) – provider-specific notes for GitHub Copilot in VS Code
