# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""
SanitizerBot Discord client implementation.

This module contains the main Discord bot client that handles nickname sanitization,
command registration, event handlers, and all bot functionality.
"""

import asyncio
import logging
import math
import os
import shlex
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Optional

import commentjson  # type: ignore
import discord  # type: ignore
import regex as re  # type: ignore
from discord import app_commands  # type: ignore
from discord.ext import tasks  # type: ignore

from .config import (
    APPLICATION_ID,
    COMMAND_COOLDOWN_SECONDS,
    COOLDOWN_TTL_SEC,
    DATABASE_URL,
    DEBUG_MODE,
    DM_OWNER_ON_ERRORS,
    DM_OWNER_ON_GUILD_EVENTS,
    FALLBACK_LABEL,
    OWNER_DESTRUCTIVE_COOLDOWN_SECONDS,
    OWNER_ID,
    SWEEP_INTERVAL_SEC,
    GuildSettings,
    parse_bool_str,
)
from .database import Database
from .helpers import now, owner_destructive_check, resolve_target_guild
from .sanitizer import filter_allowed_chars, remove_marks_and_controls, sanitize_name

log = logging.getLogger("sanitizerbot")

try:
    from .telemetry import maybe_send_telemetry_background  # type: ignore
except Exception as e:
    maybe_send_telemetry_background = None
    log.warning(
        "[TELEMETRY] Disabled: failed to import telemetry module (%s). Running without census.",
        e,
    )

try:
    from .version_check import check_outdated  # type: ignore
except Exception as e:
    check_outdated = None
    log.warning("[VERSION] Disabled: failed to import version check module (%s).", e)


class SanitizerCommandTree(discord.app_commands.CommandTree):
    async def _call(self, interaction: discord.Interaction) -> None:
        await super()._call(interaction)
        try:
            # self.client is the bot instance
            # Skip outdated warning for check-update command since it handles its own warnings
            if (
                self.client
                and hasattr(self.client, "_maybe_send_outdated_warning")
                and interaction.command
                and interaction.command.name != "check-update"
            ):
                await self.client._maybe_send_outdated_warning(interaction)
        except Exception as e:
            log.debug("[COMMAND_TREE] Error appending outdated warning: %s", e)


class SanitizerBot(discord.Client):
    def __init__(self, intents: discord.Intents):
        kwargs = {"intents": intents}
        if APPLICATION_ID:
            kwargs["application_id"] = APPLICATION_ID
        super().__init__(**kwargs)
        self.db = Database(DATABASE_URL) if DATABASE_URL else None
        self.tree = SanitizerCommandTree(self)
        self._cmd_cooldown_last: dict[int, float] = {}
        # Separate owner destructive cooldown timestamp
        self._owner_destructive_last = 0.0

        # Version check / outdated warning state
        self._outdated_message: Optional[str] = None
        self._outdated_warning_sent_interactions: set[int] = set()
        self._last_check_update_time: float = 0.0  # For bot-admin cooldown

        # Status cycling variables
        self._status_messages: list[dict] = []
        self._current_status_index = 0
        self._error_count = 0
        self._last_error_reset = 0.0
        self._file_not_found = False

        # Validate owner is configured
        if not OWNER_ID:
            raise ValueError(
                "OWNER_ID environment variable is not set. "
                "Bot owner must be configured. Set OWNER_ID to your Discord user ID."
            )
        self._load_status_messages()

        self._policy_keys = [
            discord.app_commands.Choice(name="enabled (true/false)", value="enabled"),
            discord.app_commands.Choice(
                name="check_length (integer)", value="check_length"
            ),
            discord.app_commands.Choice(
                name="min_nick_length (integer)", value="min_nick_length"
            ),
            discord.app_commands.Choice(
                name="max_nick_length (integer)", value="max_nick_length"
            ),
            discord.app_commands.Choice(
                name="cooldown_seconds (integer)", value="cooldown_seconds"
            ),
            discord.app_commands.Choice(
                name="preserve_spaces (true/false)", value="preserve_spaces"
            ),
            discord.app_commands.Choice(
                name="sanitize_emoji (true/false)", value="sanitize_emoji"
            ),
            discord.app_commands.Choice(
                name="enforce_bots (true/false)", value="enforce_bots"
            ),
            discord.app_commands.Choice(
                name="logging_channel_id (channel id or none)",
                value="logging_channel_id",
            ),
            discord.app_commands.Choice(
                name="bypass_role_id (role id or none)", value="bypass_role_id"
            ),
            discord.app_commands.Choice(
                name="fallback_mode (default|randomized|static)",
                value="fallback_mode",
            ),
            discord.app_commands.Choice(
                name="fallback_label (1-20, letters/numbers/spaces/dashes)",
                value="fallback_label",
            ),
        ]

    def _load_status_messages(self):
        """Load status messages from bot_statuses.jsonc."""
        try:
            # Try multiple paths for the JSONC file
            base_dirs = [
                os.path.dirname(os.path.dirname(__file__)),  # /app or project root
                os.getcwd(),  # current working directory
            ]

            for base_dir in base_dirs:
                json_path = os.path.join(base_dir, "bot_statuses.jsonc")
                if os.path.isfile(json_path):
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            data = commentjson.load(f)
                            statuses = data.get("statuses", [])

                            # Validate that statuses is a non-empty list
                            if not isinstance(statuses, list) or len(statuses) == 0:
                                raise ValueError(
                                    "Invalid JSON structure: 'statuses' must be a non-empty array"
                                )

                            # Parse statuses - support both old string format and new dict format
                            self._status_messages = []
                            for status in statuses:
                                if isinstance(status, str):
                                    # Simple format: just a string
                                    self._status_messages.append(
                                        {
                                            "text": status,
                                            "duration": 30,
                                            "type": "watching",
                                        }
                                    )
                                elif isinstance(status, dict):
                                    # Advanced format: dict with text, duration, and optional type
                                    text = status.get("text", "")
                                    if not text or not isinstance(text, str):
                                        raise ValueError(
                                            "Invalid status entry: 'text' must be a non-empty string"
                                        )
                                    duration = status.get("duration", 30)
                                    if (
                                        not isinstance(duration, (int, float))
                                        or duration <= 0
                                    ):
                                        raise ValueError(
                                            "Invalid status entry: 'duration' must be a positive number"
                                        )
                                    activity_type = status.get("type", "watching")
                                    self._status_messages.append(
                                        {
                                            "text": text,
                                            "duration": duration,
                                            "type": activity_type,
                                        }
                                    )
                                else:
                                    raise ValueError(
                                        "Invalid status entry: must be a string or object"
                                    )

                        if self._status_messages:
                            # Check for required statuses
                            status_texts = [s["text"] for s in self._status_messages]
                            required_statuses = [
                                "Bot Coded By NamelessNanashi",
                                "Licensed under NNCL, see /botinfo",
                            ]
                            missing_statuses = [
                                req
                                for req in required_statuses
                                if req not in status_texts
                            ]

                            if missing_statuses:
                                # Missing required author/license credits
                                log.error(
                                    f"[STATUS] Missing required statuses: {', '.join(missing_statuses)}"
                                )
                                self._file_not_found = True
                                self._status_messages = [
                                    {
                                        "text": "403 Author Credit Removed",
                                        "duration": 30,
                                        "type": "watching",
                                    },
                                    {
                                        "text": "401 License Violation, Usage Unauthorized",
                                        "duration": 30,
                                        "type": "watching",
                                    },
                                ]
                                if DM_OWNER_ON_ERRORS:
                                    asyncio.create_task(
                                        self._dm_owner(
                                            f"**Bot Configuration Error**\n"
                                            f"Status file is missing required credits:\n"
                                            f"{', '.join(missing_statuses)}\n"
                                            f"Status: Bot is now in DnD mode (red status)"
                                        )
                                    )
                                return

                            log.info(
                                f"[STATUS] Loaded {len(self._status_messages)} status messages"
                            )
                            self._file_not_found = False
                            return
                    except ValueError as e:
                        # Invalid JSON or validation errors - set 400 status
                        log.error(f"[STATUS] Invalid bot_statuses.json: {e}")
                        self._file_not_found = True
                        self._status_messages = [
                            {
                                "text": "400 Invalid Flavortext",
                                "duration": 30,
                                "type": "watching",
                            }
                        ]
                        if DM_OWNER_ON_ERRORS:
                            asyncio.create_task(
                                self._dm_owner(
                                    f"**Bot Configuration Error**\n"
                                    f"Invalid bot_statuses.jsonc file: {e}\n"
                                    f"Status: Bot is now in DnD mode (red status)"
                                )
                            )
                        return

            # File not found - set 404 status
            log.error("[STATUS] bot_statuses.json not found")
            self._file_not_found = True
            self._status_messages = [
                {"text": "404 Flavortext not found", "duration": 30, "type": "watching"}
            ]
            if DM_OWNER_ON_ERRORS:
                asyncio.create_task(
                    self._dm_owner(
                        "**Bot Configuration Error**\n"
                        "bot_statuses.jsonc file not found\n"
                        "Status: Bot is now in DnD mode (red status)"
                    )
                )
        except Exception as e:
            log.error(f"[STATUS] Failed to load status messages: {e}")
            self._file_not_found = True
            self._status_messages = [
                {"text": "404 Flavortext not found", "duration": 30, "type": "watching"}
            ]
            if DM_OWNER_ON_ERRORS:
                asyncio.create_task(
                    self._dm_owner(
                        f"**Bot Configuration Error**\n"
                        f"Failed to load status messages: {e}\n"
                        f"Status: Bot is now in DnD mode (red status)"
                    )
                )

    def _track_error(
        self, error_msg: str = "Unknown error", guild_id: int | None = None
    ):
        """Track an error occurrence for status color determination.

        Args:
            error_msg: Description of the error that occurred
            guild_id: Optional guild ID where the error occurred
        """
        self._error_count += 1
        if DM_OWNER_ON_ERRORS and self._error_count > 2:
            # Create task to DM owner without blocking
            guild_info = f"\nGuild ID: `{guild_id}`" if guild_id else ""
            asyncio.create_task(
                self._dm_owner(
                    f"**Bot Error Alert** ({self._error_count} errors)\n"
                    f"Error: {error_msg}{guild_info}\n"
                    f"Status: Bot is now in DnD mode (red status)"
                )
            )

    def _get_bot_status(self) -> discord.Status:
        """Determine bot status color based on error rate, file status, and version.

        Returns:
            discord.Status.online (green) if healthy
            discord.Status.idle (yellow) if out of date (no errors)
            discord.Status.dnd (red) if experiencing errors or status file not found

        Note: Once red status is triggered, it persists until bot restart.
        """
        # If status file not found, always red
        if self._file_not_found:
            return discord.Status.dnd

        # If more than 2 errors, show red status (persists until restart)
        if self._error_count > 2:
            return discord.Status.dnd

        # If outdated, show yellow/idle status
        if self._outdated_message:
            return discord.Status.idle

        return discord.Status.online

    async def _dm_owner(self, content: str) -> bool:
        if not DM_OWNER_ON_GUILD_EVENTS:
            return False
        if not OWNER_ID:
            return False
        try:
            user = self.get_user(OWNER_ID) or await self.fetch_user(OWNER_ID)
            if user:
                await user.send(content)
                return True
        except Exception:
            pass
        return False

    def _register_all_commands(self):
        # Public

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

        # Guild (Server) Admin

        @self.tree.command(
            name="sanitize-user",
            description="Manage Nicknames Required: Clean up a member's nickname now",
        )
        @app_commands.default_permissions(manage_nicknames=True)
        async def _sanitize(interaction: discord.Interaction, member: discord.Member):
            await self.cmd_sanitize(interaction, member)

        # Bot admin

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
            await self.cmd_start(interaction, server_id)

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
            await self.cmd_stop(interaction, server_id)

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
            name="set-bypass-role",
            description="Bot Admin Only: Set or view a role that bypasses nickname sanitization",
        )
        @app_commands.default_permissions(manage_nicknames=True)
        async def _set_bypass_role(
            interaction: discord.Interaction, role: Optional[discord.Role] = None
        ):
            await self.cmd_set_bypass_role(interaction, role)

        @self.tree.command(
            name="set-emoji-sanitization",
            description="Bot Admin Only: Enable/disable removing emoji in nicknames or view current value",
        )
        @app_commands.default_permissions(manage_nicknames=True)
        async def _set_emoji(
            interaction: discord.Interaction, value: Optional[bool] = None
        ):
            await self.cmd_set_sanitize_emoji(interaction, value)

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
            await self.cmd_set_preserve_spaces(interaction, value)

        @self.tree.command(
            name="set-min-length",
            description="Bot Admin Only: Set or view the minimum allowed nickname length",
        )
        @app_commands.default_permissions(manage_nicknames=True)
        @app_commands.autocomplete(value=self._ac_min_length_value)
        async def _set_min_nick_length(
            interaction: discord.Interaction, value: Optional[int] = None
        ):
            await self.cmd_set_min_nick_length(interaction, value)

        @self.tree.command(
            name="set-max-length",
            description="Bot Admin Only: Set or view the maximum allowed nickname length",
        )
        @app_commands.default_permissions(manage_nicknames=True)
        @app_commands.autocomplete(value=self._ac_max_length_value)
        async def _set_max_nick_length(
            interaction: discord.Interaction, value: Optional[int] = None
        ):
            await self.cmd_set_max_nick_length(interaction, value)

        @self.tree.command(
            name="set-check-count",
            description="Bot Admin Only: Set or view the number of leading characters (grapheme clusters) to sanitize",
        )
        @app_commands.default_permissions(manage_nicknames=True)
        @app_commands.autocomplete(value=self._ac_check_count_value)
        async def _set_check_count(
            interaction: discord.Interaction, value: Optional[int] = None
        ):
            await self.cmd_set_check_length(interaction, value)

        @self.tree.command(
            name="set-cooldown-seconds",
            description="Bot Admin Only: Set or view the cooldown (in seconds) between nickname edits per user",
        )
        @app_commands.default_permissions(manage_nicknames=True)
        @app_commands.autocomplete(value=self._ac_int_value)
        async def _set_cooldown(
            interaction: discord.Interaction, value: Optional[int] = None
        ):
            await self.cmd_set_cooldown_seconds(interaction, value)

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
            name="clear-logging-channel",
            description="Bot Admin Only: Clear the logging channel",
        )
        @app_commands.default_permissions(manage_nicknames=True)
        @app_commands.describe(
            confirm="Type true to confirm clearing the logging channel"
        )
        async def _clear_logging_channel(
            interaction: discord.Interaction, confirm: Optional[bool] = False
        ):
            await self.cmd_clear_logging_channel(interaction, confirm)

        @self.tree.command(
            name="clear-bypass-role",
            description="Bot Admin Only: Clear the bypass role",
        )
        @app_commands.default_permissions(manage_nicknames=True)
        @app_commands.describe(confirm="Type true to confirm clearing the bypass role")
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
            name="sweep-now",
            description="Bot Admin Only: Immediately sweep and sanitize members in this guild (server)",
        )
        @app_commands.default_permissions(manage_nicknames=True)
        async def _sweep_now(interaction: discord.Interaction):
            await self.cmd_sweep_now(interaction)

        # Owner-only

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
            name="check-update",
            description="Bot Owner Only: Check version now and update out-of-date warnings",
        )
        @app_commands.default_permissions()
        async def _check_update(
            interaction: discord.Interaction,
        ):
            await self.cmd_check_update(interaction)

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
            name="global-reset-settings",
            description="Bot Owner Only: Reset all sanitizer settings to defaults across all guilds (servers)",
        )
        @app_commands.default_permissions()
        @app_commands.describe(
            confirm="Type true to confirm resetting settings globally"
        )
        async def _global_reset_settings(
            interaction: discord.Interaction, confirm: Optional[bool] = False
        ):
            await self.cmd_global_reset_settings(interaction, confirm)

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
        async def _nuke_admins(
            interaction: discord.Interaction,
            server_id: Optional[str] = None,
            confirm: Optional[bool] = False,
        ):
            await self.cmd_nuke_bot_admins(interaction, server_id, confirm)

        @self.tree.command(
            name="global-nuke-bot-admins",
            description="Bot Owner Only: Remove all bot admins in all guilds (servers)",
        )
        @app_commands.default_permissions()
        @app_commands.describe(
            confirm="Type true to confirm removal of all bot admins globally"
        )
        async def _global_nuke_admins(
            interaction: discord.Interaction, confirm: Optional[bool] = False
        ):
            await self.cmd_global_nuke_bot_admins(interaction, confirm)

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
            await self.cmd_list_blacklisted_servers(interaction, attach_file)

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

    async def setup_hook(self) -> None:
        self._register_all_commands()
        # Global command cooldown check (owner and bot admins bypass)
        try:
            self.tree.add_check(self._command_cooldown_check)  # type: ignore[arg-type]
        except Exception:
            pass
        try:
            asyncio.create_task(self._version_check_task())
        except Exception:
            pass
        try:
            await self.tree.sync()
            log.info("[STATUS] Slash commands synced globally on startup.")
        except Exception as e:
            log.warning("Failed to sync app commands on startup: %s", e)
        if maybe_send_telemetry_background:
            try:
                log.info("[TELEMETRY] Attempting to start telemetry system")
                maybe_send_telemetry_background()
            except Exception as e:
                log.warning(
                    "[TELEMETRY] Telemetry initialization failed: %s. Continuing without telemetry.",
                    e,
                )

    async def on_ready(self):
        if self.db:
            try:
                await self.db.connect()
                await self.db.init()
            except Exception as e:
                log.error("Database initialization failed: %s", e)
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
                            log.debug(
                                "Failed leaving blacklisted guild %s: %s", g.id, e
                            )
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
        asyncio.create_task(self.status_cycle())

        log.info("[STATUS] Starting member sweep background task.")
        self.member_sweep.start()  # type: ignore

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

    async def _sanitize_member(self, member: discord.Member, source: str) -> bool:
        settings = GuildSettings(member.guild.id)
        if self.db:
            try:
                settings = await self.db.get_settings(member.guild.id)
            except Exception as e:
                log.debug("Failed to get settings for guild %s: %s", member.guild.id, e)

        if member.bot:
            if self.user and member.id == self.user.id:
                return False
            if not settings.enforce_bots:
                return False

        if not settings.enabled:
            return False

        if settings.bypass_role_id and any(
            r.id == settings.bypass_role_id for r in getattr(member, "roles", [])
        ):
            return False

        if self.db:
            last_ts = await self.db.get_cooldown(member.id)
            if last_ts is not None and now() - last_ts < settings.cooldown_seconds:
                return False

        name_now = member.nick or getattr(member, "global_name", None) or member.name
        candidate, used_fallback = sanitize_name(name_now, settings)

        # If we had to fallback and server mode is 'default', attempt sanitizing the account username instead
        if used_fallback and getattr(settings, "fallback_mode", "default") == "default":
            base_username = getattr(member, "name", None)
            if base_username and base_username != name_now:
                alt_candidate, alt_used_fallback = sanitize_name(
                    base_username, settings
                )
                if not alt_used_fallback:
                    candidate = alt_candidate
                else:
                    # Username also invalid; fall back to custom label
                    candidate = settings.fallback_label or "Illegal Name"

        if candidate == name_now:
            return False

        guild = member.guild
        me = guild.me

        if not me.guild_permissions.manage_nicknames:
            log.warning("Missing Manage Nicknames permission.")
            return False

        if member.top_role >= me.top_role and member != me:
            log.debug("Cannot edit %s due to role hierarchy.", member)
            return False

        try:
            await member.edit(
                nick=candidate, reason=f"Sanitized by policy from {source}"
            )
            if self.db:
                await self.db.set_cooldown(member.id, now())
            log.info("Edited nickname: %s -> %s [%s]", name_now, candidate, source)

            if settings.logging_channel_id:
                ch = member.guild.get_channel(settings.logging_channel_id)
                if ch is None:
                    try:
                        ch = await member.guild.fetch_channel(
                            settings.logging_channel_id
                        )
                    except Exception:
                        ch = None
                if isinstance(ch, (discord.TextChannel, discord.Thread)):
                    try:
                        log_msg = f"Nickname updated: {member.mention} - `{name_now}` -> `{candidate}` (via {source})"
                        # Append outdated warning if available
                        if self._outdated_message:
                            log_msg += f"\n\n{self._outdated_message}"
                        await ch.send(log_msg)  # type: ignore
                    except Exception:
                        pass
            return True
        except discord.Forbidden:
            log.debug("Forbidden editing nickname for %s.", member)
        except discord.HTTPException as e:
            log.debug("HTTPException editing %s: %s", member, e)
        return False

    async def _diagnose_sanitize_blockers(
        self,
        member: discord.Member,
        settings: GuildSettings,
        candidate: str,
    ) -> list[str]:
        reasons: list[str] = []

        # General state checks
        if not settings.enabled:
            reasons.append("The sanitizer is disabled in this server.")

        if member.bot:
            if self.user and member.id == self.user.id:
                reasons.append("You can't target this bot's own account.")
            if not settings.enforce_bots:
                reasons.append("Bots are excluded because enforce_bots is disabled.")

        # Bypass role
        if settings.bypass_role_id and any(
            r.id == settings.bypass_role_id for r in getattr(member, "roles", [])
        ):
            reasons.append(
                f"Target has the bypass role <@&{settings.bypass_role_id}>, so changes are skipped."
            )

        # Cooldown
        if self.db:
            try:
                last_ts = await self.db.get_cooldown(member.id)
            except Exception:
                last_ts = None
            if last_ts is not None:
                remaining = settings.cooldown_seconds - (now() - last_ts)
                if remaining > 0:
                    reasons.append(
                        f"A cooldown is active for this user. Try again in ~{max(1, int(math.ceil(remaining)))}s."
                    )

        # Already compliant
        name_now = member.nick or getattr(member, "global_name", None) or member.name
        if candidate == name_now:
            reasons.append(
                "No change is necessary; nickname already complies with policy."
            )
            try:
                if settings.check_length and settings.check_length > 0:
                    clusters = re.findall(r"\X", name_now)
                    if settings.check_length < len(clusters):
                        tail = "".join(clusters[settings.check_length :])
                        processed_tail = remove_marks_and_controls(tail)
                        processed_tail = filter_allowed_chars(
                            processed_tail, settings.sanitize_emoji
                        )
                        if not settings.preserve_spaces:
                            processed_tail = re.sub(r"\s+", " ", processed_tail).strip()
                        if processed_tail != tail:
                            reasons.append(
                                f"Tail beyond the first {settings.check_length} grapheme(s) contains characters that would be sanitized, but check_length limits scope. Increase check_length to sanitize them."
                            )
            except Exception:
                pass

        # Permissions / hierarchy
        me = member.guild.me
        if not me or not me.guild_permissions.manage_nicknames:
            reasons.append("Bot is missing the Manage Nicknames permission.")
        else:
            try:
                if member != me and member.top_role >= me.top_role:
                    reasons.append(
                        "Cannot change nickname due to role hierarchy (target's top role is higher or equal to the bot's)."
                    )
            except Exception:
                # In case roles aren't available or cached
                pass

        return reasons

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
                self._track_error(
                    f"Member sweep HTTP error in {guild.name}: {e}", guild.id
                )
            if processed and DEBUG_MODE:
                log.info("Sweep processed %d members in %s", processed, guild.name)

    @member_sweep.before_loop
    async def before_member_sweep(self):
        await self.wait_until_ready()

    async def status_cycle(self):
        """Cycle through status messages with dynamic durations."""
        await self.wait_until_ready()

        activity_type_map = {
            "playing": discord.ActivityType.playing,
            "streaming": discord.ActivityType.streaming,
            "listening": discord.ActivityType.listening,
            "watching": discord.ActivityType.watching,
            "competing": discord.ActivityType.competing,
        }

        while not self.is_closed():
            try:
                if not self._status_messages:
                    await asyncio.sleep(30)
                    continue

                # Get current status message
                current_status = self._status_messages[self._current_status_index]
                status_text = current_status.get("text", "404 Flavortext not found")
                duration = current_status.get("duration", 30)
                # Clamp duration to minimum 20 seconds to avoid Discord rate limits
                duration = max(20, duration)
                activity_type_str = current_status.get("type", "watching").lower()
                activity_type = activity_type_map.get(
                    activity_type_str, discord.ActivityType.watching
                )

                # Determine status color based on error rate
                status_color = self._get_bot_status()

                # Update bot status
                activity = discord.Activity(type=activity_type, name=status_text)
                await self.change_presence(activity=activity, status=status_color)

                # Wait for the duration specified for this status
                await asyncio.sleep(duration)

                # Move to next status message (wraps cleanly at end of list)
                self._current_status_index = (self._current_status_index + 1) % len(
                    self._status_messages
                )

            except Exception as e:
                log.error(f"[STATUS] Failed to update status: {e}")
                self._track_error(f"Status cycle update failed: {e}")
                await asyncio.sleep(30)  # Wait before retrying

    async def close(self):
        self.member_sweep.cancel()  # type: ignore
        await super().close()

    def _is_guild_admin(self, member: discord.Member) -> bool:
        return bool(member.guild_permissions.manage_nicknames)

    async def _is_bot_admin(self, guild_id: int, user_id: int) -> bool:
        if OWNER_ID and user_id == OWNER_ID:
            return True
        if not self.db:
            return False
        return await self.db.is_admin(guild_id, user_id)

    async def cmd_start(
        self, interaction: discord.Interaction, server_id: Optional[str] = None
    ):
        target_gid = await resolve_target_guild(interaction, server_id)
        if target_gid is None:
            return
        g = self.get_guild(target_gid)
        if g is None:
            await interaction.response.send_message(
                "I am not in that server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(target_gid, interaction.user.id):
            await interaction.response.send_message(
                "You are not authorized to start the bot in that server.",
                ephemeral=True,
            )
            return
        await self.db.set_setting(target_gid, "enabled", True)
        await interaction.response.send_message(
            f"Sanitizer enabled for server {g.name} ({g.id}).", ephemeral=True
        )

    async def cmd_stop(
        self, interaction: discord.Interaction, server_id: Optional[str] = None
    ):
        target_gid = await resolve_target_guild(interaction, server_id)
        if target_gid is None:
            return
        g = self.get_guild(target_gid)
        if g is None:
            await interaction.response.send_message(
                "I am not in that server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(target_gid, interaction.user.id):
            await interaction.response.send_message(
                "You are not authorized to stop the bot in that server.", ephemeral=True
            )
            return
        await self.db.set_setting(target_gid, "enabled", False)
        await interaction.response.send_message(
            f"Sanitizer disabled for server {g.name} ({g.id}).", ephemeral=True
        )

    async def cmd_sanitize(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        settings = GuildSettings(interaction.guild.id)
        if self.db:
            try:
                settings = await self.db.get_settings(interaction.guild.id)
            except Exception:
                pass
        warn_disabled = None
        if not settings.enabled:
            warn_disabled = "Note: The sanitizer is currently disabled in this server. Automatic enforcement is paused until an admin runs `/enable-sanitizer`."

        if not (
            self._is_guild_admin(interaction.user)
            or await self._is_bot_admin(interaction.guild.id, interaction.user.id)
        ):
            await interaction.response.send_message(
                "You must have the Manage Nicknames permission or be a bot admin to use this command.",
                ephemeral=True,
            )
            return
        current_name = (
            member.nick or getattr(member, "global_name", None) or member.name
        )
        candidate, used_fallback = sanitize_name(current_name, settings)

        # If fallback occurred and server mode is 'default', attempt user's account username
        if used_fallback and getattr(settings, "fallback_mode", "default") == "default":
            base_username = getattr(member, "name", None)
            if base_username and base_username != current_name:
                alt_candidate, alt_used_fallback = sanitize_name(
                    base_username, settings
                )
                if not alt_used_fallback:
                    candidate = alt_candidate
                else:
                    candidate = settings.fallback_label or "Illegal Name"

        if candidate == current_name:
            full_settings = GuildSettings(**{**settings.__dict__})
            full_settings.check_length = 0
            candidate_full, _candidate_full_fallback = sanitize_name(
                current_name, full_settings
            )
            if candidate_full != current_name and settings.check_length > 0:
                msg = (
                    f"No change applied under current scope (check_length={settings.check_length}). "
                    f"However, full-name sanitization would change it to `{candidate_full}`. "
                    f"Consider increasing check_length or setting it to 0 to sanitize the whole name."
                )
            else:
                msg = f"No change needed for {member.mention}; nickname already compliant."
            if warn_disabled:
                msg = f"{msg}\n{warn_disabled}"
            await interaction.response.send_message(msg, ephemeral=True)
            return

        did_change = await self._sanitize_member(member, source="command")
        if did_change:
            msg = f"Nickname updated: `{current_name}` -> `{candidate}`."
        else:
            # Provide explicit reasons why it could not change
            reasons = await self._diagnose_sanitize_blockers(
                member, settings, candidate
            )
            if reasons:
                bullets = "\n".join(f"- {r}" for r in reasons)
                msg = f"Couldn't change nickname from `{current_name}` to `{candidate}` because:\n{bullets}"
            else:
                msg = f"Attempted to update nickname from `{current_name}` to `{candidate}`, but no change was applied. The Discord API may have refused the edit (Forbidden/HTTP error)."
        if warn_disabled:
            msg = f"{msg}\n{warn_disabled}"
        await interaction.response.send_message(msg, ephemeral=True)

    async def cmd_sweep_now(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        # Admin check (bot admin only)
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "Only bot admins can use this command.",
                ephemeral=True,
            )
            return
        # Check settings enabled
        settings = await self.db.get_settings(interaction.guild.id)
        if not settings.enabled:
            await interaction.response.send_message(
                "The sanitizer is currently disabled in this server. Enable it with `/enable-sanitizer`.",
                ephemeral=True,
            )
            return
        # Defer while sweeping
        await interaction.response.defer(ephemeral=True)
        processed = 0
        changed = 0
        try:
            async for member in interaction.guild.fetch_members(limit=None):
                if member.bot and not settings.enforce_bots:
                    continue
                did_change = await self._sanitize_member(member, source="manual-sweep")
                if did_change:
                    changed += 1
                processed += 1
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"Sweep encountered an HTTP error after processing {processed} member(s): {e}",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Sweep complete. Processed {processed} member(s); changed {changed} nickname(s).",
            ephemeral=True,
        )

    async def _ac_policy_key(self, interaction: discord.Interaction, current: str):

        current_l = (current or "").lower()
        choices = [
            c
            for c in self._policy_keys
            if current_l in c.name.lower() or current_l in c.value.lower()
        ]
        return choices[:25]

    async def _ac_bool_value(self, interaction: discord.Interaction, current: str):
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

    async def _ac_int_value(self, interaction: discord.Interaction, current: str):

        suggestions = ["0", "1", "2", "3", "5", "10", "15", "30", "60"]
        if current and current.isdigit():

            suggestions = [current] + [s for s in suggestions if s != current]
        return [discord.app_commands.Choice(name=s, value=int(s)) for s in suggestions][
            :25
        ]

    async def _ac_check_count_value(
        self, interaction: discord.Interaction, current: str
    ):
        # Curated suggestions for check_length
        base = ["0", "4", "6", "8", "10", "18"]
        current_l = (current or "").strip()
        vals = base
        if current_l and current_l.isdigit():
            vals = [current_l] + [v for v in base if v != current_l]
        return [discord.app_commands.Choice(name=v, value=int(v)) for v in vals][:25]

    async def _ac_min_length_value(
        self, interaction: discord.Interaction, current: str
    ):
        # Only allow suggestions up to 8 for min length
        vals = [str(i) for i in range(0, 9)]
        current_l = (current or "").strip()
        if current_l and current_l.isdigit():
            # Show the typed value first (even if > 8, we will still validate on submit)
            vals = [current_l] + [v for v in vals if v != current_l]
        return [discord.app_commands.Choice(name=v, value=int(v)) for v in vals][:25]

    async def _ac_max_length_value(
        self, interaction: discord.Interaction, current: str
    ):
        # Provide curated choices up to 32 for max length
        base = ["16", "20", "24", "28", "30", "32"]
        current_l = (current or "").strip()
        vals = base
        if current_l and current_l.isdigit():
            vals = [current_l] + [v for v in base if v != current_l]
        return [discord.app_commands.Choice(name=v, value=int(v)) for v in vals][:25]

    async def _ac_fallback_mode(self, interaction: discord.Interaction, current: str):
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

    async def _ac_policy_value(self, interaction: discord.Interaction, current: str):
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
                choices = await self._ac_min_length_value(interaction, current)
            elif key == "max_nick_length":
                choices = await self._ac_max_length_value(interaction, current)
            elif key == "check_length":
                choices = await self._ac_check_count_value(interaction, current)
            else:
                choices = await self._ac_int_value(interaction, current)
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
            return await self._ac_bool_value(interaction, current)
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

    async def _ac_guild_id(self, interaction: discord.Interaction, current: str):
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

    async def _ac_blacklisted_guild_id(
        self, interaction: discord.Interaction, current: str
    ):
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
            if not self.db:
                return []
            rows = await self.db.list_blacklisted_guilds()
            for gid, name, reason in rows:
                nm = name or "<unknown>"
                label = f"{nm} ({gid})"
                hay = f"{nm} {gid} {reason or ''}".lower()
                if not current or current in hay:
                    items.append(
                        discord.app_commands.Choice(name=label, value=str(gid))
                    )
                if len(items) >= 25:
                    break
        except Exception:
            return []
        return items

    async def _command_cooldown_check(self, interaction: discord.Interaction) -> bool:
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
            if interaction.guild and self.db:
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

    async def _resolve_target_guild(
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

    async def _owner_destructive_check(self, interaction: discord.Interaction) -> bool:
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

    async def cmd_set_setting(
        self,
        interaction: discord.Interaction,
        key: Optional[str] = None,
        value: Optional[str] = None,
        pairs: Optional[str] = None,
        server_id: Optional[str] = None,
    ):
        target_gid = await resolve_target_guild(interaction, server_id)
        if target_gid is None:
            return

        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        # Must be in the target guild to modify its settings
        if self.get_guild(target_gid) is None:
            await interaction.response.send_message(
                "I am not in that server. I can only modify settings for servers I'm currently in.",
                ephemeral=True,
            )
            return
        # Permission check: owner bypass; otherwise must be bot admin of target guild
        if not (OWNER_ID and interaction.user.id == OWNER_ID):
            if not await self._is_bot_admin(target_gid, interaction.user.id):
                await interaction.response.send_message(
                    "You are not authorized to modify settings for that server.",
                    ephemeral=True,
                )
                return

        settings = await self.db.get_settings(target_gid)
        warn_disabled = None
        if not settings.enabled:
            warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."

        key_alias = {
            "enabled": "enabled",
            "check_length": "check_length",
            "min_nick_length": "min_nick_length",
            "max_nick_length": "max_nick_length",
            "cooldown_seconds": "cooldown_seconds",
            "preserve_spaces": "preserve_spaces",
            "sanitize_emoji": "sanitize_emoji",
            "enforce_bots": "enforce_bots",
            "logging_channel_id": "logging_channel_id",
            "bypass_role_id": "bypass_role_id",
            "fallback_mode": "fallback_mode",
            "fallback_label": "fallback_label",
        }
        allowed_user_keys = {
            "enabled",
            "preserve_spaces",
            "sanitize_emoji",
            "enforce_bots",
            "check_length",
            "min_nick_length",
            "max_nick_length",
            "cooldown_seconds",
            "logging_channel_id",
            "bypass_role_id",
            "fallback_mode",
            "fallback_label",
        }

        def _unquote(s: str) -> str:
            s = (s or "").strip()
            if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
                return s[1:-1]
            return s

        if pairs:
            try:
                raw_tokens = shlex.split(pairs)
            except Exception:
                raw_tokens = pairs.split()
            tokens = [t for t in raw_tokens if "=" in t]
            if not tokens:
                await interaction.response.send_message(
                    "No valid key=value pairs provided.", ephemeral=True
                )
                return
            updated = []
            will_enable = False
            errors = []
            for tok in tokens:
                k, v_raw = tok.split("=", 1)
                raw_k = k.strip().lower()
                if raw_k not in allowed_user_keys:
                    errors.append(f"Unsupported key: {raw_k}")
                    continue
                k = key_alias.get(raw_k, raw_k)
                v_raw = _unquote(v_raw.strip())
                try:
                    if k in {
                        "check_length",
                        "min_nick_length",
                        "max_nick_length",
                        "cooldown_seconds",
                    }:
                        v = int(v_raw)
                        if k == "min_nick_length" and v > 8:
                            v = 8
                        if k == "max_nick_length" and v > 32:
                            v = 32
                    elif k in {
                        "preserve_spaces",
                        "sanitize_emoji",
                        "enforce_bots",
                        "enabled",
                    }:
                        v = parse_bool_str(v_raw)
                        if k == "enabled" and bool(v) is True:
                            will_enable = True
                    elif k in {"logging_channel_id", "bypass_role_id"}:
                        v = (
                            int(v_raw)
                            if v_raw.lower() not in {"none", "null", "unset"}
                            else None
                        )
                    elif k == "fallback_label":
                        lab = v_raw.strip()
                        if lab.lower() in {"none", "null", "unset"}:
                            v = None
                        else:
                            if not (1 <= len(lab) <= 20) or not re.fullmatch(
                                r"[A-Za-z0-9 \-]+", lab
                            ):
                                raise ValueError(
                                    "fallback_label must be 1-20 characters: letters, numbers, spaces, or dashes"
                                )
                            v = lab
                    elif k == "fallback_mode":
                        mv = v_raw.strip().lower()
                        if mv not in {"default", "randomized", "static"}:
                            raise ValueError(
                                "fallback_mode must be one of: default, randomized, static"
                            )
                        v = mv
                    else:
                        errors.append(f"Unsupported key: {k}")
                        continue
                    await self.db.set_setting(target_gid, k, v)
                    updated.append(f"{k}={v}")
                except Exception as e:
                    errors.append(f"{k}: {e}")
            msg = []
            if updated:
                msg.append("Updated: " + ", ".join(updated))
            if errors:
                msg.append("Errors: " + "; ".join(errors))
            text = "\n".join(msg) if msg else "No changes."
            if warn_disabled and not will_enable:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return

        if not key:
            await interaction.response.send_message(
                "Provide a key or use the 'pairs' argument for multiple updates.",
                ephemeral=True,
            )
            return
        raw_key = key.lower()
        if raw_key not in allowed_user_keys:
            await interaction.response.send_message(
                "Unsupported setting.", ephemeral=True
            )
            return
        key = key_alias.get(raw_key, raw_key)

        if value is None:
            s = await self.db.get_settings(target_gid)
            if key == "check_length":
                cur = s.check_length
            elif key == "min_nick_length":
                cur = s.min_nick_length
            elif key == "max_nick_length":
                cur = s.max_nick_length
            elif key == "preserve_spaces":
                cur = s.preserve_spaces
            elif key == "cooldown_seconds":
                cur = s.cooldown_seconds
            elif key == "sanitize_emoji":
                cur = s.sanitize_emoji
            elif key == "enforce_bots":
                cur = s.enforce_bots
            elif key == "fallback_mode":
                cur = getattr(s, "fallback_mode", "default")
            elif key == "enabled":
                cur = s.enabled
            elif key == "logging_channel_id":
                cur = s.logging_channel_id
            elif key == "bypass_role_id":
                cur = s.bypass_role_id
            else:
                await interaction.response.send_message(
                    "Unsupported setting.", ephemeral=True
                )
                return
            text = f"Current {key}: {cur}"
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        try:
            if value is not None:
                value = _unquote(value)
            if key in {
                "check_length",
                "min_nick_length",
                "max_nick_length",
                "cooldown_seconds",
            }:
                v = int(value)
                if key == "min_nick_length" and v > 8:
                    v = 8
                if key == "max_nick_length" and v > 32:
                    v = 32
            elif key in {
                "preserve_spaces",
                "sanitize_emoji",
                "enforce_bots",
                "enabled",
            }:
                v = parse_bool_str(value)
            elif key == "fallback_mode":
                mv = value.strip().lower()
                if mv not in {"default", "randomized", "static"}:
                    raise ValueError(
                        "fallback_mode must be one of: default, randomized, static"
                    )
                v = mv
            elif key in {"logging_channel_id", "bypass_role_id"}:
                v = (
                    int(value)
                    if value.strip().lower() not in {"none", "null", "unset"}
                    else None
                )
            elif key == "fallback_label":
                lab = value.strip()
                if lab.lower() in {"none", "null", "unset"}:
                    v = None
                else:
                    if not (1 <= len(lab) <= 20) or not re.fullmatch(
                        r"[A-Za-z0-9 \-]+", lab
                    ):
                        await interaction.response.send_message(
                            "fallback_label must be 1-20 characters: letters, numbers, spaces, or dashes.",
                            ephemeral=True,
                        )
                        return
                    v = lab
            else:
                await interaction.response.send_message(
                    "Unsupported setting.", ephemeral=True
                )
                return
            await self.db.set_setting(target_gid, key, v)
            # Build a friendly display of the value that was set
            if key == "logging_channel_id":
                display = f"<#{v}>" if v else "None"
            elif key == "bypass_role_id":
                display = f"<@&{v}>" if v else "None"
            elif isinstance(v, bool):
                display = "true" if v else "false"
            elif isinstance(v, str) or v is None:
                display = f"'{v}'" if isinstance(v, str) else "None"
            else:
                display = str(v)
            text = f"Updated {key} to {display}."
            # Suppress the disabled warning if this operation enabled the bot
            if warn_disabled and not (
                key == "enabled" and isinstance(v, bool) and v is True
            ):
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to update setting: {e}", ephemeral=True
            )

    async def cmd_set_enforce_bots(
        self, interaction: discord.Interaction, value: Optional[bool] = None
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "Only bot admins can modify settings.", ephemeral=True
            )
            return
        s = await self.db.get_settings(interaction.guild.id)
        warn_disabled = None
        if not s.enabled:
            warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
        if value is None:
            text = f"Current enforce_bots: {s.enforce_bots}"
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        await self.db.set_setting(interaction.guild.id, "enforce_bots", bool(value))
        text = f"enforce_bots set to {bool(value)}."
        if warn_disabled:
            text = f"{text}\n{warn_disabled}"
        await interaction.response.send_message(text, ephemeral=True)

    async def cmd_set_check_length(
        self, interaction: discord.Interaction, value: Optional[int] = None
    ):
        if value is None:
            s = await self.db.get_settings(interaction.guild.id)  # type: ignore
            warn_disabled = None
            if not s.enabled:
                warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
            text = f"Current check_length: {s.check_length}"
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        await self.cmd_set_setting(interaction, "check_length", str(value))

    async def cmd_set_min_nick_length(
        self, interaction: discord.Interaction, value: Optional[int] = None
    ):
        if value is None:
            s = await self.db.get_settings(interaction.guild.id)  # type: ignore
            warn_disabled = None
            if not s.enabled:
                warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
            text = f"Current min_nick_length: {s.min_nick_length}"
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        await self.cmd_set_setting(interaction, "min_nick_length", str(value))

    async def cmd_set_max_nick_length(
        self, interaction: discord.Interaction, value: Optional[int] = None
    ):
        if value is None:
            s = await self.db.get_settings(interaction.guild.id)  # type: ignore
            warn_disabled = None
            if not s.enabled:
                warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
            text = f"Current max_nick_length: {s.max_nick_length}"
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        await self.cmd_set_setting(interaction, "max_nick_length", str(value))

    async def cmd_set_preserve_spaces(
        self, interaction: discord.Interaction, value: Optional[bool] = None
    ):
        if value is None:
            s = await self.db.get_settings(interaction.guild.id)  # type: ignore
            warn_disabled = None
            if not s.enabled:
                warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
            text = f"Current preserve_spaces: {s.preserve_spaces}"
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        await self.cmd_set_setting(
            interaction, "preserve_spaces", "true" if value else "false"
        )

    async def cmd_set_cooldown_seconds(
        self, interaction: discord.Interaction, value: Optional[int] = None
    ):
        if value is None:
            s = await self.db.get_settings(interaction.guild.id)  # type: ignore
            warn_disabled = None
            if not s.enabled:
                warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
            text = f"Current cooldown_seconds: {s.cooldown_seconds}"
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        await self.cmd_set_setting(interaction, "cooldown_seconds", str(value))

    async def cmd_set_sanitize_emoji(
        self, interaction: discord.Interaction, value: Optional[bool] = None
    ):
        if value is None:
            s = await self.db.get_settings(interaction.guild.id)  # type: ignore
            warn_disabled = None
            if not s.enabled:
                warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
            text = f"Current sanitize_emoji: {s.sanitize_emoji}"
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        await self.cmd_set_setting(
            interaction, "sanitize_emoji", "true" if value else "false"
        )

    async def cmd_set_fallback_mode(
        self, interaction: discord.Interaction, mode: Optional[str] = None
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "Only bot admins can modify settings.", ephemeral=True
            )
            return
        s = await self.db.get_settings(interaction.guild.id)
        warn_disabled = None
        if not s.enabled:
            warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
        valid = {"default", "randomized", "static"}
        if mode is None:
            text = f"Current fallback_mode: {getattr(s, 'fallback_mode', 'default')}"
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        mval = mode.strip().lower()
        if mval not in valid:
            await interaction.response.send_message(
                "Invalid mode. Use one of: default, randomized, static.",
                ephemeral=True,
            )
            return
        await self.db.set_setting(interaction.guild.id, "fallback_mode", mval)
        text = f"fallback_mode set to {mval}."
        if warn_disabled:
            text = f"{text}\n{warn_disabled}"
        await interaction.response.send_message(text, ephemeral=True)

    async def cmd_set_logging_channel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "Only bot admins can modify settings.", ephemeral=True
            )
            return
        settings = await self.db.get_settings(interaction.guild.id)
        warn_disabled = None
        if not settings.enabled:
            warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
        if channel is None:
            cur = settings.logging_channel_id
            mention = f"<#{cur}>" if cur else "not set"
            text = f"Current logging channel: {mention}"
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        await self.db.set_setting(
            interaction.guild.id, "logging_channel_id", channel.id
        )
        text = f"Logging channel set to {channel.mention}."
        if warn_disabled:
            text = f"{text}\n{warn_disabled}"
        await interaction.response.send_message(text, ephemeral=True)

    async def cmd_set_bypass_role(
        self, interaction: discord.Interaction, role: Optional[discord.Role] = None
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "Only bot admins can modify settings.", ephemeral=True
            )
            return
        settings = await self.db.get_settings(interaction.guild.id)
        warn_disabled = None
        if not settings.enabled:
            warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
        if role is None:
            cur = settings.bypass_role_id
            mention = f"<@&{cur}>" if cur else "not set"
            text = f"Current bypass role: {mention}"
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        await self.db.set_setting(interaction.guild.id, "bypass_role_id", role.id)
        text = f"Bypass role set to {role.mention}."
        if warn_disabled:
            text = f"{text}\n{warn_disabled}"
        await interaction.response.send_message(text, ephemeral=True)

    async def cmd_clear_logging_channel(
        self, interaction: discord.Interaction, confirm: Optional[bool] = False
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "Only bot admins can modify settings.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=true to proceed.", ephemeral=True
            )
            return
        settings = await self.db.get_settings(interaction.guild.id)
        warn_disabled = None
        if not settings.enabled:
            warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
        await self.db.set_setting(interaction.guild.id, "logging_channel_id", None)
        text = "Logging channel cleared (set to default)."
        if warn_disabled:
            text = f"{text}\n{warn_disabled}"
        await interaction.response.send_message(text, ephemeral=True)

    async def cmd_clear_bypass_role(
        self, interaction: discord.Interaction, confirm: Optional[bool] = False
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "Only bot admins can modify settings.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=true to proceed.", ephemeral=True
            )
            return
        settings = await self.db.get_settings(interaction.guild.id)
        warn_disabled = None
        if not settings.enabled:
            warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
        await self.db.set_setting(interaction.guild.id, "bypass_role_id", None)
        text = "Bypass role cleared (set to default)."
        if warn_disabled:
            text = f"{text}\n{warn_disabled}"
        await interaction.response.send_message(text, ephemeral=True)

    async def cmd_set_fallback_label(
        self, interaction: discord.Interaction, value: Optional[str] = None
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "Only bot admins can modify settings.", ephemeral=True
            )
            return
        settings = await self.db.get_settings(interaction.guild.id)
        warn_disabled = None
        if not settings.enabled:
            warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
        if value is None:
            cur = settings.fallback_label or "Illegal Name"
            mode = getattr(settings, "fallback_mode", "default")
            text = f"Current fallback_label: {cur}"
            if mode in ("randomized", "username"):
                text += "\nNote: fallback_label is ignored while fallback_mode is set to randomized or username."
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        lab = value.strip()
        if lab.lower() in {"none", "null", "unset"}:
            await self.db.set_setting(interaction.guild.id, "fallback_label", None)
            text = "fallback_label cleared (set to default)."
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return

        if not (1 <= len(lab) <= 20) or not re.fullmatch(r"[A-Za-z0-9 \-]+", lab):
            await interaction.response.send_message(
                "fallback_label must be 1-20 characters: letters, numbers, spaces, or dashes.",
                ephemeral=True,
            )
            return

        await self.db.set_setting(interaction.guild.id, "fallback_label", lab)
        mode = getattr(settings, "fallback_mode", "default")
        text = f"fallback_label set to '{lab}'."
        if mode in ("randomized", "username"):
            text += "\nWarning: This label will be ignored while fallback_mode=randomized or fallback_mode=username."
        if warn_disabled:
            text = f"{text}\n{warn_disabled}"
        await interaction.response.send_message(text, ephemeral=True)

    async def cmd_clear_fallback_label(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "Only bot admins can modify settings.", ephemeral=True
            )
            return
        settings = await self.db.get_settings(interaction.guild.id)
        warn_disabled = None
        if not settings.enabled:
            warn_disabled = "Note: The sanitizer is currently disabled in this server. Changes will apply after a bot admin runs `/enable-sanitizer`."
        await self.db.set_setting(interaction.guild.id, "fallback_label", None)
        text = "fallback_label cleared (set to default)."
        if warn_disabled:
            text = f"{text}\n{warn_disabled}"
        await interaction.response.send_message(text, ephemeral=True)

    async def cmd_reset_settings(
        self,
        interaction: discord.Interaction,
        server_id: Optional[str] = None,
        confirm: Optional[bool] = False,
    ):
        target_gid = await resolve_target_guild(interaction, server_id)
        if target_gid is None:
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        # Must be in the target guild to reset its settings
        if self.get_guild(target_gid) is None:
            await interaction.response.send_message(
                "I am not in that server. I can only reset settings for servers I'm currently in.",
                ephemeral=True,
            )
            return
        # Permission: owner bypass; otherwise bot admin of target guild
        if not (OWNER_ID and interaction.user.id == OWNER_ID):
            if not await self._is_bot_admin(target_gid, interaction.user.id):
                await interaction.response.send_message(
                    "You are not authorized to reset settings for that server.",
                    ephemeral=True,
                )
                return
        # Require confirmation
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=true to proceed.",
                ephemeral=True,
            )
            return
        # Perform actual reset
        try:
            await self.db.reset_guild_settings(target_gid)
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to reset settings: {e}",
                ephemeral=True,
            )
            return
        note = "The sanitizer is disabled by default; A bot admin needs to run `/enable-sanitizer` to re-enable it."
        scope_note = (
            "for that server"
            if (
                server_id
                and (
                    interaction.guild is None
                    or target_gid != getattr(interaction.guild, "id", None)
                )
            )
            else "for this server"
        )
        await interaction.response.send_message(
            f"Reset settings to defaults {scope_note}. {note}", ephemeral=True
        )

    async def cmd_global_reset_settings(
        self, interaction: discord.Interaction, confirm: Optional[bool] = False
    ):
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=true to proceed.", ephemeral=True
            )
            return
        if not await owner_destructive_check(self, interaction):
            return
        # First, attempt to notify configured logging channels in all guilds
        sent = 0
        try:
            sent = await self._broadcast_to_log_channels(
                f"Global action by owner {interaction.user.mention}: All bot settings will be reset to defaults across all servers. You **_WILL_** need to re-set them."
            )
            if sent:
                log.info("Broadcasted pre-reset alert to %d guild(s).", sent)
        except Exception as e:
            log.debug("Failed to broadcast pre-reset alert: %s", e)
        # Then perform the reset
        count = await self.db.reset_all_settings()
        await interaction.response.send_message(
            f"Reset settings to defaults across {count} server(s). Pre-reset alert sent to {sent} guild(s).",
            ephemeral=True,
        )

    async def cmd_delete_my_data(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        try:
            c1, c2 = await self.db.delete_user_data_in_guild(
                interaction.guild.id, interaction.user.id
            )
            if (c1 or 0) + (c2 or 0) == 0:
                await interaction.response.send_message(
                    "No stored data found for you in this server. \nIf you want your data deleted across all servers, please DM the bot owner listed under /botinfo.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"Deleted your stored database entries in this server (Cooldowns: {c1}, Admin status entries: {c2}). \nIf you want your data deleted across all servers, please DM the bot owner listed under /botinfo.",
                    ephemeral=True,
                )
        except Exception as e:
            msg = str(e).strip()
            detail = f": {msg}" if msg else "."
            await interaction.response.send_message(
                f"Failed to delete your data{detail} \nPlease DM the bot developer and the owner listed under /botinfo with a screenshot of this error if you ever see this message.",
                ephemeral=True,
            )

    async def cmd_delete_user_data(
        self, interaction: discord.Interaction, user: discord.User
    ):
        if not await owner_destructive_check(self, interaction):
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        try:
            n1, n2 = await self.db.delete_user_data_global(user.id)
            if (n1 or 0) + (n2 or 0) == 0:
                await interaction.response.send_message(
                    f"No stored data found for {user.mention} across all servers.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"Deleted data for {user.mention} across all servers (cooldowns: {n1}, admin entries: {n2}).",
                    ephemeral=True,
                )
        except Exception as e:
            msg = str(e).strip()
            detail = f": {msg}" if msg else "."
            await interaction.response.send_message(
                f"Failed to delete data for {user.mention}{detail}", ephemeral=True
            )

    async def cmd_global_delete_user_data(
        self,
        interaction: discord.Interaction,
        confirm: Optional[bool] = False,
    ):
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=true to proceed.", ephemeral=True
            )
            return
        if not await owner_destructive_check(self, interaction):
            return
        try:
            n1, n2 = await self.db.clear_all_user_data()
            try:
                sent = await self._broadcast_to_log_channels(
                    f"Global action by owner {interaction.user.mention}: Deleted ALL stored user data across all servers"
                )
                log.info("Announced user data deletion to %d guild(s).", sent)
            except Exception as be:
                log.debug("Failed to broadcast deletion announcement: %s", be)
            await interaction.response.send_message(
                f"Deleted ALL stored user data across all servers (cooldowns: {n1}, admin entries: {n2}). Announcement sent to logging channels where configured.",
                ephemeral=True,
            )
        except Exception as e:
            msg = str(e).strip()
            detail = f": {msg}" if msg else "."
            await interaction.response.send_message(
                f"Failed to delete all user data{detail}", ephemeral=True
            )

    async def cmd_nuke_bot_admins(
        self,
        interaction: discord.Interaction,
        server_id: Optional[str] = None,
        confirm: Optional[bool] = False,
    ):
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=true to proceed.", ephemeral=True
            )
            return
        if not await owner_destructive_check(self, interaction):
            return
        target_gid = await resolve_target_guild(interaction, server_id)
        if target_gid is None:
            return
        if self.get_guild(target_gid) is None:
            await interaction.response.send_message(
                "I am not in that server. Cannot remove admins for it.", ephemeral=True
            )
            return
        deleted = await self.db.clear_admins(target_gid)
        scope_note = (
            "that server"
            if (
                server_id
                and (
                    interaction.guild is None
                    or target_gid != getattr(interaction.guild, "id", None)
                )
            )
            else "this server"
        )
        await interaction.response.send_message(
            f"Removed {deleted} bot admin(s) from {scope_note}.", ephemeral=True
        )

    async def cmd_global_bot_disable(
        self, interaction: discord.Interaction, confirm: Optional[bool] = False
    ):
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=true to proceed.", ephemeral=True
            )
            return
        if not await owner_destructive_check(self, interaction):
            return
        count = await self.db.disable_all()

        try:
            sent = await self._broadcast_to_log_channels(
                f"Global action by owner {interaction.user.mention}: Sanitizer disabled across all servers."
            )
            if sent:
                log.info("Broadcasted global disable alert to %d guild(s).", sent)
        except Exception as e:
            log.debug("Failed to broadcast global disable alert: %s", e)
        await interaction.response.send_message(
            f"Globally disabled sanitizer across {count} server(s). Announcement sent to logging channels where configured.",
            ephemeral=True,
        )

    async def cmd_global_nuke_bot_admins(
        self, interaction: discord.Interaction, confirm: Optional[bool] = False
    ):
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=true to proceed.", ephemeral=True
            )
            return
        if not await owner_destructive_check(self, interaction):
            return
        count = await self.db.clear_admins_global()

        try:
            sent = await self._broadcast_to_log_channels(
                f"Global action by owner {interaction.user.mention}: All bot admins were removed across all servers."
            )
            if sent:
                log.info("Broadcasted global nuke-admins alert to %d guild(s).", sent)
        except Exception as e:
            log.debug("Failed to broadcast global nuke-admins alert: %s", e)
        await interaction.response.send_message(
            f"Removed {count} bot admin(s) across all servers. Announcement sent to logging channels where configured.",
            ephemeral=True,
        )

    async def _broadcast_to_log_channels(self, content: str) -> int:
        """Send a message to the configured logging channel in all guilds.

        Returns the number of guilds where a message was sent.
        """
        if not self.db:
            return 0
        sent = 0
        for guild in list(self.guilds):
            # Fetch the logging channel configured for this guild
            try:
                settings = await self.db.get_settings(guild.id)
                ch_id = settings.logging_channel_id
            except Exception:
                ch_id = None
            if not ch_id:
                continue
            ch = guild.get_channel(ch_id)
            if ch is None:
                try:
                    ch = await guild.fetch_channel(ch_id)
                except Exception:
                    ch = None
            if isinstance(ch, (discord.TextChannel, discord.Thread)):
                try:
                    await ch.send(content)  # type: ignore
                    sent += 1
                except Exception:
                    pass
        return sent

    async def _append_to_recent_log_messages(self, content: str) -> int:
        """Send content as a new message to configured logging channels.

        Sends a new message instead of trying to edit existing messages.
        Returns the number of messages that were sent.
        """
        if not self.db:
            return 0
        sent = 0
        for guild in list(self.guilds):
            # Fetch the logging channel configured for this guild
            try:
                settings = await self.db.get_settings(guild.id)
                ch_id = settings.logging_channel_id
            except Exception:
                ch_id = None
            if not ch_id:
                continue
            ch = guild.get_channel(ch_id)
            if ch is None:
                try:
                    ch = await guild.fetch_channel(ch_id)
                except Exception:
                    ch = None
            if isinstance(ch, (discord.TextChannel, discord.Thread)):
                try:
                    await ch.send(content)
                    sent += 1
                except Exception:
                    pass
        return sent

    async def _version_check_task(self) -> None:
        """Check for updates on startup and periodically at 00:00, 06:00, 12:00, 18:00 UTC."""
        if check_outdated is None:
            return
        try:
            await self.wait_until_ready()
        except Exception:
            return

        # Run check on startup with retries (but don't set yellow/red status during retries)
        max_startup_retries = 3
        for attempt in range(max_startup_retries):
            if await self._run_version_check():
                break
            if attempt < max_startup_retries - 1:
                log.info(
                    "[VERSION] Startup check failed, retrying in 5 minutes (attempt %d/%d)",
                    attempt + 1,
                    max_startup_retries,
                )
                await asyncio.sleep(300)  # 5 minutes
        else:
            log.warning(
                "[VERSION] Startup check failed after %d attempts, will retry at next scheduled check",
                max_startup_retries,
            )

        # Then schedule periodic checks at 00:00, 06:00, 12:00, 18:00 UTC
        while True:
            try:
                next_check_time = self._get_next_check_time()
                sleep_seconds = (
                    next_check_time - datetime.now(timezone.utc)
                ).total_seconds()
                if sleep_seconds > 0:
                    log.debug(
                        "[VERSION] Next check at %s UTC (in %.0f seconds)",
                        next_check_time.strftime("%H:%M:%S"),
                        sleep_seconds,
                    )
                    await asyncio.sleep(sleep_seconds)
                await self._run_version_check()
            except Exception as e:
                log.debug("[VERSION] Periodic check task error: %s", e)
                # Continue to next scheduled check time instead of sleeping

    def _get_next_check_time(self) -> datetime:
        """Get the next scheduled check time (00:00, 06:00, 12:00, or 18:00 UTC)."""
        now = datetime.now(timezone.utc)
        check_hours = [0, 6, 12, 18]

        for hour in check_hours:
            check_time = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if check_time > now:
                return check_time

        # If no hour found today, next check is tomorrow at 00:00
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)

    async def _run_version_check(self) -> bool:
        """Run a single version check. Returns True if check completed (successful or skipped), False on network error."""
        if check_outdated is None:
            return True
        try:
            is_outdated, current, latest, err = await check_outdated()
        except Exception as e:
            log.debug("[VERSION] Check failed (network error): %s", e)
            return False
        if err:
            log.debug("[VERSION] Check skipped: %s", err)
            return True
        if not is_outdated:
            if self._outdated_message:
                self._outdated_message = None
                log.info("[VERSION] Cleared outdated message (up to date)")
            return True
        if not current or not latest:
            return True

        short_current = current[:12]
        short_latest = latest[:12]
        msg = (
            "Update available: this instance is out of date. "
            f"Current: {short_current} Latest: {short_latest}."
        )
        self._outdated_message = msg
        log.warning("[VERSION] %s", msg)
        log.info("[VERSION] Outdated message set for command warnings")
        return True

    async def _maybe_send_outdated_warning(
        self, interaction: discord.Interaction
    ) -> None:
        msg = self._outdated_message
        if not msg:
            return
        # Check if we've already sent a warning for this interaction
        interaction_id = interaction.id
        if interaction_id in self._outdated_warning_sent_interactions:
            return
        self._outdated_warning_sent_interactions.add(interaction_id)
        try:
            if not interaction.response.is_done():
                log.debug("[VERSION] Sending outdated warning as response")
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                log.debug("[VERSION] Sending outdated warning as followup")
                await interaction.followup.send(msg, ephemeral=True)
        except Exception as e:
            log.debug("[VERSION] Failed to send outdated warning: %s", e)

    async def cmd_add_admin(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        server_id: Optional[str] = None,
    ):
        target_gid = await resolve_target_guild(interaction, server_id)
        if target_gid is None:
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can manage admins.", ephemeral=True
            )
            return
        if self.get_guild(target_gid) is None:
            await interaction.response.send_message(
                "I am not in that server. Cannot manage admins for it.", ephemeral=True
            )
            return
        await self.db.add_admin(target_gid, user.id)
        scope_note = (
            "that server"
            if (
                server_id
                and (
                    interaction.guild is None
                    or target_gid != getattr(interaction.guild, "id", None)
                )
            )
            else "this server"
        )
        await interaction.response.send_message(
            f"Added {user.mention} as bot admin for {scope_note}.",
            ephemeral=True,
        )

    async def cmd_remove_admin(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        server_id: Optional[str] = None,
    ):
        target_gid = await resolve_target_guild(interaction, server_id)
        if target_gid is None:
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can manage admins.", ephemeral=True
            )
            return
        if self.get_guild(target_gid) is None:
            await interaction.response.send_message(
                "I am not in that server. Cannot manage admins for it.", ephemeral=True
            )
            return
        await self.db.remove_admin(target_gid, user.id)
        scope_note = (
            "that server"
            if (
                server_id
                and (
                    interaction.guild is None
                    or target_gid != getattr(interaction.guild, "id", None)
                )
            )
            else "this server"
        )
        await interaction.response.send_message(
            f"Removed {user.mention} as bot admin for {scope_note}.",
            ephemeral=True,
        )

    async def cmd_blacklist_server(
        self,
        interaction: discord.Interaction,
        server_id: str,
        reason: Optional[str] = None,
        confirm: Optional[bool] = False,
    ):
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=true to proceed.",
                ephemeral=True,
            )
            return
        if not await owner_destructive_check(self, interaction):
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        try:
            gid = int(server_id)
        except Exception:
            await interaction.response.send_message(
                f"'{server_id}' is not a valid server ID.",
                ephemeral=True,
            )
            return
        # Try to capture a readable name; may be None if not cached
        g_cached = self.get_guild(gid)
        g_name = g_cached.name if g_cached is not None else None
        await self.db.add_blacklisted_guild(gid, reason, g_name)
        # Always delete stored data for this guild (whether or not we're in it)
        try:
            deleted_admins = await self.db.clear_admins(gid)
            await self.db.reset_guild_settings(gid)
        except Exception:
            deleted_admins = 0
        # If currently in that guild, attempt to leave
        g = self.get_guild(gid)
        if g is not None:
            try:
                await g.leave()
                left_note = f" and left guild '{g.name}'"
            except Exception:
                left_note = ""
        else:
            left_note = ""
        suffix = f" Reason: {reason}" if (reason and reason.strip()) else ""
        await interaction.response.send_message(
            f"Blacklisted server ID {gid}{left_note}. Deleted {deleted_admins} admin entries.{suffix}",
            ephemeral=True,
        )

    async def cmd_unblacklist_server(
        self,
        interaction: discord.Interaction,
        server_id: str,
        confirm: Optional[bool] = False,
    ):
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=true to proceed.",
                ephemeral=True,
            )
            return
        if not await owner_destructive_check(self, interaction):
            return
        try:
            gid = int(server_id)
        except Exception:
            await interaction.response.send_message(
                f"'{server_id}' is not a valid server ID.",
                ephemeral=True,
            )
            return
        removed = await self.db.remove_blacklisted_guild(gid)
        if removed:
            msg = f"Removed server ID {gid} from blacklist."
        else:
            msg = f"Server ID {gid} was not in the blacklist."
        await interaction.response.send_message(msg, ephemeral=True)

    async def cmd_set_blacklist_reason(
        self,
        interaction: discord.Interaction,
        server_id: str,
        reason: Optional[str] = None,
    ):
        if not await owner_destructive_check(self, interaction):
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        try:
            gid = int(server_id)
        except Exception:
            await interaction.response.send_message(
                f"'{server_id}' is not a valid server ID.",
                ephemeral=True,
            )
            return
        # Upsert: preserve name, update reason
        try:
            await self.db.add_blacklisted_guild(gid, reason=reason, name=None)
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to set blacklist reason: {e}",
                ephemeral=True,
            )
            return
        text = (
            f"Updated blacklist reason for {gid} to: {reason}"
            if (reason and reason.strip())
            else f"Cleared blacklist reason for {gid}."
        )
        await interaction.response.send_message(text, ephemeral=True)

    async def cmd_set_blacklist_name(
        self,
        interaction: discord.Interaction,
        server_id: str,
        name: Optional[str] = None,
    ):
        if not await owner_destructive_check(self, interaction):
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        try:
            gid = int(server_id)
        except Exception:
            await interaction.response.send_message(
                f"'{server_id}' is not a valid server ID.",
                ephemeral=True,
            )
            return
        # Upsert: preserve reason, update name
        try:
            await self.db.add_blacklisted_guild(gid, reason=None, name=name)
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to set blacklist name: {e}",
                ephemeral=True,
            )
            return
        text = (
            f"Updated blacklist name for {gid} to: {name}"
            if (name and name.strip())
            else f"Cleared blacklist name for {gid}."
        )
        await interaction.response.send_message(text, ephemeral=True)

    async def cmd_list_blacklisted_servers(
        self, interaction: discord.Interaction, attach_file: Optional[bool] = False
    ):
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        try:
            entries = await self.db.list_blacklisted_guilds()
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to load blacklist: {e}",
                ephemeral=True,
            )
            return
        if not entries:
            await interaction.response.send_message(
                "Blacklist is empty.", ephemeral=True
            )
            return
        lines = []
        for gid, name, reason in entries:
            label = f"{name} ({gid})" if (name and name.strip()) else str(gid)
            if reason and reason.strip():
                lines.append(f"- {label} - {reason}")
            else:
                lines.append(f"- {label}")
        header = "Blacklisted servers:\n"
        text = header + ("\n".join(lines) if lines else "")
        try:
            # When attach_file is enabled, send only the file (no inline text), regardless of length
            if attach_file:
                await interaction.user.send(
                    file=discord.File(
                        BytesIO(text.encode("utf-8")), filename="blacklist.md"
                    )
                )
            else:
                # Split between entries: send header first, then chunk lines to respect ~1800-char limit
                header = "Blacklisted servers:\n"
                await interaction.user.send(header.rstrip())
                chunk: list[str] = []
                cur_len = 0
                for line in lines or ["<none>"]:
                    add_len = (1 if chunk else 0) + len(line)
                    if cur_len + add_len > 1800:
                        await interaction.user.send("\n".join(chunk))
                        chunk = [line]
                        cur_len = len(line)
                    else:
                        chunk.append(line)
                        cur_len += add_len
                if chunk:
                    await interaction.user.send("\n".join(chunk))
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "I've sent you the blacklist via DM.",
                    ephemeral=True,
                )
        except Exception:
            # DM failed; send ephemerally. Respect attach_file option: if enabled, send only the file.
            if attach_file:
                try:
                    await interaction.followup.send(
                        file=discord.File(
                            BytesIO(text.encode("utf-8")), filename="blacklist.md"
                        ),
                        ephemeral=True,
                    )
                except Exception:
                    # As last resort, split between entries and send as multiple ephemeral messages
                    header = "Blacklisted servers:\n"
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            header.rstrip(), ephemeral=True
                        )
                    else:
                        await interaction.followup.send(header.rstrip(), ephemeral=True)
                    chunk: list[str] = []
                    cur_len = 0
                    for line in lines or ["<none>"]:
                        add_len = (1 if chunk else 0) + len(line)
                        if cur_len + add_len > 2000:
                            await interaction.followup.send(
                                "\n".join(chunk), ephemeral=True
                            )
                            chunk = [line]
                            cur_len = len(line)
                        else:
                            chunk.append(line)
                            cur_len += add_len
                    if chunk:
                        await interaction.followup.send(
                            "\n".join(chunk), ephemeral=True
                        )
            else:
                # Split between entries and send ephemerally via response/followup (~1800-char chunks)
                header = "Blacklisted servers:\n"
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        header.rstrip(), ephemeral=True
                    )
                else:
                    await interaction.followup.send(header.rstrip(), ephemeral=True)
                chunk: list[str] = []
                cur_len = 0
                for line in lines or ["<none>"]:
                    add_len = (1 if chunk else 0) + len(line)
                    if cur_len + add_len > 1800:
                        await interaction.followup.send(
                            "\n".join(chunk), ephemeral=True
                        )
                        chunk = [line]
                        cur_len = len(line)
                    else:
                        chunk.append(line)
                        cur_len += add_len
                if chunk:
                    await interaction.followup.send("\n".join(chunk), ephemeral=True)

    async def cmd_list_bot_admins(
        self, interaction: discord.Interaction, server_id: Optional[str] = None
    ):
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        gid = await resolve_target_guild(interaction, server_id)
        if gid is None:
            return
        # Presence validation: must be in the target guild
        if self.get_guild(gid) is None:
            await interaction.response.send_message(
                "I am not in that server. Cannot list admins for it.", ephemeral=True
            )
            return
        try:
            ids = await self.db.list_admins(gid)
            if not ids:
                await interaction.response.send_message(
                    "No bot admins are configured for this server.", ephemeral=True
                )
                return
            mentions = [f"<@{uid}>" for uid in ids]
            await interaction.response.send_message(
                "Bot admins for this server: " + ", ".join(mentions),
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to fetch admins: {e}", ephemeral=True
            )

    async def cmd_check_update(self, interaction: discord.Interaction) -> None:
        is_owner = OWNER_ID and interaction.user.id == OWNER_ID
        is_admin = False

        if not is_owner:
            # Allow bot admins if in a guild
            if interaction.guild and self.db:
                try:
                    is_admin = await self.db.is_admin(
                        interaction.guild.id, interaction.user.id
                    )
                    if not is_admin:
                        await interaction.response.send_message(
                            "Only the bot owner or bot admins can perform this action.",
                            ephemeral=True,
                        )
                        return
                except Exception:
                    await interaction.response.send_message(
                        "Only the bot owner can perform this action.", ephemeral=True
                    )
                    return
            else:
                await interaction.response.send_message(
                    "Only the bot owner can perform this action.", ephemeral=True
                )
                return

        # Apply 2 minute cooldown for bot-admins only (not for owner)
        if is_admin and not is_owner:
            current_time = now()
            cooldown_seconds = 120
            time_remaining = cooldown_seconds - (
                current_time - self._last_check_update_time
            )
            if time_remaining > 0:
                await interaction.response.send_message(
                    f"Bot admins can only use /check-update once every 2 minutes. Try again in {int(time_remaining) + 1} seconds.",
                    ephemeral=True,
                )
                return
            self._last_check_update_time = current_time

        if check_outdated is None:
            await interaction.response.send_message(
                "Version check is unavailable.", ephemeral=True
            )
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            is_outdated, current, latest, err = await check_outdated()
        except Exception as e:
            await interaction.followup.send(
                f"Version check failed: {e}", ephemeral=True
            )
            return
        if err:
            await interaction.followup.send(
                f"Version check skipped: {err}", ephemeral=True
            )
            return
        if not current:
            await interaction.followup.send("Current version unknown.", ephemeral=True)
            return
        if not is_outdated:
            if self._outdated_message:
                self._outdated_message = None
                log.info("[VERSION] Cleared outdated message (up to date)")
                try:
                    # Only update presence when transitioning from outdated to up-to-date
                    await self.change_presence(status=self._get_bot_status())
                except Exception:
                    pass
            msg = f"Up to date. Current: {current[:12]}."
            if latest:
                msg = f"Up to date. Current: {current[:12]} Latest: {latest[:12]}."
            await interaction.followup.send(msg, ephemeral=True)
            return
        if not latest:
            await interaction.followup.send(
                "Update available, but latest version is unknown.",
                ephemeral=True,
            )
            return
        msg = (
            "Update available: this instance is out of date. "
            f"Current: {current[:12]} Latest: {latest[:12]}."
        )
        # Only update presence if this is a NEW outdated status (transitioning to outdated)
        if not self._outdated_message:
            self._outdated_message = msg
            log.warning("[VERSION] %s", msg)
            log.info("[VERSION] Outdated message set for command warnings")
            try:
                # Only update presence when transitioning to outdated (showing orange)
                await self.change_presence(status=self._get_bot_status())
            except Exception:
                pass
        else:
            # Already outdated, just update the message without changing presence
            self._outdated_message = msg
        await interaction.followup.send(msg, ephemeral=True)

    async def cmd_dm_admin_report(
        self, interaction: discord.Interaction, attach_file: Optional[bool] = False
    ):
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        # Build report text across all guilds
        lines: list[str] = []
        for g in sorted(self.guilds, key=lambda gg: (gg.name or "", gg.id)):
            try:
                ids = await self.db.list_admins(g.id)
            except Exception:
                ids = []
            if ids:
                mentions = ", ".join(f"<@{uid}>" for uid in ids)
            else:
                mentions = "<none>"
            lines.append(f"• {g.name} ({g.id}) - admins: {len(ids)} - {mentions}")

        try:
            owner_user = interaction.user
            header = "Admin report for all guilds (servers) bot is in:\n"
            full_text = header + ("\n".join(lines) if lines else "<none>")
            if attach_file:
                await owner_user.send(
                    file=discord.File(
                        BytesIO(full_text.encode("utf-8")), filename="admin-report.md"
                    )
                )
            else:
                # Chunk at ~1800 chars, only between entries; send header first
                await owner_user.send(header.rstrip())
                chunk: list[str] = []
                cur_len = 0
                for line in lines or ["<none>"]:
                    add_len = (1 if chunk else 0) + len(line)
                    if cur_len + add_len > 1800:
                        await owner_user.send("\n".join(chunk))
                        chunk = [line]
                        cur_len = len(line)
                    else:
                        chunk.append(line)
                        cur_len += add_len
                if chunk:
                    await owner_user.send("\n".join(chunk))
            await interaction.response.send_message(
                "Sent you a DM with the admin report.",
                ephemeral=True,
            )
        except Exception as e:
            if attach_file:
                try:
                    await interaction.followup.send(
                        file=discord.File(
                            BytesIO(full_text.encode("utf-8")),
                            filename="admin-report.md",
                        ),
                        ephemeral=True,
                    )
                except Exception:
                    await interaction.response.send_message(
                        full_text[:2000], ephemeral=True
                    )
            else:
                await interaction.response.send_message(
                    f"Failed to send DM: {e}", ephemeral=True
                )

    async def cmd_dm_server_settings(
        self, interaction: discord.Interaction, attach_file: Optional[bool] = False
    ):
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return

        lines: list[str] = []
        for g in sorted(self.guilds, key=lambda gg: (gg.name or "", gg.id)):
            try:
                s = await self.db.get_settings(g.id)
            except Exception:
                s = GuildSettings(g.id)
            label = f"{g.name} ({g.id})"

            def b(v: bool) -> str:
                return "true" if v else "false"

            def q(v: str | int | bool | None) -> str:
                return f'"{str(v)}"'

            tokens: list[str] = [
                f"enabled={q(b(s.enabled))}",
                f"check_length={q(s.check_length)}",
                f"enforce_bots={q(b(s.enforce_bots))}",
                f"sanitize_emoji={q(b(s.sanitize_emoji))}",
                f"preserve_spaces={q(b(s.preserve_spaces))}",
                f"min_nick_length={q(s.min_nick_length)}",
                f"max_nick_length={q(s.max_nick_length)}",
                f"cooldown_seconds={q(s.cooldown_seconds)}",
                f"bypass_role_id={q(s.bypass_role_id if s.bypass_role_id else 'none')}",
                f"logging_channel_id={q(s.logging_channel_id if s.logging_channel_id else 'none')}",
                f"fallback_mode={q(s.fallback_mode)}",
            ]
            fb = s.fallback_label
            if (
                fb is None
                or not str(fb).strip()
                or (FALLBACK_LABEL and str(fb).strip() == str(FALLBACK_LABEL).strip())
            ):
                tokens.append(f"fallback_label={q('none')}")
            else:
                tokens.append(f"fallback_label={q(s.fallback_label)}")

            pair_str = " ".join(tokens)
            lines.append("• " + label + "\n" + f"```{pair_str}```")

        try:
            owner_user = interaction.user
            header = "Server settings report for all guilds (servers) bot is in:\n"
            full_text = header + ("\n".join(lines) if lines else "<none>")
            if attach_file:
                await owner_user.send(
                    file=discord.File(
                        BytesIO(full_text.encode("utf-8")),
                        filename="server-settings-report.md",
                    )
                )
            else:
                # Chunk at ~1800 chars, only between entries; send header first
                await owner_user.send(header.rstrip())
                chunk: list[str] = []
                cur_len = 0
                for line in lines or ["<none>"]:
                    add_len = (1 if chunk else 0) + len(line)
                    if cur_len + add_len > 1800:
                        await owner_user.send("\n".join(chunk))
                        chunk = [line]
                        cur_len = len(line)
                    else:
                        chunk.append(line)
                        cur_len += add_len
                if chunk:
                    await owner_user.send("\n".join(chunk))
            await interaction.response.send_message(
                "Sent you a DM with the server settings report.",
                ephemeral=True,
            )
        except Exception as e:
            if attach_file:
                try:
                    await interaction.followup.send(
                        file=discord.File(
                            BytesIO(full_text.encode("utf-8")),
                            filename="server-settings-report.md",
                        ),
                        ephemeral=True,
                    )
                except Exception:
                    await interaction.response.send_message(
                        full_text[:2000], ephemeral=True
                    )
            else:
                await interaction.response.send_message(
                    f"Failed to send DM: {e}", ephemeral=True
                )

    async def cmd_dm_all_reports(
        self, interaction: discord.Interaction, attach_file: Optional[bool] = False
    ):
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return

        # Defer immediately since this takes time
        await interaction.response.defer(ephemeral=True)
        owner_user = interaction.user

        # Build admin report lines
        admin_lines: list[str] = []
        for g in sorted(self.guilds, key=lambda gg: (gg.name or "", gg.id)):
            try:
                ids = await self.db.list_admins(g.id)
            except Exception:
                ids = []
            mentions = ", ".join(f"<@{uid}>" for uid in ids) if ids else "<none>"
            admin_lines.append(f"• {g.name} ({g.id}) - admins: {len(ids)} - {mentions}")

        # Build server settings lines
        settings_lines: list[str] = []
        for g in sorted(self.guilds, key=lambda gg: (gg.name or "", gg.id)):
            try:
                s = await self.db.get_settings(g.id)
            except Exception:
                s = GuildSettings(g.id)
            label = f"{g.name} ({g.id})"

            def b(v: bool) -> str:
                return "true" if v else "false"

            def q(v: str | int | bool | None) -> str:
                return f'"{str(v)}"'

            tokens: list[str] = [
                f"enabled={q(b(s.enabled))}",
                f"check_length={q(s.check_length)}",
                f"enforce_bots={q(b(s.enforce_bots))}",
                f"sanitize_emoji={q(b(s.sanitize_emoji))}",
                f"preserve_spaces={q(s.preserve_spaces)}",
                f"min_nick_length={q(s.min_nick_length)}",
                f"max_nick_length={q(s.max_nick_length)}",
                f"cooldown_seconds={q(s.cooldown_seconds)}",
                f"bypass_role_id={q(s.bypass_role_id if s.bypass_role_id else 'none')}",
                f"logging_channel_id={q(s.logging_channel_id if s.logging_channel_id else 'none')}",
                f"fallback_mode={q(s.fallback_mode)}",
            ]
            fb = s.fallback_label
            if (
                fb is None
                or not str(fb).strip()
                or (FALLBACK_LABEL and str(fb).strip() == str(FALLBACK_LABEL).strip())
            ):
                tokens.append(f"fallback_label={q('none')}")
            else:
                tokens.append(f"fallback_label={q(s.fallback_label)}")
            pair_str = " ".join(tokens)
            settings_lines.append("• " + label + "\n" + f"```{pair_str}```")

        # Build blacklist lines
        bl_lines: list[str] = []
        try:
            entries = await self.db.list_blacklisted_guilds()
        except Exception:
            entries = []
        for gid, name, reason in entries:
            label = f"{name} ({gid})" if (name and name.strip()) else str(gid)
            if reason and reason.strip():
                bl_lines.append(f"• {label} - {reason}")
            else:
                bl_lines.append(f"• {label}")

        # Send each report either as file or chunked messages at 1800 characters
        def chunk_and_send_lines(lines: list[str], header: str):
            return {"header": header, "lines": lines or ["<none>"]}

        reports = [
            chunk_and_send_lines(
                admin_lines, "# Admin Report\n\nBot admins for all guilds (servers):\n"
            ),
            chunk_and_send_lines(
                settings_lines,
                "# Server Settings Report\n\nSettings for all guilds (servers):\n",
            ),
            chunk_and_send_lines(
                bl_lines, "# Blacklist Report\n\nBlacklisted guilds (servers):\n"
            ),
        ]

        try:
            # Send opening message
            await owner_user.send(
                "**All Reports**\n\nGenerating admin, server settings, and blacklist reports..."
            )

            for idx, rep in enumerate(reports):
                header = rep["header"]
                lines = rep["lines"]
                if attach_file:
                    full_text = header + ("\n".join(lines) if lines else "")
                    # Name files distinctly per report
                    fname = (
                        "admin-report.md"
                        if idx == 0
                        else (
                            "server-settings-report.md"
                            if idx == 1
                            else "blacklist-report.md"
                        )
                    )
                    await owner_user.send(
                        file=discord.File(
                            BytesIO(full_text.encode("utf-8")), filename=fname
                        )
                    )
                else:
                    await owner_user.send(header.rstrip())
                    chunk: list[str] = []
                    cur_len = 0
                    for line in lines:
                        add_len = (1 if chunk else 0) + len(line)
                        if cur_len + add_len > 1800:
                            await owner_user.send("\n".join(chunk))
                            chunk = [line]
                            cur_len = len(line)
                        else:
                            chunk.append(line)
                            cur_len += add_len
                    if chunk:
                        await owner_user.send("\n".join(chunk))
            await interaction.followup.send(
                "Sent you DMs with all reports.", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"Failed to send all reports: {e}", ephemeral=True
            )

    async def cmd_leave_server(
        self,
        interaction: discord.Interaction,
        server_id: str,
        confirm: Optional[bool] = False,
    ):
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=true to proceed.",
                ephemeral=True,
            )
            return
        if not await owner_destructive_check(self, interaction):
            return
        # Parse snowflake from text to int; Discord IDs exceed 32-bit
        try:
            gid = int(server_id)
        except Exception:
            await interaction.response.send_message(
                f"'{server_id}' is not a valid server ID.",
                ephemeral=True,
            )
            return
        guild = self.get_guild(gid)
        if guild is None:
            # Attempt fetch if not cached
            try:
                guild = await self.fetch_guild(gid)
            except Exception:
                guild = None
        if guild is None:
            await interaction.response.send_message(
                f"I am not in a server with ID {gid} or it could not be fetched.",
                ephemeral=True,
            )
            return
        # Try to announce intent to leave in logging channel if configured
        try:
            settings = await self.db.get_settings(guild.id)
            ch_id = settings.logging_channel_id
        except Exception:
            ch_id = None
        if ch_id:
            ch = guild.get_channel(ch_id)
            if ch is None:
                try:
                    ch = await guild.fetch_channel(ch_id)
                except Exception:
                    ch = None
            if ch is not None and isinstance(ch, (discord.TextChannel, discord.Thread)):
                try:
                    await ch.send(
                        "Bot owner requested: Leaving this server and deleting stored data for this server."
                    )  # type: ignore
                except Exception:
                    pass
        # Clear admins and settings for this guild
        try:
            deleted_admins = await self.db.clear_admins(guild.id)
            await self.db.reset_guild_settings(guild.id)
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to clear stored data before leaving: {e}", ephemeral=True
            )
            return
        # Acknowledge and leave
        if interaction.response.is_done():
            try:
                await interaction.followup.send(
                    f"Leaving server '{guild.name}' and deleted {deleted_admins} admin entries.",
                    ephemeral=True,
                )
            except Exception:
                pass
        else:
            try:
                await interaction.response.send_message(
                    f"Leaving server '{guild.name}' and deleted {deleted_admins} admin entries.",
                    ephemeral=True,
                )
            except Exception:
                pass
        try:
            await guild.leave()
            await self._dm_owner(
                f"Left guild: {guild.name} ({guild.id}) - Requested by bot owner."
            )
        except Exception:
            # As a fallback, try to kick self if possible
            try:
                me = guild.me
                if me:
                    await guild.kick(me, reason="Owner-requested bot leave")
            except Exception:
                pass
