# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Background tasks for SanitizerBot."""

import asyncio
import logging

import discord  # type: ignore
from discord.ext import tasks  # type: ignore

from .config import (
    COOLDOWN_TTL_SEC,
    DEBUG_MODE,
    SWEEP_FETCH_MAX_RETRIES,
    SWEEP_GUILD_DELAY_SEC,
    SWEEP_INTERVAL_SEC,
    SWEEP_RETRY_BASE_SEC,
    GuildSettings,
)

log = logging.getLogger("sanitizerbot")

_RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}


def _is_retryable_http_exception(exc: discord.HTTPException) -> bool:
    return getattr(exc, "status", None) in _RETRYABLE_HTTP_STATUSES


def _compute_retry_delay(exc: discord.HTTPException, attempt: int) -> float:
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(retry_after, (float, int)) and retry_after > 0:
        return float(retry_after)
    return min(SWEEP_RETRY_BASE_SEC * (2**attempt), 30.0)


async def sweep_guild_members(self, guild: discord.Guild, settings: GuildSettings, source: str):
    """Sweep one guild with bounded retries for transient HTTP failures."""
    for attempt in range(SWEEP_FETCH_MAX_RETRIES + 1):
        processed = 0
        changed = 0
        try:
            async for member in guild.fetch_members(limit=None):
                if member.bot and not settings.enforce_bots:
                    continue
                did_change = await self._sanitize_member(member, source=source)
                if did_change:
                    changed += 1
                processed += 1
            return processed, changed, None
        except discord.HTTPException as e:
            if not _is_retryable_http_exception(e) or attempt >= SWEEP_FETCH_MAX_RETRIES:
                return processed, changed, e
            delay = _compute_retry_delay(e, attempt)
            log.warning(
                "Member sweep retry in %s due to HTTP %s (attempt %d/%d, waiting %.1fs): %s",
                guild.name,
                getattr(e, "status", "unknown"),
                attempt + 1,
                SWEEP_FETCH_MAX_RETRIES,
                delay,
                e,
            )
            await asyncio.sleep(delay)
    return 0, 0, None


@tasks.loop(seconds=SWEEP_INTERVAL_SEC)
async def member_sweep(self):
    # Periodically clear expired cooldowns to minimize data retention.
    # Run once per sweep cycle (not once per guild) to avoid redundant DB work.
    if self.db:
        try:
            await self.db.clear_expired_cooldowns(COOLDOWN_TTL_SEC)
        except Exception as e:
            log.debug("clear_expired_cooldowns failed: %s", e)

    if self._sweep_lock.locked():
        log.debug("Member sweep skipped because another sweep is already running.")
        return

    async with self._sweep_lock:
        guilds = list(self.guilds)
        for idx, guild in enumerate(guilds):
            settings = GuildSettings(guild.id)
            if self.db:
                try:
                    settings = await self.db.get_settings(guild.id)
                except Exception as e:
                    log.debug("Failed to get settings for guild %s: %s", guild.id, e)
            if not settings.enabled:
                continue

            processed, changed, sweep_error = await sweep_guild_members(
                self, guild, settings, source="sweep"
            )
            if sweep_error:
                if DEBUG_MODE:
                    log.warning(
                        "Member sweep rate limit/HTTP error in %s: %s",
                        guild.name,
                        sweep_error,
                    )
                # Mark as non-critical - upstream/rate-limit errors shouldn't trigger red status
                self._track_error(
                    f"Member sweep HTTP error in {guild.name}: {sweep_error}",
                    guild.id,
                    critical=False,
                )
            elif processed and DEBUG_MODE:
                log.info(
                    "Sweep processed %d members and changed %d in %s",
                    processed,
                    changed,
                    guild.name,
                )

            if SWEEP_GUILD_DELAY_SEC > 0 and idx < len(guilds) - 1:
                await asyncio.sleep(SWEEP_GUILD_DELAY_SEC)


@member_sweep.before_loop
async def before_member_sweep(self):
    await self.wait_until_ready()
