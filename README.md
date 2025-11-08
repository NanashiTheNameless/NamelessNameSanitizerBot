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

- CHECK_LENGTH: integer, default 0 - number of leading grapheme clusters to sanitize
- MIN_NICK_LENGTH: integer, default 3 - minimum allowed nickname length
- MAX_NICK_LENGTH: integer, default 32 - maximum allowed nickname length
- PRESERVE_SPACES: true|false, default true - keep or normalize spaces
- COOLDOWN_SECONDS: integer, default 30 - cooldown between edits per user
- SANITIZE_EMOJI: true|false, default true - if true, emoji are removed
- ENFORCE_BOTS: true|false, default false - default toggle for enforcing nickname rules on other bot accounts. The bot never sanitizes its own account.
- FALLBACK_MODE: default|randomized|username, default default - how fallback names are chosen when a sanitized result is empty/illegal
- FALLBACK_LABEL: string, default "Illegal Name" - global default fallback label; used in fallback_mode=default and as the final fallback in username mode
- COOLDOWN_TTL_SEC: integer, default max(86400, COOLDOWN_SECONDS*10) - retention for per-user cooldown entries; older entries are purged automatically.
- OWNER_DESTRUCTIVE_COOLDOWN_SECONDS: integer, default 10 - separate cooldown applied only to destructive owner-only commands (e.g. blacklist, unblacklist, global resets) so routine admin actions aren’t throttled.

Runtime

- SWEEP_INTERVAL_SEC: integer, default 60 - periodic sweep interval seconds
- SWEEP_BATCH: integer, default 512 - reserved; currently no effect
- LOG_LEVEL: DEBUG|INFO|WARNING|ERROR - overrides default logging level (INFO)
- DM_OWNER_ON_GUILD_EVENTS: true|false, default true - if true, the bot will DM the owner on guild join/leave events

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

### Data persistence (named volume)

The included Compose file provisions a named volume `botdata` mounted at `/app/data` inside the container. This is where persistent app data lives, including the stable telemetry instance ID file at `/app/data/.telemetry_id`.

You don't need to create any host directory for this; Docker manages the named volume. To inspect the file inside the running container:

