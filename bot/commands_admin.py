# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Admin command registration for SanitizerBot."""

from typing import Optional

import discord  # type: ignore
from discord import app_commands  # type: ignore


def register_admin_commands(self):
    @self.tree.command(
        name="check-update",
        description="Bot Admin Only: Check version now and update out-of-date warnings",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    async def _check_update(
        interaction: discord.Interaction,
    ):
        await self.cmd_check_update(interaction)

    @self.tree.command(
        name="clear-bypass-roles",
        description="Bot Admin Only: Clear bypass role(s)",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.describe(confirm="Type true to confirm clearing bypass role(s)")
    async def _clear_bypass_role(
        interaction: discord.Interaction, confirm: Optional[bool] = False
    ):
        await self.cmd_clear_bypass_role(interaction, confirm)

    @self.tree.command(
        name="clear-fallback-label",
        description="Bot Admin Only: Clear the fallback nickname",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    async def _clear_fallback_label(interaction: discord.Interaction):
        await self.cmd_clear_fallback_label(interaction)

    @self.tree.command(
        name="clear-logging-channel",
        description="Bot Admin Only: Clear the logging channel",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.describe(confirm="Type true to confirm clearing the logging channel")
    async def _clear_logging_channel(
        interaction: discord.Interaction, confirm: Optional[bool] = False
    ):
        await self.cmd_clear_logging_channel(interaction, confirm)

    @self.tree.command(
        name="disable-sanitizer",
        description="Bot Admin Only: Disable the sanitizer in this guild (server)",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.describe(
        server_id="Optional guild (server) ID to disable; required in DMs or to target another guild (server)"
    )
    @app_commands.autocomplete(server_id=self._ac_guild_id)
    async def _disable(
        interaction: discord.Interaction, server_id: Optional[str] = None
    ):
        await self.cmd_disable_sanitizer(interaction, server_id)

    @self.tree.command(
        name="enable-sanitizer",
        description="Bot Admin Only: Enable the sanitizer in this guild (server)",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.describe(
        server_id="Optional guild (server) ID to enable; required in DMs or to target another guild (server)"
    )
    @app_commands.autocomplete(server_id=self._ac_guild_id)
    async def _enable(
        interaction: discord.Interaction, server_id: Optional[str] = None
    ):
        await self.cmd_enable_sanitizer(interaction, server_id)

    @self.tree.command(
        name="reset-settings",
        description="Bot Admin Only: Reset all sanitizer settings to defaults for this guild (server)",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.describe(
        server_id="Optional guild (server) ID to reset; required in DMs or to target another guild (server)",
        confirm="Type true to confirm",
    )
    @app_commands.autocomplete(server_id=self._ac_guild_id)
    async def _reset_settings(
        interaction: discord.Interaction,
        server_id: Optional[str] = None,
        confirm: Optional[bool] = False,
    ):
        await self.cmd_reset_settings(interaction, server_id, confirm)

    @self.tree.command(
        name="set-bypass-roles",
        description="Bot Admin Only: Set or view role(s) that bypass nickname sanitization",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.describe(
        role="Role mentions or IDs separated by spaces or commas (leave empty to view)"
    )
    async def _set_bypass_role(
        interaction: discord.Interaction, role: Optional[str] = None
    ):
        await self.cmd_set_bypass_role(interaction, role)

    @self.tree.command(
        name="set-check-count",
        description="Bot Admin Only: Set or view the number of leading characters (grapheme clusters) to sanitize",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.autocomplete(value=self._ac_check_count_value)
    async def _set_check_count(
        interaction: discord.Interaction, value: Optional[int] = None
    ):
        await self.cmd_set_check_count(interaction, value)

    @self.tree.command(
        name="set-cooldown-seconds",
        description="Bot Admin Only: Set or view the cooldown (in seconds) between nickname edits per user",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.autocomplete(value=self._ac_int_value)
    async def _set_cooldown_seconds(
        interaction: discord.Interaction, value: Optional[int] = None
    ):
        await self.cmd_set_cooldown_seconds(interaction, value)

    @self.tree.command(
        name="set-emoji-sanitization",
        description="Bot Admin Only: Enable/disable removing emoji in nicknames or view current value",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    async def _set_emoji_sanitization(
        interaction: discord.Interaction, value: Optional[bool] = None
    ):
        await self.cmd_set_emoji_sanitization(interaction, value)

    @self.tree.command(
        name="set-enforce-bots",
        description="Bot Admin Only: Enable/disable enforcing nickname rules on other bots or view current value",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    async def _set_enforce_bots(
        interaction: discord.Interaction, value: Optional[bool] = None
    ):
        await self.cmd_set_enforce_bots(interaction, value)

    @self.tree.command(
        name="set-fallback-label",
        description="Bot Admin Only: Set or view the fallback nickname used when a name is fully illegal",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    async def _set_fallback_label(
        interaction: discord.Interaction, value: Optional[str] = None
    ):
        await self.cmd_set_fallback_label(interaction, value)

    @self.tree.command(
        name="set-fallback-mode",
        description="Bot Admin Only: Set or view the fallback mode: default|randomized|static",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.autocomplete(mode=self._ac_fallback_mode)
    async def _set_fallback_mode(
        interaction: discord.Interaction, mode: Optional[str] = None
    ):
        await self.cmd_set_fallback_mode(interaction, mode)

    @self.tree.command(
        name="set-keep-spaces",
        description="Set or view whether to keep original spacing (true) or normalize spaces (false)",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    async def _set_keep_spaces(
        interaction: discord.Interaction, value: Optional[bool] = None
    ):
        await self.cmd_set_keep_spaces(interaction, value)

    @self.tree.command(
        name="set-logging-channel",
        description="Bot Admin Only: Set or view the channel to receive nickname change logs",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    async def _set_logging_channel(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        await self.cmd_set_logging_channel(interaction, channel)

    @self.tree.command(
        name="set-max-length",
        description="Bot Admin Only: Set or view the maximum allowed nickname length",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.autocomplete(value=self._ac_max_length_value)
    async def _set_max_length(
        interaction: discord.Interaction, value: Optional[int] = None
    ):
        await self.cmd_set_max_nick_length(interaction, value)

    @self.tree.command(
        name="set-min-length",
        description="Bot Admin Only: Set or view the minimum allowed nickname length",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.autocomplete(value=self._ac_min_length_value)
    async def _set_min_length(
        interaction: discord.Interaction, value: Optional[int] = None
    ):
        await self.cmd_set_min_nick_length(interaction, value)

    @self.tree.command(
        name="set-policy",
        description="Bot Admin Only: Set or view policy values; supports multiple updates",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.describe(
        key="Policy key to change (ignored if 'pairs' is provided)",
        value="New value for the policy key (leave empty to view current)",
        pairs="Multiple key=value pairs separated by spaces, e.g. 'min_nick_length=3 max_nick_length=24'",
        server_id="Optional guild (server) ID to modify; required in DMs or when editing another guild (server)",
    )
    @app_commands.autocomplete(
        key=self._ac_policy_key,
        value=self._ac_policy_value,
        server_id=self._ac_guild_id,
    )
    async def _set_policy(
        interaction: discord.Interaction,
        key: Optional[str] = None,
        value: Optional[str] = None,
        pairs: Optional[str] = None,
        server_id: Optional[str] = None,
    ):
        await self.cmd_set_setting(interaction, key, value, pairs, server_id)

    @self.tree.command(
        name="sweep-now",
        description="Bot Admin Only: Immediately sweep and sanitize members in this guild (server)",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    async def _sweep_now(interaction: discord.Interaction):
        await self.cmd_sweep_now(interaction)
