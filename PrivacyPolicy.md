# Privacy Policy

Last updated: 2025-11-01

This Privacy Policy explains what information NamelessNameSanitizerBot ("the Bot") processes and how it is used. By using the Bot, you agree to this Policy.

## Summary

- The Bot does not read or store message content.
- The Bot processes basic Discord metadata in memory to operate (e.g., user IDs, guild IDs, nicknames) and may store minimal per-guild configuration in a database.
- Optionally, the Bot can post nickname-change notifications to a configured logging channel in your server.
- The Bot can optionally DM the bot owner about guild join/leave events when enabled by the operator (DM_OWNER_ON_GUILD_EVENTS).

## Data the Bot Processes

- Guild and Member metadata from Discord APIs (e.g., guild ID, user ID, roles, current nickname, permissions) to determine whether and how to sanitize nicknames.
- Per-guild configuration settings: policy values, admin lists, feature toggles, optional logging channel ID, optional bypass role ID, and optional fallback label.
- Cooldown timestamps for users (stored as simple timestamps keyed by user ID) to avoid excessive nickname edits.
- Blacklist entries for guilds the operator chooses to block (guild ID, optional guild name, optional textual reason).

The Bot does not process or store message content and does not require the Message Content intent.

## Data Storage and Retention

- Per-guild settings and bot admin lists are stored in a PostgreSQL database.
- Cooldown data is stored in a database table and purged automatically after a retention period configured by `COOLDOWN_TTL_SEC`.
- Blacklist entries (guild ID, name, reason) are stored until removed. If the bot encounters a blacklisted guild, it will auto-leave and delete any stored per-guild settings/admins for that guild.
- No message content is stored.
- Configuration is retained until modified or deleted by server administrators or the operator.
- Additionally, command usage may be rate-limited by a short per-user cooldown (see below) that records only a timestamp in memory; owners and bot admins are exempt.

## Data Sharing

- The Bot does not sell or share data with third parties.
- Data may be processed by the hosting provider or infrastructure used by the operator (e.g., your server, container platform, or managed database), subject to their own policies.

## Legal Basis and Purpose

- The purpose of processing is to enforce nickname policy and provide administrative controls per server.
- The legal basis is the server administrators’ and operator’s legitimate interest in moderation and community standards and/or consent of the server owner.

## User Rights and Controls

- Server administrators can:
  - Enable or disable the Bot per guild.
  - Update policy settings.
  - Configure a logging channel and bypass role.
  - Manage bot admins.
- Users may contact the server administrators or the operator to request changes to their nickname or to raise concerns.
  - Users can run `/delete-my-data` to delete any of their stored entries in a server (cooldowns/admin entries). The bot owner may run `/delete-user-data` to delete a specific user's data across servers, and `/global-delete-user-data` deletes ALL user data across servers and may announce the action in configured logging channels for audit transparency.
  - The bot owner can blacklist a guild; when doing so, the bot deletes stored settings/admins for that guild and (if present) leaves the guild.

  ## Command Cooldown

  - The bot may enforce a short per-user command cooldown configured by the operator via `COMMAND_COOLDOWN_SECONDS`. This is intended to prevent accidental repeated invocation and does not persist beyond process memory. The bot owner and bot admins are exempt from this cooldown.

## International Transfers

- Data may be processed in the region where the operator hosts the Bot and database. Operators should ensure compliance with any applicable data transfer requirements.

## Security

- Reasonable administrative and technical measures are used to protect stored configuration data (e.g., access controls, limiting permissions of the Bot account).
- Operators should keep credentials (DISCORD_TOKEN, DATABASE_URL) secure and restrict access to logs and volumes.

## Children’s Data

- The Bot is intended for Discord servers and communities that comply with Discord’s own age requirements. The Bot does not knowingly process personal data of children beyond Discord’s provided metadata.

## Contact

- For privacy questions or data requests, contact the server owner or the repository owner via GitHub.

## Changes to this Policy

- We may update this Policy from time to time. Continued use of the Bot indicates acceptance of the updated Policy.
