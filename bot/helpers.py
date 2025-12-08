# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
import time
from typing import Optional

import discord  # type: ignore

from .config import OWNER_DESTRUCTIVE_COOLDOWN_SECONDS, OWNER_ID


def now() -> float:
    return time.time()


async def resolve_target_guild(
    interaction: discord.Interaction, server_id: Optional[str]
) -> Optional[int]:
    """Resolve target guild (server) id for commands supporting cross-guild (server) operations.

    Sends ephemeral error messages to the interaction response when invalid/missing.
    Returns the guild (server) id or None if resolution failed (message already sent).
    """
    if server_id:
        try:
            return int(server_id)
        except Exception:
            try:
                await interaction.response.send_message(
                    f"'{server_id}' is not a valid guild (server) ID.", ephemeral=True
                )
            except Exception:
                pass
            return None
    if interaction.guild is None:
        try:
            await interaction.response.send_message(
                "server_id is required when used in DMs for guild (server) operations.",
                ephemeral=True,
            )
        except Exception:
            pass
        return None
    return interaction.guild.id


async def owner_destructive_check(
    bot: "discord.Client", interaction: discord.Interaction
) -> bool:
    """Rate-limit destructive owner-only commands.

    Uses OWNER_DESTRUCTIVE_COOLDOWN_SECONDS and stores state on bot._owner_destructive_last.
    Returns True if allowed, False if blocked (sends ephemeral notice).
    """
    if not OWNER_ID or interaction.user.id != OWNER_ID:
        return True
    try:
        cd = int(OWNER_DESTRUCTIVE_COOLDOWN_SECONDS)
    except Exception:
        cd = 0
    if cd <= 0:
        return True
    last = getattr(bot, "_owner_destructive_last", 0.0) or 0.0
    now_ts = now()
    remain = cd - (now_ts - last)
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
    setattr(bot, "_owner_destructive_last", now_ts)
    return True
