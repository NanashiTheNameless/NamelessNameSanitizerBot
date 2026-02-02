# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Admin utility helpers for SanitizerBot."""

from typing import Optional

import discord  # type: ignore

from .config import (
    COMMAND_COOLDOWN_SECONDS,
    OWNER_DESTRUCTIVE_COOLDOWN_SECONDS,
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
    # Enforce cooldown
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
    self._cmd_cooldown_last[user_id or 0] = now_ts
    return True


async def resolve_target_guild(
    self, interaction: discord.Interaction, server_id: Optional[str]
) -> Optional[int]:
    """Resolve a target guild ID from optional server_id or current interaction.

    - When in DMs and no server_id is provided, sends a friendly ephemeral message and returns None.
    - When server_id is provided but invalid, sends an error and returns None.
    - Otherwise returns the integer guild ID.
    """
    if server_id:
        try:
            return int(server_id)
        except Exception:
            try:
                await interaction.response.send_message(
                    f"'{server_id}' is not a valid server ID.", ephemeral=True
                )
            except Exception:
                pass
            return None
    if interaction.guild is None:
        try:
            await interaction.response.send_message(
                "server_id is required when used in DMs.", ephemeral=True
            )
        except Exception:
            pass
        return None
    return interaction.guild.id


async def owner_destructive_check(self, interaction: discord.Interaction) -> bool:
    """Rate-limit destructive owner commands separately using OWNER_DESTRUCTIVE_COOLDOWN_SECONDS.

    Returns True if allowed to proceed, False if blocked (and sends a friendly message).
    """
    # Only rate-limit the configured owner; others will be blocked by per-command checks anyway
    if not OWNER_ID or interaction.user.id != OWNER_ID:
        return True
    try:
        cd = int(OWNER_DESTRUCTIVE_COOLDOWN_SECONDS)
    except Exception:
        cd = 0
    if cd <= 0:
        return True
    now_ts = now()
    remain = cd - (now_ts - self._owner_destructive_last)
    if remain > 0:
        try:
            msg = f"Owner destructive cooldown active. Try again in {int(remain)}s."
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        except Exception:
            pass
        return False
    self._owner_destructive_last = now_ts
    return True
