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
import shlex
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord  # type: ignore
import regex as re  # type: ignore

from .admin_utils import (
    command_cooldown_check,
    is_bot_admin,
    is_guild_admin,
)
from .admin_utils import owner_destructive_check as admin_owner_destructive_check
from .admin_utils import resolve_target_guild as admin_resolve_target_guild
from .autocomplete import (
    ac_blacklisted_guild_id,
    ac_bool_value,
    ac_check_count_value,
    ac_fallback_mode,
    ac_guild_id,
    ac_int_value,
    ac_max_length_value,
    ac_min_length_value,
    ac_policy_key,
    ac_policy_value,
)
from .commands import register_all_commands
from .config import (
    APPLICATION_ID,
    DATABASE_URL,
    DM_OWNER_ON_GUILD_EVENTS,
    OWNER_ID,
    GuildSettings,
    parse_bool_str,
)
from .database import Database
from .events import (
    on_guild_join,
    on_guild_remove,
    on_member_join,
    on_message,
    on_ready,
)
from .helpers import now, owner_destructive_check, resolve_target_guild
from .reports import (
    dm_admin_report,
    dm_all_reports,
    dm_blacklisted_servers,
    dm_server_settings,
)
from .sanitizer import filter_allowed_chars, remove_marks_and_controls, sanitize_name
from .status import (
    get_bot_status,
    load_status_messages,
    status_cycle,
    track_error,
)
from .tasks import before_member_sweep as tasks_before_member_sweep
from .tasks import member_sweep as tasks_member_sweep

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
        self._config_error = False
        self._pending_owner_dms: list[str] = []  # Queue DMs to send on ready

        # Validate owner is configured
        if not OWNER_ID:
            raise ValueError(
                "OWNER_ID environment variable is not set. "
                "Bot owner must be configured. Set OWNER_ID to your Discord user ID."
            )
        # Validate database configuration
        if not DATABASE_URL:
            raise ValueError(
                "DATABASE_URL environment variable is not set. "
                "Bot requires a database to function. Configure DATABASE_URL with your PostgreSQL connection string."
            )

        self._load_status_messages()

        self._policy_keys = [
            discord.app_commands.Choice(name="enabled (True/False)", value="enabled"),
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
                name="preserve_spaces (True/False)", value="preserve_spaces"
            ),
            discord.app_commands.Choice(
                name="sanitize_emoji (True/False)", value="sanitize_emoji"
            ),
            discord.app_commands.Choice(
                name="enforce_bots (True/False)", value="enforce_bots"
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
        load_status_messages(self)

    def _track_error(
        self, error_msg: str = "Unknown error", guild_id: int | None = None
    ):
        track_error(self, error_msg, guild_id)

    def _get_bot_status(self) -> discord.Status:
        return get_bot_status(self)

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
        register_all_commands(self)

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
            self._config_error = True
            self._status_messages = [
                {
                    "text": "500 Command Sync Failed",
                    "duration": 30,
                    "type": "watching",
                }
            ]
            self._track_error(f"Command sync failed: {e}")
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
        await on_ready(self)

    async def on_guild_join(self, guild: discord.Guild):
        await on_guild_join(self, guild)

    async def on_guild_remove(self, guild: discord.Guild):
        await on_guild_remove(self, guild)

    async def on_member_join(self, member: discord.Member):
        await on_member_join(self, member)

    async def on_message(self, message: discord.Message):
        await on_message(self, message)

    async def _sanitize_member(self, member: discord.Member, source: str) -> bool:
        # Don't sanitize if a configuration error is active
        if self._config_error:
            return False

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

    member_sweep = tasks_member_sweep
    before_member_sweep = tasks_before_member_sweep

    async def status_cycle(self):
        await status_cycle(self)

    async def close(self):
        self.member_sweep.cancel()  # type: ignore
        await super().close()

    def _is_guild_admin(self, member: discord.Member) -> bool:
        return is_guild_admin(self, member)

    async def _is_bot_admin(self, guild_id: int, user_id: int) -> bool:
        return await is_bot_admin(self, guild_id, user_id)

    async def cmd_enable_sanitizer(
        self, interaction: discord.Interaction, server_id: Optional[str] = None
    ):
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        target_gid = await resolve_target_guild(interaction, server_id)
        if target_gid is None:
            return
        g = self.get_guild(target_gid)
        if g is None:
            await interaction.response.send_message(
                "I am not in that server.", ephemeral=True
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

    async def cmd_disable_sanitizer(
        self, interaction: discord.Interaction, server_id: Optional[str] = None
    ):
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        target_gid = await resolve_target_guild(interaction, server_id)
        if target_gid is None:
            return
        g = self.get_guild(target_gid)
        if g is None:
            await interaction.response.send_message(
                "I am not in that server.", ephemeral=True
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
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return

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
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
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
        return await ac_policy_key(self, interaction, current)

    async def _ac_bool_value(self, interaction: discord.Interaction, current: str):
        return await ac_bool_value(self, interaction, current)

    async def _ac_int_value(self, interaction: discord.Interaction, current: str):
        return await ac_int_value(self, interaction, current)

    async def _ac_check_count_value(
        self, interaction: discord.Interaction, current: str
    ):
        return await ac_check_count_value(self, interaction, current)

    async def _ac_min_length_value(
        self, interaction: discord.Interaction, current: str
    ):
        return await ac_min_length_value(self, interaction, current)

    async def _ac_max_length_value(
        self, interaction: discord.Interaction, current: str
    ):
        return await ac_max_length_value(self, interaction, current)

    async def _ac_fallback_mode(self, interaction: discord.Interaction, current: str):
        return await ac_fallback_mode(self, interaction, current)

    async def _ac_policy_value(self, interaction: discord.Interaction, current: str):
        return await ac_policy_value(self, interaction, current)

    async def _ac_guild_id(self, interaction: discord.Interaction, current: str):
        return await ac_guild_id(self, interaction, current)

    async def _ac_blacklisted_guild_id(
        self, interaction: discord.Interaction, current: str
    ):
        return await ac_blacklisted_guild_id(self, interaction, current)

    async def _command_cooldown_check(self, interaction: discord.Interaction) -> bool:
        return await command_cooldown_check(self, interaction)

    async def _resolve_target_guild(
        self, interaction: discord.Interaction, server_id: Optional[str]
    ) -> Optional[int]:
        return await admin_resolve_target_guild(self, interaction, server_id)

    async def _owner_destructive_check(self, interaction: discord.Interaction) -> bool:
        return await admin_owner_destructive_check(self, interaction)

    async def cmd_set_setting(
        self,
        interaction: discord.Interaction,
        key: Optional[str] = None,
        value: Optional[str] = None,
        pairs: Optional[str] = None,
        server_id: Optional[str] = None,
    ):
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        target_gid = await resolve_target_guild(interaction, server_id)
        if target_gid is None:
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
                display = "True" if v else "False"
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
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
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

    async def cmd_set_check_count(
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

    async def cmd_set_keep_spaces(
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
            interaction, "preserve_spaces", "True" if value else "False"
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

    async def cmd_set_emoji_sanitization(
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
            interaction, "sanitize_emoji", "True" if value else "False"
        )

    async def cmd_set_fallback_mode(
        self, interaction: discord.Interaction, mode: Optional[str] = None
    ):
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
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
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
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
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
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
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "Only bot admins can modify settings.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=True to proceed.", ephemeral=True
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
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "Only bot admins can modify settings.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=True to proceed.", ephemeral=True
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
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
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
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
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
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        target_gid = await resolve_target_guild(interaction, server_id)
        if target_gid is None:
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
                "Confirmation required: pass confirm=True to proceed.",
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
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=True to proceed.", ephemeral=True
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
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=True to proceed.", ephemeral=True
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
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=True to proceed.", ephemeral=True
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
        if self._config_error:
            await interaction.response.send_message(
                "The bot is currently disabled due to configuration issue(s). Please contact the bot owner.",
                ephemeral=True,
            )
            return
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=True to proceed.", ephemeral=True
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
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=True to proceed.", ephemeral=True
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
                "Confirmation required: pass confirm=True to proceed.",
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
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=True to proceed.",
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

    async def cmd_dm_blacklisted_servers(
        self, interaction: discord.Interaction, attach_file: Optional[bool] = False
    ):
        await dm_blacklisted_servers(self, interaction, attach_file)

    async def cmd_list_bot_admins(
        self, interaction: discord.Interaction, server_id: Optional[str] = None
    ):
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
        await dm_admin_report(self, interaction, attach_file)

    async def cmd_dm_server_settings(
        self, interaction: discord.Interaction, attach_file: Optional[bool] = False
    ):
        await dm_server_settings(self, interaction, attach_file)

    async def cmd_dm_all_reports(
        self, interaction: discord.Interaction, attach_file: Optional[bool] = False
    ):
        await dm_all_reports(self, interaction, attach_file)

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
        if not confirm:
            await interaction.response.send_message(
                "Confirmation required: pass confirm=True to proceed.",
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
