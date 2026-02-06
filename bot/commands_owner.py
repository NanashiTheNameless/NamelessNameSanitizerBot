# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Owner-only command registration for SanitizerBot."""

from typing import Optional

import discord  # type: ignore
from discord import app_commands  # type: ignore


def register_owner_commands(self):
    @self.tree.command(
        name="add-bot-admin",
        description="Bot Owner Only: Add a bot admin for this guild (server)",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        server_id="Optional guild (server) ID to modify; required in DMs or to target another guild (server)"
    )
    @app_commands.autocomplete(server_id=self._ac_guild_id)
    async def _add_admin(
        interaction: discord.Interaction,
        user: discord.User,
        server_id: Optional[str] = None,
    ):
        await self.cmd_add_admin(interaction, user, server_id)

    @self.tree.command(
        name="blacklist-server",
        description="Bot Owner Only: Blacklist a guild (server) and leave/delete its stored data",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        server_id="The guild (server) ID to blacklist",
        reason="Optional reason for blacklisting",
        confirm="Type true to confirm",
    )
    @app_commands.autocomplete(server_id=self._ac_guild_id)
    async def _blacklist_server(
        interaction: discord.Interaction,
        server_id: str,
        reason: Optional[str] = None,
        confirm: Optional[bool] = False,
    ):
        await self.cmd_blacklist_server(interaction, server_id, reason, confirm)

    @self.tree.command(
        name="blacklist-set-name",
        description="Bot Owner Only: Update the display name for a blacklisted guild (server)",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        server_id="Guild (server) ID whose blacklist name to set",
        name="New display name (empty to clear)",
    )
    @app_commands.autocomplete(server_id=self._ac_blacklisted_guild_id)
    async def _set_blacklist_name(
        interaction: discord.Interaction,
        server_id: str,
        name: Optional[str] = None,
    ):
        await self.cmd_set_blacklist_name(interaction, server_id, name)

    @self.tree.command(
        name="blacklist-set-reason",
        description="Bot Owner Only: Update the reason for a blacklisted guild (server)",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        server_id="Guild (server) ID whose blacklist reason to set",
        reason="New reason text (empty to clear)",
    )
    @app_commands.autocomplete(server_id=self._ac_blacklisted_guild_id)
    async def _set_blacklist_reason(
        interaction: discord.Interaction,
        server_id: str,
        reason: Optional[str] = None,
    ):
        await self.cmd_set_blacklist_reason(interaction, server_id, reason)

    @self.tree.command(
        name="delete-user-data",
        description="Bot Owner Only: Delete a user's stored data across all guilds (servers) (cooldowns/admin entries)",
    )
    @app_commands.default_permissions()
    @app_commands.describe(confirm="Type true to confirm deletion of user data")
    async def _owner_delete_user_data(
        interaction: discord.Interaction,
        user: discord.User,
        confirm: Optional[bool] = False,
    ):
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: Pass confirm=True to delete user data.",
                ephemeral=True,
            )
            return
        await self.cmd_delete_user_data(interaction, user)

    @self.tree.command(
        name="dm-admin-report",
        description="Bot Owner Only: DM a report of all guilds (servers) and their bot admins",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        attach_file="Optional: attach the report as a file (default: false)",
    )
    async def _dm_admin_report(
        interaction: discord.Interaction, attach_file: Optional[bool] = False
    ):
        await self.cmd_dm_admin_report(interaction, attach_file)

    @self.tree.command(
        name="dm-all-reports",
        description="Bot Owner Only: DM the bot owner all reports (admins, settings, blacklist)",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        attach_file="Optional: attach each report as a file (default: false)",
    )
    async def _dm_all_reports(
        interaction: discord.Interaction, attach_file: Optional[bool] = False
    ):
        await self.cmd_dm_all_reports(interaction, attach_file)

    @self.tree.command(
        name="dm-blacklisted-servers",
        description="Bot Owner Only: DM the bot owner a list of blacklisted guilds (servers)",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        attach_file="Optional: attach full list as a file when large (default: false)",
    )
    async def _dm_blacklisted_servers(
        interaction: discord.Interaction, attach_file: Optional[bool] = False
    ):
        await self.cmd_dm_blacklisted_servers(interaction, attach_file)

    @self.tree.command(
        name="dm-server-settings",
        description="Bot Owner Only: DM a report of all guilds (servers) and their sanitizer settings",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        attach_file="Optional: attach the report as a file (default: false)",
    )
    async def _dm_server_settings(
        interaction: discord.Interaction, attach_file: Optional[bool] = False
    ):
        await self.cmd_dm_server_settings(interaction, attach_file)

    @self.tree.command(
        name="global-bot-disable",
        description="Bot Owner Only: Disable the sanitizer bot in all guilds (servers)",
    )
    @app_commands.default_permissions()
    @app_commands.describe(confirm="Type true to confirm global disable of the bot")
    async def _global_disable(
        interaction: discord.Interaction, confirm: Optional[bool] = False
    ):
        await self.cmd_global_bot_disable(interaction, confirm)

    @self.tree.command(
        name="global-delete-user-data",
        description="Bot Owner Only: Delete all user data globally and announce in logging channels",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        confirm="Type true to confirm deletion of ALL user data globally"
    )
    async def _global_delete_user_data(
        interaction: discord.Interaction,
        confirm: Optional[bool] = False,
    ):
        await self.cmd_global_delete_user_data(interaction, confirm)

    @self.tree.command(
        name="global-nuke-bot-admins",
        description="Bot Owner Only: Remove all bot admins in all guilds (servers)",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        confirm="Type true to confirm removal of all bot admins globally"
    )
    async def _global_nuke_bot_admins(
        interaction: discord.Interaction, confirm: Optional[bool] = False
    ):
        await self.cmd_global_nuke_bot_admins(interaction, confirm)

    @self.tree.command(
        name="global-reset-settings",
        description="Bot Owner Only: Reset all sanitizer settings to defaults across all guilds (servers)",
    )
    @app_commands.default_permissions()
    @app_commands.describe(confirm="Type true to confirm resetting settings globally")
    async def _global_reset_settings(
        interaction: discord.Interaction, confirm: Optional[bool] = False
    ):
        await self.cmd_global_reset_settings(interaction, confirm)

    @self.tree.command(
        name="leave-server",
        description="Bot Owner Only: Leave a guild (server) and delete its stored data",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        server_id="The guild (server) ID to leave", confirm="Type true to confirm"
    )
    @app_commands.autocomplete(server_id=self._ac_guild_id)
    async def _leave_server(
        interaction: discord.Interaction,
        server_id: str,
        confirm: Optional[bool] = False,
    ):
        await self.cmd_leave_server(interaction, server_id, confirm)

    @self.tree.command(
        name="list-bot-admins",
        description="Bot Owner Only: List bot admins for a guild (server)",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        server_id="Optional guild (server) ID to list; required in DMs"
    )
    @app_commands.autocomplete(server_id=self._ac_guild_id)
    async def _list_admins(
        interaction: discord.Interaction, server_id: Optional[str] = None
    ):
        await self.cmd_list_bot_admins(interaction, server_id)

    @self.tree.command(
        name="nuke-bot-admins",
        description="Bot Owner Only: Remove all bot admins in this guild (server)",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        server_id="Optional guild (server) ID to target; required in DMs or to nuke another guild (server)",
        confirm="Type true to confirm removal of all bot admins",
    )
    @app_commands.autocomplete(server_id=self._ac_guild_id)
    async def _nuke_bot_admins(
        interaction: discord.Interaction,
        server_id: Optional[str] = None,
        confirm: Optional[bool] = False,
    ):
        await self.cmd_nuke_bot_admins(interaction, server_id, confirm)

    @self.tree.command(
        name="remove-bot-admin",
        description="Bot Owner Only: Remove a bot admin for this guild (server)",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        server_id="Optional guild (server) ID to modify; required in DMs or to target another guild (server)"
    )
    @app_commands.autocomplete(server_id=self._ac_guild_id)
    async def _remove_admin(
        interaction: discord.Interaction,
        user: discord.User,
        server_id: Optional[str] = None,
    ):
        await self.cmd_remove_admin(interaction, user, server_id)

    @self.tree.command(
        name="unblacklist-server",
        description="Bot Owner Only: Remove a guild (server) from the blacklist",
    )
    @app_commands.default_permissions()
    @app_commands.describe(
        server_id="The guild (server) ID to unblacklist",
        confirm="Type true to confirm",
    )
    @app_commands.autocomplete(server_id=self._ac_blacklisted_guild_id)
    async def _unblacklist_server(
        interaction: discord.Interaction,
        server_id: str,
        confirm: Optional[bool] = False,
    ):
        await self.cmd_unblacklist_server(interaction, server_id, confirm)
