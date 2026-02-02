# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Background tasks for SanitizerBot."""

import logging

import discord  # type: ignore
from discord.ext import tasks  # type: ignore

from .config import COOLDOWN_TTL_SEC, DEBUG_MODE, SWEEP_INTERVAL_SEC, GuildSettings

log = logging.getLogger("sanitizerbot")


@tasks.loop(seconds=SWEEP_INTERVAL_SEC)
async def member_sweep(self):
    for guild in list(self.guilds):
        # Periodically clear expired cooldowns to minimize data retention
        if self.db:
            try:
                await self.db.clear_expired_cooldowns(COOLDOWN_TTL_SEC)
            except Exception as e:
                log.debug("clear_expired_cooldowns failed: %s", e)

        settings = GuildSettings(guild.id)
        if self.db:
            try:
                settings = await self.db.get_settings(guild.id)
            except Exception as e:
                log.debug("Failed to get settings for guild %s: %s", guild.id, e)
        if not settings.enabled:
            continue
        processed = 0
        try:
            async for member in guild.fetch_members(limit=None):
                if member.bot and not settings.enforce_bots:
                    continue
                await self._sanitize_member(member, source="sweep")
                processed += 1
        except discord.HTTPException as e:
            if DEBUG_MODE:
                log.warning(
                    "Member sweep rate limit/HTTP error in %s: %s", guild.name, e
                )
            self._track_error(f"Member sweep HTTP error in {guild.name}: {e}", guild.id)
        if processed and DEBUG_MODE:
            log.info("Sweep processed %d members in %s", processed, guild.name)


@member_sweep.before_loop
async def before_member_sweep(self):
    await self.wait_until_ready()
