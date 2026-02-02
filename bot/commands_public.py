# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Public command registration for SanitizerBot."""

import discord  # type: ignore
from discord import app_commands  # type: ignore

from .config import OWNER_ID


def register_public_commands(self):
    @self.tree.command(
        name="botinfo",
        description="Everyone: Show bot information, owner, developer, source and policies",
    )
    async def _botinfo(interaction: discord.Interaction):
        try:
            owner_mention = f"<@{OWNER_ID}>" if OWNER_ID else "Not configured"
            dev_mention = "<@221701506561212416> (NamelessNanashi)"
            msg = (
                f"**Instance Owner: {owner_mention}**\n"
                f"**Bot Developer: {dev_mention}**\n"
                f"[Bot Website](<https://nnsb.namelessnanashi.dev/>)\n"
                f"[Terms Of Service](<https://nnsb.namelessnanashi.dev/TermsOfService/>)\n"
                f"[Privacy Policy](<https://nnsb.namelessnanashi.dev/PrivacyPolicy/>)\n"
                f"[Source Code](<https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/>)"
            )
            await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to fetch bot info: {e}", ephemeral=True
            )

    @self.tree.command(
        name="delete-my-data",
        description="Everyone: Delete any of your data stored by the bot in this guild (server) (cooldowns/admin entries)",
    )
    async def _delete_my_data(interaction: discord.Interaction):
        await self.cmd_delete_my_data(interaction)

    @self.tree.command(
        name="sanitize-user",
        description="Manage Nicknames Required: Clean up a member's nickname now",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    async def _sanitize(interaction: discord.Interaction, member: discord.Member):
        await self.cmd_sanitize(interaction, member)
