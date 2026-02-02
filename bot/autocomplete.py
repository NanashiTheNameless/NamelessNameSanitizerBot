# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Autocomplete helpers for SanitizerBot commands."""

import discord  # type: ignore

from .config import OWNER_ID


async def ac_policy_key(self, interaction: discord.Interaction, current: str):
    current_l = (current or "").lower()
    choices = [
        c
        for c in self._policy_keys
        if current_l in c.name.lower() or current_l in c.value.lower()
    ]
    return choices[:25]


async def ac_bool_value(self, interaction: discord.Interaction, current: str):
    opts = [
        discord.app_commands.Choice(name="true", value="true"),
        discord.app_commands.Choice(name="false", value="false"),
        discord.app_commands.Choice(name="yes", value="yes"),
        discord.app_commands.Choice(name="no", value="no"),
        discord.app_commands.Choice(name="on", value="on"),
        discord.app_commands.Choice(name="off", value="off"),
        discord.app_commands.Choice(name="1", value="1"),
        discord.app_commands.Choice(name="0", value="0"),
    ]
    current_l = (current or "").lower()
    return [c for c in opts if current_l in c.name][:25]


async def ac_int_value(self, interaction: discord.Interaction, current: str):
    suggestions = ["0", "1", "2", "3", "5", "10", "15", "30", "60"]
    if current and current.isdigit():
        suggestions = [current] + [s for s in suggestions if s != current]
    return [discord.app_commands.Choice(name=s, value=int(s)) for s in suggestions][:25]


async def ac_check_count_value(self, interaction: discord.Interaction, current: str):
    # Curated suggestions for check_length
    base = ["0", "4", "6", "8", "10", "18"]
    current_l = (current or "").strip()
    vals = base
    if current_l and current_l.isdigit():
        vals = [current_l] + [v for v in base if v != current_l]
    return [discord.app_commands.Choice(name=v, value=int(v)) for v in vals][:25]


async def ac_min_length_value(self, interaction: discord.Interaction, current: str):
    # Only allow suggestions up to 8 for min length
    vals = [str(i) for i in range(0, 9)]
    current_l = (current or "").strip()
    if current_l and current_l.isdigit():
        # Show the typed value first (even if > 8, we will still validate on submit)
        vals = [current_l] + [v for v in vals if v != current_l]
    return [discord.app_commands.Choice(name=v, value=int(v)) for v in vals][:25]


async def ac_max_length_value(self, interaction: discord.Interaction, current: str):
    # Provide curated choices up to 32 for max length
    base = ["16", "20", "24", "28", "30", "32"]
    current_l = (current or "").strip()
    vals = base
    if current_l and current_l.isdigit():
        vals = [current_l] + [v for v in base if v != current_l]
    return [discord.app_commands.Choice(name=v, value=int(v)) for v in vals][:25]


async def ac_fallback_mode(self, interaction: discord.Interaction, current: str):
    """Autocomplete handler for the /set-fallback-mode command.

    Provides the valid fallback modes filtered by the user's current partial input.
    """
    opts = [
        discord.app_commands.Choice(name="default", value="default"),
        discord.app_commands.Choice(name="randomized", value="randomized"),
        discord.app_commands.Choice(name="static", value="static"),
    ]
    cur_l = (current or "").lower()
    return [o for o in opts if cur_l in o.name][:25]


