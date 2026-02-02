# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Event handlers for SanitizerBot."""

import logging
from typing import Optional

import discord  # type: ignore

from .config import APPLICATION_ID, DEBUG_MODE, GuildSettings

log = logging.getLogger("sanitizerbot")


async def on_ready(self):
    if self.db:
        try:
            await self.db.connect()
            await self.db.init()
        except Exception as e:
            log.error("Database initialization failed: %s", e)
            self._config_error = True
            self._status_messages = [
                {
                    "text": "500 Database Connection Failed",
                    "duration": 30,
                    "type": "watching",
                }
            ]
            self._track_error(f"Database initialization failed: {e}")
    if DEBUG_MODE:
        gids = ", ".join(f"{g.name}({g.id})" for g in self.guilds)
        log.info("[STARTUP] Logged in as %s (%s)", self.user, self.user.id)
        log.info("[STARTUP] Connected guilds: %s", gids or "<none>")
    else:
        log.info("[STARTUP] Logged in as %s (%s)", self.user, self.user.id)
        log.info("[STARTUP] Connected to %d guild(s)", len(self.guilds))

    if APPLICATION_ID:
        invite = f"https://discord.com/oauth2/authorize?client_id={APPLICATION_ID}&scope=bot%20applications.commands&permissions=134217728&integration_type=0"
        log.info(f"[INFO] Bot invite link: {invite}")
    else:
        log.warning(
            "[INFO] APPLICATION_ID is not set. Set it in your .env to generate a bot invite link."
        )
    if not self.guilds:
        log.warning("[STATUS] No guilds detected. Bot is not in any servers.")

    # Auto-leave any blacklisted guilds if present
    if self.db:
        try:
            bl = await self.db.list_blacklisted_guilds()
            bl_set = {row[0] for row in bl}
        except Exception:
            bl_set = set()
        if bl_set:
            attempt = 0
            for g in list(self.guilds):
                if g.id in bl_set:
                    attempt += 1
                    try:
                        # Update stored name for this blacklisted guild (keep existing reason)
                        try:
                            await self.db.add_blacklisted_guild(g.id, None, g.name)
                        except Exception:
                            pass
                        # Always delete stored data
                        try:
                            await self.db.clear_admins(g.id)
                            await self.db.reset_guild_settings(g.id)
                        except Exception:
                            pass
                        await g.leave()
                        log.info(
                            "[BLACKLIST] Left blacklisted guild %s (%s)",
                            g.name,
                            g.id,
                        )
                    except Exception as e:
                        log.debug("Failed leaving blacklisted guild %s: %s", g.id, e)
            if attempt:
                log.info(
                    "[BLACKLIST] Processed %d blacklisted guild(s) on startup.",
                    attempt,
                )

    # Purge data for servers the bot is not in (defensive cleanup)
    if self.db:
        try:
            known_ids = {g.id for g in self.guilds}
            removed = await self.db.purge_unknown_guilds(known_ids)
            if removed:
                log.info(
                    "[CLEANUP] Purged stored data for %d unknown guild(s).", removed
                )
        except Exception as e:
            log.debug("Failed purging unknown guild data: %s", e)

    log.info("[STATUS] Starting status cycling task.")
    import asyncio

    asyncio.create_task(self.status_cycle())

    log.info("[STATUS] Starting member sweep background task.")
    self.member_sweep.start()  # type: ignore

    # Send any pending owner DMs that were queued during initialization
    if hasattr(self, "_pending_owner_dms") and self._pending_owner_dms:
        for dm_content in self._pending_owner_dms:
            try:
                await self._dm_owner(dm_content)
            except Exception as e:
                log.debug(f"Failed to send pending owner DM: {e}")
        self._pending_owner_dms.clear()


async def on_guild_join(self, guild: discord.Guild):
    if DEBUG_MODE:
        log.info(f"[EVENT] Bot joined new guild: {guild.name} ({guild.id})")
    # If blacklisted, DM owner with reason and immediately leave; otherwise send generic join DM
    if self.db:
        try:
            if await self.db.is_guild_blacklisted(guild.id):
                # Update stored name for this blacklisted guild (keep reason)
                try:
                    await self.db.add_blacklisted_guild(guild.id, None, guild.name)
                except Exception:
                    pass
                # DM owner a specific message for blacklisted join
                reason_txt: Optional[str] = None
                try:
                    info = await self.db.get_blacklisted_guild(guild.id)
                    reason_txt = info[1] if info else None
                except Exception:
                    pass
                await self._dm_owner(
                    f"Joined blacklisted guild: {guild.name} ({guild.id})"
                    + (
                        f" - reason: {reason_txt}"
                        if (reason_txt and str(reason_txt).strip())
                        else ""
                    )
                    + "; leaving now."
                )
                try:
                    await self.db.clear_admins(guild.id)
                    await self.db.reset_guild_settings(guild.id)
                except Exception:
                    pass
                try:
                    await guild.leave()
                    if DEBUG_MODE:
                        log.info(
                            "[BLACKLIST] Immediately left blacklisted guild %s (%s)",
                            guild.name,
                            guild.id,
                        )
                except Exception as e:
                    log.debug(
                        "Failed to leave blacklisted guild on join %s: %s",
                        guild.id,
                        e,
                    )
                return
        except Exception:
            pass
    # Not blacklisted (or no DB)
    await self._dm_owner(f"Joined guild: {guild.name} ({guild.id})")


async def on_guild_remove(self, guild: discord.Guild):
    if DEBUG_MODE:
        log.info(f"[EVENT] Bot left guild: {guild.name} ({guild.id})")
    # When leaving a guild, proactively delete stored data for it
    if self.db:
        try:
            await self.db.clear_admins(guild.id)
            await self.db.reset_guild_settings(guild.id)
            if DEBUG_MODE:
                log.info(
                    "[CLEANUP] Cleared stored data after leaving guild %s (%s)",
                    guild.name,
                    guild.id,
                )
        except Exception as e:
            log.debug(
                "Failed to clear stored data for removed guild %s: %s", guild.id, e
            )
    # Notify owner that the bot left the guild
    await self._dm_owner(f"Left guild: {guild.name} ({guild.id})")


async def on_member_join(self, member: discord.Member):
    # Don't sanitize if a configuration error is active
    if self._config_error:
        return
    if member.bot:
        try:
            settings = (
                await self.db.get_settings(member.guild.id)
                if self.db
                else GuildSettings(member.guild.id)
            )
        except Exception:
            settings = GuildSettings(member.guild.id)
        if not settings.enforce_bots:
            return
    await self._sanitize_member(member, source="join")


async def on_message(self, message: discord.Message):
    if message.guild is None:
        return

    if message.author.bot:
        try:
            settings = (
                await self.db.get_settings(message.guild.id)
                if self.db
                else GuildSettings(message.guild.id)
            )
        except Exception:
            settings = GuildSettings(message.guild.id)
        if not settings.enforce_bots:
            return

    m = message.author
    if isinstance(m, discord.Member):
        await self._sanitize_member(m, source="message")
