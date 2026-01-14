# Privacy Policy

Last updated: 2026-01-11 (YYYY-MM-DD)

This page explains what NamelessNameSanitizerBot ("The Bot") does with data.
By using The Bot, you agree to this policy.

## Quick summary

- The Bot does not read or store message content.
- The Bot only uses basic Discord metadata needed to work (e.g., guild ID, user ID, roles, current nickname) and per-guild settings.
- Optional features may post nickname-change notices in a server log channel and/or DM the bot owner about guild join/leave events.
- Anonymous, opt-out self-host telemetry counts how many instances of The Bot are being run at any given time. It never includes any PII, message content, User IDs, or Guild IDs.

## What The Bot processes

- Discord metadata to decide if and how to sanitize nicknames.
- Per-guild settings (policy values, admin lists, feature toggles, optional logging channel ID, optional bypass role ID, and fallback label).
- Minimal operational data such as cooldown timestamps (per user) and guild blacklist entries (guild ID, optional name, optional reason).

The Bot does not process or store message content and The Bot does not require the Message Content intent.

## Storage and retention

- Per-guild settings/admin lists are stored in a database and kept until you change or delete them.
- Cooldown entries are auto-purged after a delay.
- If a guild is blacklisted, the bot leaves and deletes stored settings/admins for that guild; blacklist entries remain until removed.

## Sharing

- We do not sell or share any data with third parties.
- Hosting/infrastructure providers used by the operator may process data under their own policies.

## Self-hosting Telemetry (opt-out)

- Purpose: privacy-preserving census of self-hosted usage.
- When: once on startup and then every 2 hours (UTC), in the background.
- Data sent: SHA-256 hash of a randomly generated instance ID (not a guild/user ID), Current UTC date (YYYY-MM-DD), Static Project name, and a static count value of 1.
- Public stats: <https://telemetry.namelessnanashi.dev/>

## Your controls

- Bot Admins: Enable/disable the bot, change settings, configure logging channel and bypass role.
- Users: Run `/delete-my-data` to remove your data in a server.
- Bot Owner: Delete user data across all servers (`/delete-user-data`, `/global-delete-user-data`).

## Security

- We apply reasonable measures to protect stored configuration data. Operators should keep credentials (e.g., `DISCORD_TOKEN`, `DATABASE_URL`) and access to logs/volumes secure.

## Where processing happens

Data is processed where the operator hosts the Bot and its database. Operators are responsible for any legal compliance in their region.

## Contact

Open a GitHub issue or contact the bot owner/operator. You can pull up the relevant information using the `/botinfo` command.

## Changes

We may update this policy. Continued use means you accept the changes.
