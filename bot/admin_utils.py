# This software is licensed under NNCL v1.4 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Admin utility helpers for SanitizerBot."""

import discord  # type: ignore

from .config import (
    COMMAND_COOLDOWN_SECONDS,
    OWNER_ID,
)
from .helpers import now


def is_guild_admin(self, member: discord.Member) -> bool:
    return bool(member.guild_permissions.manage_nicknames)


async def is_bot_admin(self, guild_id: int, user_id: int) -> bool:
    if OWNER_ID and user_id == OWNER_ID:
        return True
    return await self.db.is_admin(guild_id, user_id)


async def command_cooldown_check(self, interaction: discord.Interaction) -> bool:
    """Global per-user command cooldown, bypassed by owner and bot admins.
    Controlled via COMMAND_COOLDOWN_SECONDS; disabled when <= 0.
    Only checks cooldown, does not apply it (applied after successful execution).
    """
    try:
        cd = int(COMMAND_COOLDOWN_SECONDS)
    except Exception:
        cd = 0
    if cd <= 0:
        return True
    user = getattr(interaction, "user", None)
    user_id = getattr(user, "id", None)
    # Owner bypass
    if OWNER_ID and user_id == OWNER_ID:
        return True
    # Bot admin bypass (per-guild)
    try:
        if interaction.guild:
            if await self.db.is_admin(interaction.guild.id, user_id):  # type: ignore[arg-type]
                return True
    except Exception:
        pass
    # Check cooldown
    now_ts = now()
    last = self._cmd_cooldown_last.get(user_id or 0, 0.0)
    remain = cd - (now_ts - last)
    if remain > 0:
        # Best-effort friendly message
        try:
            msg = f"You're doing that too fast. Try again in {int(remain)}s."
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        except Exception:
            pass
        return False
    return True