async def ac_policy_value(self, interaction: discord.Interaction, current: str):
    key = getattr(getattr(interaction, "namespace", object()), "key", None)
    key = (key or "").lower()

    aliases = {
        "enabled": "enabled",
        "check_length": "check_length",
        "min_nick_length": "min_nick_length",
        "max_nick_length": "max_nick_length",
        "cooldown_seconds": "cooldown_seconds",
        "fallback_label": "fallback_label",
        "logging_channel_id": "logging_channel_id",
        "bypass_role_id": "bypass_role_id",
        "enforce_bots": "enforce_bots",
        "preserve_spaces": "preserve_spaces",
        "sanitize_emoji": "sanitize_emoji",
        "fallback_mode": "fallback_mode",
    }
    key = aliases.get(key, key)
    if key in {
        "check_length",
        "min_nick_length",
        "max_nick_length",
        "cooldown_seconds",
    }:
        # For min/max nick lengths, constrain suggestions appropriately
        if key == "min_nick_length":
            choices = await ac_min_length_value(self, interaction, current)
        elif key == "max_nick_length":
            choices = await ac_max_length_value(self, interaction, current)
        elif key == "check_length":
            choices = await ac_check_count_value(self, interaction, current)
        else:
            choices = await ac_int_value(self, interaction, current)
        return [
            discord.app_commands.Choice(name=c.name, value=str(c.value))
            for c in choices
        ]
    if key in {
        "enabled",
        "preserve_spaces",
        "sanitize_emoji",
        "enforce_bots",
        "fallback_mode",  # special-case handled below
    }:
        if key == "fallback_mode":
            opts = [
                discord.app_commands.Choice(name="default", value="default"),
                discord.app_commands.Choice(name="randomized", value="randomized"),
                discord.app_commands.Choice(name="static", value="static"),
            ]
            cur_l = (current or "").lower()
            return [o for o in opts if cur_l in o.name][:25]
        return await ac_bool_value(self, interaction, current)
    # For ID-like settings suggest 'none' and current channel/role where applicable
    if key in {"logging_channel_id", "bypass_role_id"}:
        cur = (current or "").strip().lower()
        choices: list[discord.app_commands.Choice[str]] = []
        # Always include 'none' sentinel
        if "none".startswith(cur) or not cur:
            choices.append(discord.app_commands.Choice(name="none", value="none"))
        try:
            # Try to infer the target guild from interaction context or optional server_id
            ns = getattr(interaction, "namespace", None)
            server_id = None
            if ns is not None:
                server_id = getattr(ns, "server_id", None)
            gid = None
            if server_id:
                try:
                    gid = int(server_id)
                except Exception:
                    gid = None
            if gid is None and interaction.guild is not None:
                gid = interaction.guild.id
            if gid is not None and key == "logging_channel_id":
                g = self.get_guild(gid)
                if g is not None:
                    # Prioritize text channels; suggest a couple whose name or id matches
                    for ch in list(getattr(g, "text_channels", []))[:100]:
                        nm = getattr(ch, "name", "")
                        cid = str(getattr(ch, "id", ""))
                        label = f"#{nm} ({cid})" if nm else cid
                        hay = f"{nm} {cid}".lower()
                        if not cur or cur in hay:
                            choices.append(
                                discord.app_commands.Choice(name=label, value=cid)
                            )
                        if len(choices) >= 25:
                            break
            if gid is not None and key == "bypass_role_id":
                g = self.get_guild(gid)
                if g is not None:
                    for role in list(getattr(g, "roles", []))[:100]:
                        nm = getattr(role, "name", "")
                        rid = str(getattr(role, "id", ""))
                        label = f"@{nm} ({rid})" if nm else rid
                        hay = f"{nm} {rid}".lower()
                        if not cur or cur in hay:
                            choices.append(
                                discord.app_commands.Choice(name=label, value=rid)
                            )
                        if len(choices) >= 25:
                            break
        except Exception:
            pass
        return choices[:25]
    return []


async def ac_guild_id(self, interaction: discord.Interaction, current: str):
    # Restrict server autocomplete to the bot owner
    try:
        user_id = getattr(getattr(interaction, "user", object()), "id", None)
        if OWNER_ID and user_id != OWNER_ID:
            return []
    except Exception:
        return []
    current = (current or "").strip().lower()
    # Build choices as "Name (ID)" with value=ID string
    choices = []
    for g in sorted(self.guilds, key=lambda gg: (gg.name or "", gg.id))[:25]:
        name = g.name or "<unnamed>"
        label = f"{name} ({g.id})"
        if not current or current in name.lower() or current in str(g.id):
            choices.append(discord.app_commands.Choice(name=label, value=str(g.id)))
        if len(choices) >= 25:
            break
    return choices


async def ac_blacklisted_guild_id(self, interaction: discord.Interaction, current: str):
    # Owner-only
    try:
        user_id = getattr(getattr(interaction, "user", object()), "id", None)
        if OWNER_ID and user_id != OWNER_ID:
            return []
    except Exception:
        return []
    current = (current or "").strip().lower()
    # Query from DB
    items: list[discord.app_commands.Choice[str]] = []
    try:
        rows = await self.db.list_blacklisted_guilds()
        for gid, name, reason in rows:
            nm = name or "<unknown>"
            label = f"{nm} ({gid})"
            hay = f"{nm} {gid} {reason or ''}".lower()
            if not current or current in hay:
                items.append(discord.app_commands.Choice(name=label, value=str(gid)))
            if len(items) >= 25:
                break
    except Exception:
        return []
    return items
