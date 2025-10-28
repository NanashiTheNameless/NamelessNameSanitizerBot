# Privacy Policy

Last updated: 2025-10-28

This Privacy Policy explains what information the discord-sanitizer-bot ("the Bot") processes and how it is used. By using the Bot, you agree to this Policy.

## Summary

- The Bot does not read or store message content.
- The Bot processes basic Discord metadata in memory to operate (e.g., user IDs, guild IDs, nicknames) and may store minimal per-guild configuration in a database.
- Optionally, the Bot can post nickname-change notifications to a configured logging channel in your server.

## Data the Bot Processes

- Guild and Member metadata from Discord APIs (e.g., guild ID, user ID, roles, current nickname, permissions) to determine whether and how to sanitize nicknames.
- Per-guild configuration settings: policy values, admin lists, feature toggles, optional logging channel ID, and optional bypass role ID.
- Cooldown timestamps for users (stored as simple timestamps keyed by user ID) to avoid excessive nickname edits.

The Bot does not process or store message content and does not require the Message Content intent.

## Data Storage and Retention

- Per-guild settings and bot admin lists are stored in a PostgreSQL database.
- Cooldown data is stored in a local JSON file inside the container (ephemeral unless persisted by the operator).
- No message content is stored.
- Configuration is retained until modified or deleted by server administrators or the operator.
- Cooldown entries expire automatically based on the configured cooldown interval.

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