```bash
docker compose exec bot ls -l /app/data
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

### Public

- /botinfo - Display instance owner, developer attribution, source repository, policy, and legal links (ephemeral).
- /delete-my-data - Erase any of your stored data in the current server (cooldown and admin entries) immediately.

### Guild/Server Admin

- /sanitize-user [member:Member] - Force-sanitize a member now (requires Manage Nicknames permission or bot admin). Respects cooldown if configured.

### Bot admin

- /sweep-now - Sweep members and sanitize nicknames according to current policy (bot admin only). Honors bypass role and enabled state.
- /enable-sanitizer - Enable nickname enforcement for this server. Required before automatic sanitize events occur.
- /disable-sanitizer - Disable enforcement (manual commands still allowed where appropriate).
- /set-logging-channel [channel:#channel] - Set/view the channel that receives nickname update logs.
- /set-bypass-role [role:@Role] - Set/view a role whose members are never sanitized.
- /set-emoji-sanitization [value:bool] - Toggle whether emoji are stripped (true) or preserved (false).
- /set-fallback-mode [mode:str] - Set/view fallback mode (`default|randomized|username`). Controls how empty/illegal results are replaced.
- /set-keep-spaces [value:bool] - Toggle preserving original spacing (true) vs normalizing whitespace (false).
- /set-min-length [value:int] - Set/view minimum allowed nickname length (clamped ≤ 8).
- /set-max-length [value:int] - Set/view maximum allowed nickname length (clamped ≤ 32).
- /set-check-count [value:int] - Set/view number of leading grapheme clusters to sanitize (0 = full name).
- /set-cooldown-seconds [value:int] - Set/view per-user edit cooldown interval.
- /set-enforce-bots [value:bool] - Toggle sanitization for other bots (never targets itself).
- /set-fallback-label [value:str] - Set/view custom fallback label (1–20 chars: letters, numbers, spaces, dashes). Ignored in `randomized` or `username` mode except final fallback in username mode.
- /clear-logging-channel [confirm:bool] - Remove logging channel (reverts to none). Requires confirm=true.
- /clear-bypass-role [confirm:bool] - Remove bypass role (all members subject to policy again). Requires confirm=true.
- /reset-settings [server_id:str] [confirm:bool] - Reset a server’s sanitizer settings to global defaults (.env derived). server_id optional in-guild; required in DMs for remote resets. Requires confirm=true.
- /set-policy [key:key] [value:value] [pairs:k=v ...] [server_id:str] - View/update policy settings; supports multi-update with quoted values; server_id allows remote guild management (owner or that guild's bot admin); required in DMs.

### Owner-only

- /add-bot-admin [user:@User] [server_id:str] - Grant bot admin privileges for a server (current if omitted; server_id required in DMs).
- /remove-bot-admin [user:@User] [server_id:str] - Revoke bot admin privileges for a server (current if omitted; server_id required in DMs).
- /list-bot-admins [server_id:str] - List bot admins (current server if omitted; server_id required in DMs).
- /global-bot-disable [confirm:bool] - Disable enforcement across all servers immediately. Requires confirm=true.
- /global-reset-settings [confirm:bool] - Reset sanitizer settings to defaults across every server. Requires confirm=true.
- /blacklist-server [server_id:str] [reason:str] [confirm:bool] - Blacklist a server; bot auto-leaves and purges its data on join/startup.
- /unblacklist-server [server_id:str] [confirm:bool] - Remove a server from blacklist.
- /set-blacklist-reason [server_id:str] [reason:str] - Set or clear a reason for a blacklisted server.
- /list-blacklisted-servers - Enumerate all blacklisted server IDs & reasons.
- /leave-server [server_id:str] [confirm:bool] - Leave a server and delete its stored configuration/admin data.
- /dm-admin-report - DM a multi-message report of servers and bot admins.
- /dm-server-settings - DM a multi-message list of all server settings (paste-friendly key=value pairs).
- /delete-user-data [user:@User] - Purge a user’s stored data globally (cooldowns/admin entries).
- /nuke-bot-admins [server_id:str] [confirm:bool] - Remove all bot admins for a server (current server if omitted in-guild; server_id required in DMs). Requires confirm=true.
- /global-nuke-bot-admins [confirm:bool] - Remove all bot admins in all servers. Requires confirm=true.
- /global-delete-user-data [confirm:bool] - Purge ALL user data in ALL servers and announce in logging channels. Requires confirm=true.

### Notes

- All command output is ephemeral.
- Some destructive/owner commands require a confirmation boolean (confirm=true).
- All destructive owner commands respect a separate cooldown window governed by OWNER_DESTRUCTIVE_COOLDOWN_SECONDS.
- Owner-only server ID autocomplete is enforced. For /unblacklist-server, autocomplete lists only servers that are currently blacklisted (owner-only).
- /set-policy without a value shows the current value.
- /set-policy pairs accepts keys: `enabled, check_length, min_nick_length, max_nick_length, cooldown_seconds, preserve_spaces, sanitize_emoji, logging_channel_id, bypass_role_id, fallback_label, fallback_mode, enforce_bots`.
- Remote management: Add `server_id` to /set-policy or /reset-settings (and owner-only admin commands) to operate on another server. In DMs the `server_id` argument is required.
- Safety: Destructive operations (reset-settings, blacklist/unblacklist, leave-server) require `confirm=true`.
- Admin user parameter now accepts a generic user mention (@User) rather than a guild Member object for cross-guild management.
- Owner commands are now invocable from DMs (and user installs) while still enforcing OWNER_ID checks.
- In DMs, commands that act on a guild require a server_id argument.
- /set-policy values may be quoted. Quoted pairs are supported, so you can paste lines from `/dm-server-settings` directly. Example: `enabled="true" check_length="0" min_nick_length="3" max_nick_length="32" preserve_spaces="true" cooldown_seconds="30" sanitize_emoji="true" enforce_bots="false" logging_channel_id="none" bypass_role_id="none" fallback_label="Illegal Name" fallback_mode="default"`.
- Use the literal string `none` (quoted or unquoted) to clear `logging_channel_id`, `bypass_role_id`, or `fallback_label`.
- `/dm-server-settings` messages are chunked only between servers to respect Discord limits; each line per server is a complete pasteable set of pairs.
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

- If you’re the owner and destructive commands appear rate-limited unexpectedly, check OWNER_DESTRUCTIVE_COOLDOWN_SECONDS.

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
- Project name (see below)

Control via environment variables

- `NNSB_TELEMETRY_OPTOUT=1` - disable the census entirely (preferred)
- `TELEMETRY_OPTOUT=1` - alternative opt‑out variable
- `TELEMETRY_ENDPOINT=https://telemetry.namelessnanashi.dev/census` - override the POST endpoint

Defaults

- Endpoint default: <https://telemetry.namelessnanashi.dev/census>
- Project name default: the name of the repository folder (e.g., `NamelessNameSanitizerBot`)

Behavior

- The send happens once per startup and then every 2 hours (on the hour, UTC), in the background, with a very short timeout
- Network errors fail silently and never affect bot operation
- Data is minimal and cannot be used to identify you or your servers

Related policies:

- [Privacy Policy](<https://namelessnamesanitizerbot.namelessnanashi.dev/PrivacyPolicy/>)
- [Terms of Service](<https://namelessnamesanitizerbot.namelessnanashi.dev/TermsOfService/>)

## License & Credits

See [LICENSE.md](<./LICENSE.md>).

[All Major Contributors](<./CONTRIBUTORS.md>)

[All Other Contributors](<https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/graphs/contributors>)

## AI agents

If you're using an AI assistant to help with this repo, see:

- [AGENTS.md](<./AGENTS.md>) – general guidance for LLM agents working in this repo
- [CLAUDE.md](<./CLAUDE.md>) – provider-specific notes for Anthropic Claude
- [COPILOT.md](<./COPILOT.md>) – provider-specific notes for GitHub Copilot in VS Code
