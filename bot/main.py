# SPDX-License-Identifier: LicenseRef-OQL-1.2
"""
Discord Sanitizer Bot entrypoint.

This module starts a Discord client that enforces nickname sanitization
policies across guilds using slash commands and periodic sweeps. It relies on
environment variables (see .env.example) and optionally a PostgreSQL database
for persistence of per-guild settings and cooldowns.

Highlights:
- Registers slash commands to enable/disable and tune policy values.
- Sanitizes member nicknames on joins, messages, and scheduled sweeps.
- Persists per‑guild settings and user cooldowns when DATABASE_URL is set.

Documentation-only edits should not modify runtime behavior.
"""

import asyncio
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Optional

import asyncpg  # type: ignore
import discord  # type: ignore
import regex as re  # type: ignore
from discord import app_commands  # type: ignore
from discord.ext import tasks  # type: ignore
from dotenv import load_dotenv  # type: ignore

log = logging.getLogger("sanitizerbot")
logging.basicConfig(level=logging.INFO)
load_dotenv()

_LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "INFO").strip().upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_NAME, logging.INFO)
logging.getLogger().setLevel(_LOG_LEVEL)
log.setLevel(_LOG_LEVEL)


def getenv_int(key, default):
    try:
        return int(os.getenv(key, default))
    except Exception:
        return default


def getenv_bool(key, default):
    val = os.getenv(key, str(default)).lower()
    return val in ("1", "true", "yes", "on")


def getenv_int_alias(keys, default):
    """Return int from the first present env key in keys (ordered), else default."""
    for k in keys:
        v = os.getenv(k)
        if v is not None and v != "":
            try:
                return int(v)
            except Exception:
                break
    return default


def parse_bool_str(val: str) -> bool:
    """Parse a case-insensitive boolean string; accepts 1/0, true/false, yes/no, on/off.
    Unrecognized values default to False.
    """
    v = (val or "").strip().lower()
    if v in ("1", "true", "yes", "on", "t", "y"):
        return True
    if v in ("0", "false", "no", "off", "f", "n"):
        return False
    return False


COOLDOWN_SECONDS = getenv_int("COOLDOWN_SECONDS", 60)
CHECK_LENGTH = getenv_int("CHECK_LENGTH", 0)
MIN_NICK_LENGTH = getenv_int("MIN_NICK_LENGTH", 2)
MAX_NICK_LENGTH = getenv_int("MAX_NICK_LENGTH", 32)
PRESERVE_SPACES = getenv_bool("PRESERVE_SPACES", True)
SANITIZE_EMOJI = getenv_bool("SANITIZE_EMOJI", True)
ENFORCE_BOTS = getenv_bool("ENFORCE_BOTS", False)
COOLDOWN_TTL_SEC = getenv_int("COOLDOWN_TTL_SEC", max(86400, COOLDOWN_SECONDS * 10))

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "221701506561212416") or "221701506561212416")
DATABASE_URL = os.getenv("DATABASE_URL")
_APP_ID = os.getenv("APPLICATION_ID", "").strip()
APPLICATION_ID = int(_APP_ID) if _APP_ID.isdigit() else None

SWEEP_INTERVAL_SEC = getenv_int("SWEEP_INTERVAL_SEC", 120)
SWEEP_BATCH = getenv_int("SWEEP_BATCH", 256)


def now():
    return time.time()


@dataclass
class GuildSettings:
    guild_id: int
    check_length: int = CHECK_LENGTH
    min_nick_length: int = MIN_NICK_LENGTH
    max_nick_length: int = MAX_NICK_LENGTH
    preserve_spaces: bool = PRESERVE_SPACES
    cooldown_seconds: int = COOLDOWN_SECONDS
    sanitize_emoji: bool = SANITIZE_EMOJI
    enabled: bool = False
    logging_channel_id: Optional[int] = None
    bypass_role_id: Optional[int] = None
    fallback_label: Optional[str] = None
    enforce_bots: bool = ENFORCE_BOTS


class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        if not self.dsn:
            raise RuntimeError("DATABASE_URL is not configured")
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)

    async def init(self):
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    check_length INTEGER NOT NULL DEFAULT 0,
                    min_nick_length INTEGER NOT NULL DEFAULT 2,
                    max_nick_length INTEGER NOT NULL DEFAULT 32,
                    preserve_spaces BOOLEAN NOT NULL DEFAULT TRUE,
                    cooldown_seconds INTEGER NOT NULL DEFAULT 60,
                    sanitize_emoji BOOLEAN NOT NULL DEFAULT TRUE,
                    enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    logging_channel_id BIGINT,
                    bypass_role_id BIGINT,
                    fallback_label TEXT,
                    enforce_bots BOOLEAN NOT NULL DEFAULT FALSE
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_cooldowns (
                    user_id BIGINT PRIMARY KEY,
                    timestamp DOUBLE PRECISION NOT NULL
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_admins (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
                """
            )
            await conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS sanitize_emoji BOOLEAN NOT NULL DEFAULT TRUE"
            )
            await conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT FALSE"
            )
            await conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS logging_channel_id BIGINT"
            )
            await conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS bypass_role_id BIGINT"
            )
            await conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS fallback_label TEXT"
            )
            await conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS enforce_bots BOOLEAN NOT NULL DEFAULT FALSE"
            )

    async def get_cooldown(self, user_id: int) -> Optional[float]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT timestamp FROM user_cooldowns WHERE user_id=$1", user_id
            )
            return float(row["timestamp"]) if row else None

    async def set_cooldown(self, user_id: int, timestamp: float):
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_cooldowns (user_id, timestamp) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET timestamp = $2",
                user_id,
                timestamp,
            )

    async def clear_expired_cooldowns(self, ttl: int):
        assert self.pool is not None
        cutoff = now() - ttl
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM user_cooldowns WHERE timestamp < $1", cutoff
            )
            await conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS sanitize_emoji BOOLEAN NOT NULL DEFAULT TRUE"
            )
            await conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT FALSE"
            )
            await conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS logging_channel_id BIGINT"
            )
            await conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS bypass_role_id BIGINT"
            )
            await conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS fallback_label TEXT"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_admins (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
                """
            )

    async def delete_user_data_global(self, user_id: int) -> tuple[int, int]:
        """Delete stored data for a user across all guilds.

        Returns (cooldowns_deleted, admin_rows_deleted).
        """
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            res1 = await conn.execute(
                "DELETE FROM user_cooldowns WHERE user_id=$1", user_id
            )
            res2 = await conn.execute(
                "DELETE FROM guild_admins WHERE user_id=$1", user_id
            )
            try:
                n1 = int(res1.split()[-1])
            except Exception:
                n1 = 0
            try:
                n2 = int(res2.split()[-1])
            except Exception:
                n2 = 0
            return n1, n2

    async def delete_user_data_in_guild(
        self, guild_id: int, user_id: int
    ) -> tuple[int, int]:
        """Delete stored data for a user in a single guild.

        Cooldowns are global, so this also clears any cooldown entry if present.
        Returns (cooldowns_deleted, admin_rows_deleted_in_guild).
        """
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            res1 = await conn.execute(
                "DELETE FROM user_cooldowns WHERE user_id=$1", user_id
            )
            res2 = await conn.execute(
                "DELETE FROM guild_admins WHERE guild_id=$1 AND user_id=$2",
                guild_id,
                user_id,
            )
            try:
                n1 = int(res1.split()[-1])
            except Exception:
                n1 = 0
            try:
                n2 = int(res2.split()[-1])
            except Exception:
                n2 = 0
            return n1, n2

    async def clear_all_user_data(self) -> tuple[int, int]:
        """Delete all user-related data across all servers.

        Returns (cooldowns_deleted, admin_rows_deleted).
        """
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            res1 = await conn.execute("DELETE FROM user_cooldowns")
            res2 = await conn.execute("DELETE FROM guild_admins")
            try:
                n1 = int(res1.split()[-1])
            except Exception:
                n1 = 0
            try:
                n2 = int(res2.split()[-1])
            except Exception:
                n2 = 0
            return n1, n2

    async def get_settings(self, guild_id: int) -> GuildSettings:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT guild_id, check_length, min_nick_length, max_nick_length, preserve_spaces, cooldown_seconds, sanitize_emoji, enabled, logging_channel_id, bypass_role_id, fallback_label, enforce_bots FROM guild_settings WHERE guild_id=$1",
                guild_id,
            )
            if row:
                return GuildSettings(
                    guild_id=row["guild_id"],
                    check_length=row["check_length"],
                    min_nick_length=row["min_nick_length"],
                    max_nick_length=row["max_nick_length"],
                    preserve_spaces=row["preserve_spaces"],
                    cooldown_seconds=row["cooldown_seconds"],
                    sanitize_emoji=row["sanitize_emoji"],
                    enabled=row["enabled"],
                    logging_channel_id=row.get("logging_channel_id"),
                    bypass_role_id=row.get("bypass_role_id"),
                    fallback_label=row.get("fallback_label"),
                    enforce_bots=row.get("enforce_bots", False),
                )
            return GuildSettings(guild_id=guild_id)

    async def set_setting(self, guild_id: int, key: str, value):
        assert self.pool is not None

        if key.upper() in {
            "OWNER_ID",
            "DISCORD_TOKEN",
            "SWEEP_BATCH",
            "APPLICATION_ID",
        }:
            raise ValueError("Attempt to modify a protected variable")

        columns = {
            "check_length": "check_length",
            "min_nick_length": "min_nick_length",
            "max_nick_length": "max_nick_length",
            "preserve_spaces": "preserve_spaces",
            "cooldown_seconds": "cooldown_seconds",
            "sanitize_emoji": "sanitize_emoji",
            "enabled": "enabled",
            "logging_channel_id": "logging_channel_id",
            "bypass_role_id": "bypass_role_id",
            "fallback_label": "fallback_label",
            "enforce_bots": "enforce_bots",
        }
        col = columns.get(key)
        if not col:
            raise ValueError(f"Unsupported setting: {key}")
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT (guild_id) DO NOTHING",
                guild_id,
            )

            try:
                await conn.execute(
                    f"UPDATE guild_settings SET {col} = $1 WHERE guild_id=$2",
                    value,
                    guild_id,
                )
            except Exception as e:
                if col == "fallback_label" and isinstance(e, Exception):
                    try:
                        await conn.execute(
                            "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS fallback_label TEXT"
                        )
                        await conn.execute(
                            f"UPDATE guild_settings SET {col} = $1 WHERE guild_id=$2",
                            value,
                            guild_id,
                        )
                        return
                    except Exception:
                        pass
                raise

    async def add_admin(self, guild_id: int, user_id: int):
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO guild_admins (guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                guild_id,
                user_id,
            )

    async def remove_admin(self, guild_id: int, user_id: int):
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM guild_admins WHERE guild_id=$1 AND user_id=$2",
                guild_id,
                user_id,
            )

    async def is_admin(self, guild_id: int, user_id: int) -> bool:
        if OWNER_ID and user_id == OWNER_ID:
            return True
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM guild_admins WHERE guild_id=$1 AND user_id=$2",
                guild_id,
                user_id,
            )
            return row is not None

    async def clear_admins(self, guild_id: int) -> int:
        """Remove all bot admins for a given guild. Returns number of rows deleted."""
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                "DELETE FROM guild_admins WHERE guild_id=$1", guild_id
            )

            try:
                return int(res.split()[-1])
            except Exception:
                return 0

    async def clear_admins_global(self) -> int:
        """Remove all bot admins across all guilds. Returns number of rows deleted."""
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            res = await conn.execute("DELETE FROM guild_admins")
            try:
                return int(res.split()[-1])
            except Exception:
                return 0

    async def disable_all(self) -> int:
        """Globally disable the sanitizer across all guilds. Returns rows updated."""
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            res = await conn.execute("UPDATE guild_settings SET enabled=FALSE")
            try:
                return int(res.split()[-1])
            except Exception:
                return 0

    async def reset_guild_settings(self, guild_id: int) -> int:
        """Delete settings row for a guild so defaults apply next time. Returns rows deleted."""
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                "DELETE FROM guild_settings WHERE guild_id=$1", guild_id
            )
            try:
                return int(res.split()[-1])
            except Exception:
                return 0

    async def reset_all_settings(self) -> int:
        """Delete all guild settings so defaults apply for all guilds. Returns rows deleted."""
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            res = await conn.execute("DELETE FROM guild_settings")
            try:
                return int(res.split()[-1])
            except Exception:
                return 0


r"""Nickname sanitization helpers using the 'regex' package for Unicode handling."""

_rm_marks = re.compile(r"[\p{Cf}\p{Cc}\p{Mn}\p{Me}]")
_allow_ascii = re.compile(r"[^\x20-\x7E]")

_allow_ascii_or_emoji = re.compile(r"[^\x20-\x7E\p{Extended_Pictographic}\u200D\uFE0F]")


def remove_marks_and_controls(s: str) -> str:
    """Remove control, format, and combining marks (Cf, Cc, Mn, Me)."""
    return _rm_marks.sub("", s)


def filter_allowed_chars(s: str, sanitize_emoji: bool) -> str:
    """Apply character policy: ASCII-only when sanitize_emoji=True; otherwise allow emoji."""
    if sanitize_emoji:
        return _allow_ascii.sub("", s)
    return _allow_ascii_or_emoji.sub("", s)


def normalize_spaces(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def sanitize_name(name: str, settings: GuildSettings) -> str:

    head = name
    tail = ""
    if settings.check_length > 0:
        clusters = re.findall(r"\X", name)
        head = "".join(clusters[: settings.check_length])
        tail = "".join(clusters[settings.check_length :])

    head = remove_marks_and_controls(head)
    head = filter_allowed_chars(head, settings.sanitize_emoji)

    if not head.strip():
        candidate = settings.fallback_label or "Illegal Name"
        if len(candidate) > settings.max_nick_length:
            candidate = candidate[: settings.max_nick_length]
        return candidate

    if not settings.preserve_spaces:
        head = normalize_spaces(head)

    candidate = f"{head}{tail}"

    if not settings.preserve_spaces:
        candidate = normalize_spaces(candidate)

    if not candidate or len(candidate) < settings.min_nick_length:
        candidate = f"user{int(time.time() * 1000) % 10000:04d}"

    if len(candidate) > settings.max_nick_length:
        candidate = candidate[: settings.max_nick_length]

    return candidate


def _validate_discord_token(token: str):
    if not token:
        log.error("Missing DISCORD_TOKEN in environment.")
        sys.exit(1)

    placeholders = {"replace_with_your_bot_token", "your_bot_token_here"}
    if token in placeholders:
        log.error(
            "DISCORD_TOKEN looks like a placeholder; please paste the real bot token from the Developer Portal."
        )
        sys.exit(1)
    if any(ch.isspace() for ch in token):
        log.error(
            "DISCORD_TOKEN contains whitespace; ensure there are no spaces or line breaks."
        )
        sys.exit(1)

    if token.count(".") != 2:
        log.error(
            "DISCORD_TOKEN appears malformed (expected three segments separated by '.'). Re-copy the token."
        )
        sys.exit(1)

    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
    if not set(token) <= allowed:
        log.error(
            "DISCORD_TOKEN contains unexpected characters. Re-copy the token and avoid special characters."
        )
        sys.exit(1)

    try:
        import base64

        seg0 = token.split(".")[0]
        seg0 += "=" * (-len(seg0) % 4)
        base64.urlsafe_b64decode(seg0.encode("ascii"))
    except Exception:
        log.warning(
            "DISCORD_TOKEN first segment did not decode via base64; continuing. If login fails, regenerate the token."
        )


intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = False
intents.presences = False

_validate_discord_token(DISCORD_TOKEN)  # type: ignore


class SanitizerBot(discord.Client):
    def __init__(self):
        kwargs = {"intents": intents}
        if APPLICATION_ID:
            kwargs["application_id"] = APPLICATION_ID
        super().__init__(**kwargs)
        self.sweep_cursor = None
        self._stop = asyncio.Event()
        self.db = Database(DATABASE_URL) if DATABASE_URL else None
        self.tree = discord.app_commands.CommandTree(self)

        self._policy_keys = [
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
                name="preserve_spaces (true/false)", value="preserve_spaces"
            ),
            discord.app_commands.Choice(
                name="cooldown_seconds (integer)", value="cooldown_seconds"
            ),
            discord.app_commands.Choice(
                name="sanitize_emoji (true/false)", value="sanitize_emoji"
            ),
            discord.app_commands.Choice(
                name="fallback_label (1-20, letters/numbers/spaces/dashes)",
                value="fallback_label",
            ),
            discord.app_commands.Choice(
                name="enforce_bots (true/false)", value="enforce_bots"
            ),
        ]

    def _register_all_commands(self):

        @self.tree.command(
            name="enable-sanitizer",
            description="Bot Admin Only: Enable the sanitizer in this server",
        )
        async def _enable(interaction: discord.Interaction):
            await self.cmd_start(interaction)

        @self.tree.command(
            name="disable-sanitizer",
            description="Bot Admin Only: Disable the sanitizer in this server",
        )
        async def _disable(interaction: discord.Interaction):
            await self.cmd_stop(interaction)

        @self.tree.command(
            name="sanitize-user",
            description="Manage Nicknames Required: Clean up a member's nickname now",
        )
        async def _sanitize(interaction: discord.Interaction, member: discord.Member):
            await self.cmd_sanitize(interaction, member)

        @self.tree.command(
            name="set-policy",
            description="Bot Admin Only: Set or view policy values; supports multiple updates",
        )
        @app_commands.describe(
            key="Policy key to change (ignored if 'pairs' is provided)",
            value="New value for the policy key (leave empty to view current)",
            pairs="Multiple key=value pairs separated by spaces, e.g. 'min_nick_length=3 max_nick_length=24'",
        )
        @app_commands.autocomplete(key=self._ac_policy_key, value=self._ac_policy_value)
        async def _set_policy(
            interaction: discord.Interaction,
            key: Optional[str] = None,
            value: Optional[str] = None,
            pairs: Optional[str] = None,
        ):
            await self.cmd_set_setting(interaction, key, value, pairs)

        @self.tree.command(
            name="set-check-count",
            description="Bot Admin Only: Set or view the number of leading characters (grapheme clusters) to sanitize",
        )
        @app_commands.autocomplete(value=self._ac_int_value)
        async def _set_check_count(
            interaction: discord.Interaction, value: Optional[int] = None
        ):
            await self.cmd_set_check_length(interaction, value)

        @self.tree.command(
            name="set-min-length",
            description="Bot Admin Only: Set or view the minimum allowed nickname length",
        )
        @app_commands.autocomplete(value=self._ac_int_value)
        async def _set_min_nick_lengthgth(
            interaction: discord.Interaction, value: Optional[int] = None
        ):
            await self.cmd_set_min_nick_length(interaction, value)

        @self.tree.command(
            name="set-max-length",
            description="Bot Admin Only: Set or view the maximum allowed nickname length",
        )
        @app_commands.autocomplete(value=self._ac_int_value)
        async def _set_max_nick_lengthgth(
            interaction: discord.Interaction, value: Optional[int] = None
        ):
            await self.cmd_set_max_nick_length(interaction, value)

        @self.tree.command(
            name="set-keep-spaces",
            description="Set or view whether to keep original spacing (true) or normalize spaces (false)",
        )
        async def _set_keep_spaces(
            interaction: discord.Interaction, value: Optional[bool] = None
        ):
            await self.cmd_set_preserve_spaces(interaction, value)

        @self.tree.command(
            name="set-cooldown-seconds",
            description="Bot Admin Only: Set or view the cooldown (in seconds) between nickname edits per user",
        )
        @app_commands.autocomplete(value=self._ac_int_value)
        async def _set_cooldown(
            interaction: discord.Interaction, value: Optional[int] = None
        ):
            await self.cmd_set_cooldown_seconds(interaction, value)

        @self.tree.command(
            name="set-emoji-sanitization",
            description="Bot Admin Only: Enable/disable removing emoji in nicknames or view current value",
        )
        async def _set_emoji(
            interaction: discord.Interaction, value: Optional[bool] = None
        ):
            await self.cmd_set_sanitize_emoji(interaction, value)

        @self.tree.command(
            name="set-enforce-bots",
            description="Bot Admin Only: Enable/disable enforcing nickname rules on other bots or view current value",
        )
        async def _set_enforce_bots(
            interaction: discord.Interaction, value: Optional[bool] = None
        ):
            await self.cmd_set_enforce_bots(interaction, value)

        @self.tree.command(
            name="set-logging-channel",
            description="Bot Admin Only: Set or view the channel to receive nickname change logs",
        )
        async def _set_logging_channel(
            interaction: discord.Interaction,
            channel: Optional[discord.TextChannel] = None,
        ):
            await self.cmd_set_logging_channel(interaction, channel)

        @self.tree.command(
            name="set-bypass-role",
            description="Bot Admin Only: Set or view a role that bypasses nickname sanitization",
        )
        async def _set_bypass_role(
            interaction: discord.Interaction, role: Optional[discord.Role] = None
        ):
            await self.cmd_set_bypass_role(interaction, role)

        @self.tree.command(
            name="clear-logging-channel",
            description="Bot Admin Only: Clear the logging channel",
        )
        async def _clear_logging_channel(interaction: discord.Interaction):
            await self.cmd_clear_logging_channel(interaction)

        @self.tree.command(
            name="clear-bypass-role",
            description="Bot Admin Only: Clear the bypass role",
        )
        async def _clear_bypass_role(interaction: discord.Interaction):
            await self.cmd_clear_bypass_role(interaction)

        @self.tree.command(
            name="nuke-bot-admins",
            description="Bot Owner Only: Remove all bot admins in this server",
        )
        async def _nuke_admins(interaction: discord.Interaction):
            await self.cmd_nuke_bot_admins(interaction)

        @self.tree.command(
            name="global-bot-disable",
            description="Bot Owner Only: Disable the sanitizer bot in all servers",
        )
        async def _global_disable(interaction: discord.Interaction):
            await self.cmd_global_bot_disable(interaction)

        @self.tree.command(
            name="global-nuke-bot-admins",
            description="Bot Owner Only: Remove all bot admins in all servers",
        )
        async def _global_nuke_admins(interaction: discord.Interaction):
            await self.cmd_global_nuke_bot_admins(interaction)

        @self.tree.command(
            name="add-bot-admin",
            description="Bot Owner Only: Add a bot admin for this server",
        )
        async def _add_admin(interaction: discord.Interaction, user: discord.Member):
            await self.cmd_add_admin(interaction, user)

        @self.tree.command(
            name="remove-bot-admin",
            description="Bot Owner Only: Remove a bot admin for this server",
        )
        async def _remove_admin(interaction: discord.Interaction, user: discord.Member):
            await self.cmd_remove_admin(interaction, user)

        @self.tree.command(
            name="set-fallback-label",
            description="Bot Admin Only: Set or view the fallback nickname used when a name is fully illegal",
        )
        async def _set_fallback_label(
            interaction: discord.Interaction, value: Optional[str] = None
        ):
            await self.cmd_set_fallback_label(interaction, value)

        @self.tree.command(
            name="clear-fallback-label",
            description="Bot Admin Only: Clear the fallback nickname",
        )
        async def _clear_fallback_label(interaction: discord.Interaction):
            await self.cmd_clear_fallback_label(interaction)

        @self.tree.command(
            name="reset-settings",
            description="Bot Admin Only: Reset all sanitizer settings to defaults for this server",
        )
        async def _reset_settings(interaction: discord.Interaction):
            await self.cmd_reset_settings(interaction)

        @self.tree.command(
            name="global-reset-settings",
            description="Bot Owner Only: Reset all sanitizer settings to defaults across all servers",
        )
        async def _global_reset_settings(interaction: discord.Interaction):
            await self.cmd_global_reset_settings(interaction)

        @self.tree.command(
            name="delete-my-data",
            description="Everyone: Delete any of your data stored by the bot in this server (cooldowns/admin entries)",
        )
        async def _delete_my_data(interaction: discord.Interaction):
            await self.cmd_delete_my_data(interaction)

        @self.tree.command(
            name="delete-user-data",
            description="Bot Owner Only: Delete a user's stored data across all servers (cooldowns/admin entries)",
        )
        async def _owner_delete_user_data(
            interaction: discord.Interaction, user: discord.User
        ):
            await self.cmd_delete_user_data(interaction, user)

        @self.tree.command(
            name="global-delete-user-data",
            description="Bot Owner Only: Delete all user data across all servers and announce in configured logging channels",
        )
        async def _global_delete_user_data(
            interaction: discord.Interaction,
        ):
            await self.cmd_global_delete_user_data(interaction)

        @self.tree.command(
            name="sweep-now",
            description="Bot Admin Only: Immediately sweep and sanitize members in this server",
        )
        async def _sweep_now(interaction: discord.Interaction):
            await self.cmd_sweep_now(interaction)

    async def setup_hook(self) -> None:

        self._register_all_commands()
        try:
            await self.tree.sync()
            log.info("[STATUS] Slash commands synced globally on startup.")
        except Exception as e:
            log.warning("Failed to sync app commands on startup: %s", e)

    async def on_ready(self):
        if self.db:
            try:
                await self.db.connect()
                await self.db.init()
            except Exception as e:
                log.error("Database initialization failed: %s", e)

        gids = ", ".join(f"{g.name}({g.id})" for g in self.guilds)
        log.info("[STARTUP] Logged in as %s (%s)", self.user, self.user.id)
        log.info("[STARTUP] Connected guilds: %s", gids or "<none>")

        if APPLICATION_ID:
            invite = f"https://discord.com/oauth2/authorize?client_id={APPLICATION_ID}"
            log.info(f"[INFO] Bot invite link: {invite}")
        else:
            log.warning(
                "[INFO] APPLICATION_ID is not set. Set it in your .env to generate a bot invite link."
            )
        if not self.guilds:
            log.warning("[STATUS] No guilds detected. Bot is not in any servers.")

        log.info("[STATUS] Starting member sweep background task.")
        self.member_sweep.start()  # type: ignore

    async def on_guild_join(self, guild: discord.Guild):
        log.info(f"[EVENT] Bot joined new guild: {guild.name} ({guild.id})")

    async def on_member_join(self, member: discord.Member):
        if member.bot:
            try:
                settings = (
                    await self.db.get_settings(member.guild.id)
                    if self.db
                    else GuildSettings(guild_id=member.guild.id)
                )
            except Exception:
                settings = GuildSettings(guild_id=member.guild.id)
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
                    else GuildSettings(guild_id=message.guild.id)
                )
            except Exception:
                settings = GuildSettings(guild_id=message.guild.id)
            if not settings.enforce_bots:
                return

        m = message.author
        if isinstance(m, discord.Member):
            await self._sanitize_member(m, source="message")

    async def _sanitize_member(self, member: discord.Member, source: str):

        settings = GuildSettings(guild_id=member.guild.id)
        if self.db:
            try:
                settings = await self.db.get_settings(member.guild.id)
            except Exception as e:
                log.debug("Failed to get settings for guild %s: %s", member.guild.id, e)

        if member.bot:
            if self.user and member.id == self.user.id:
                return
            if not settings.enforce_bots:
                return

        if not settings.enabled:
            return

        if settings.bypass_role_id and any(
            r.id == settings.bypass_role_id for r in getattr(member, "roles", [])
        ):
            return

        if self.db:
            last_ts = await self.db.get_cooldown(member.id)
            if last_ts is not None and now() - last_ts < settings.cooldown_seconds:
                return

        name_now = member.nick or member.name
        candidate = sanitize_name(name_now, settings)

        if candidate == name_now:
            return

        guild = member.guild
        me = guild.me

        if not me.guild_permissions.manage_nicknames:
            log.warning("Missing Manage Nicknames permission.")
            return

        if member.top_role >= me.top_role and member != me:
            log.debug("Cannot edit %s due to role hierarchy.", member)
            return

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
                        await ch.send(f"Nickname updated: {member.mention} — '{name_now}' → '{candidate}' (via {source})")  # type: ignore
                    except Exception:
                        pass
        except discord.Forbidden:
            log.debug("Forbidden editing nickname for %s.", member)
        except discord.HTTPException as e:
            log.debug("HTTPException editing %s: %s", member, e)

    @tasks.loop(seconds=SWEEP_INTERVAL_SEC)
    async def member_sweep(self):
        total = 0
        for guild in list(self.guilds):

            # Periodically clear expired cooldowns to minimize data retention
            if self.db:
                try:
                    await self.db.clear_expired_cooldowns(COOLDOWN_TTL_SEC)
                except Exception as e:
                    log.debug("clear_expired_cooldowns failed: %s", e)

            settings = GuildSettings(guild_id=guild.id)
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
                log.warning(
                    "Member sweep rate limit/HTTP error in %s: %s", guild.name, e
                )
            if processed:
                log.info("Sweep processed %d members in %s", processed, guild.name)
            total += processed

    @member_sweep.before_loop
    async def before_member_sweep(self):
        await self.wait_until_ready()

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

    async def cmd_start(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "You are not authorized to start the bot in this server.",
                ephemeral=True,
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        await self.db.set_setting(interaction.guild.id, "enabled", True)
        await interaction.response.send_message(
            "Sanitizer enabled for this server.", ephemeral=True
        )

    async def cmd_stop(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not await self._is_bot_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "You are not authorized to stop the bot in this server.", ephemeral=True
            )
            return
        if not self.db:
            await interaction.response.send_message(
                "Database not configured.", ephemeral=True
            )
            return
        await self.db.set_setting(interaction.guild.id, "enabled", False)
        await interaction.response.send_message(
            "Sanitizer disabled for this server.", ephemeral=True
        )

    async def cmd_sanitize(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        settings = GuildSettings(guild_id=interaction.guild.id)
        if self.db:
            try:
                settings = await self.db.get_settings(interaction.guild.id)
            except Exception:
                pass
        if not settings.enabled:
            await interaction.response.send_message(
                "Note: The sanitizer is currently disabled in this server. Automatic enforcement is paused until an admin runs `/enable-sanitizer`.",
                ephemeral=True,
            )

        if not (
            self._is_guild_admin(interaction.user)
            or await self._is_bot_admin(interaction.guild.id, interaction.user.id)
        ):
            await interaction.response.send_message(
                "You must have the Manage Nicknames permission or be a bot admin to use this command.",
                ephemeral=True,
            )
            return
        await self._sanitize_member(member, source="command")
        await interaction.response.send_message(
            f"Sanitization attempted for {member.mention}.", ephemeral=True
        )

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
                before = member.nick or member.name
                await self._sanitize_member(member, source="manual-sweep")
                after = member.nick or member.name
                if before != after:
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

    async def _ac_policy_value(self, interaction: discord.Interaction, current: str):
        key = getattr(getattr(interaction, "namespace", object()), "key", None)
        key = (key or "").lower()

        aliases = {
            "check_length": "check_length",
            "min_nick_length": "min_nick_length",
            "max_nick_length": "max_nick_length",
            "cooldown_seconds": "cooldown_seconds",
            "fallback_label": "fallback_label",
        }
        key = aliases.get(key, key)
        if key in {"check_length", "min_nick_length", "max_nick_length", "cooldown_seconds"}:

            choices = await self._ac_int_value(interaction, current)
            return [
                discord.app_commands.Choice(name=c.name, value=str(c.value))
                for c in choices
            ]
        if key in {"preserve_spaces", "sanitize_emoji", "enforce_bots"}:
            return await self._ac_bool_value(interaction, current)
        return []

    async def cmd_set_setting(
        self,
        interaction: discord.Interaction,
        key: Optional[str] = None,
        value: Optional[str] = None,
        pairs: Optional[str] = None,
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

        key_alias = {
            "check_length": "check_length",
            "min_nick_length": "min_nick_length",
            "max_nick_length": "max_nick_length",
            "cooldown_seconds": "cooldown_seconds",
            "fallback_label": "fallback_label",
            "enforce_bots": "enforce_bots",
        }
        allowed_user_keys = {
            "check_length",
            "min_nick_length",
            "max_nick_length",
            "cooldown_seconds",
            "preserve_spaces",
            "sanitize_emoji",
            "logging_channel_id",
            "bypass_role_id",
            "fallback_label",
            "enforce_bots",
        }

        if pairs:
            tokens = [t for t in pairs.split() if "=" in t]
            if not tokens:
                await interaction.response.send_message(
                    "No valid key=value pairs provided.", ephemeral=True
                )
                return
            updated = []
            errors = []
            for tok in tokens:
                k, v_raw = tok.split("=", 1)
                raw_k = k.strip().lower()
                if raw_k not in allowed_user_keys:
                    errors.append(f"Unsupported key: {raw_k}")
                    continue
                k = key_alias.get(raw_k, raw_k)
                v_raw = v_raw.strip()
                try:
                    if k in {"check_length", "min_nick_length", "max_nick_length", "cooldown_seconds"}:
                        v = int(v_raw)
                    elif k in {"preserve_spaces", "sanitize_emoji", "enforce_bots"}:
                        v = parse_bool_str(v_raw)
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
                    else:
                        errors.append(f"Unsupported key: {k}")
                        continue
                    await self.db.set_setting(interaction.guild.id, k, v)
                    updated.append(f"{k}={v}")
                except Exception as e:
                    errors.append(f"{k}: {e}")
            msg = []
            if updated:
                msg.append("Updated: " + ", ".join(updated))
            if errors:
                msg.append("Errors: " + "; ".join(errors))
            text = "\n".join(msg) if msg else "No changes."
            if warn_disabled:
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
            s = await self.db.get_settings(interaction.guild.id)
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
            if key in {"check_length", "min_nick_length", "max_nick_length", "cooldown_seconds"}:
                v = int(value)
            elif key in {"preserve_spaces", "sanitize_emoji", "enforce_bots"}:
                v = parse_bool_str(value)
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
            await self.db.set_setting(interaction.guild.id, key, v)
            text = f"Updated {key}."
            if warn_disabled:
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
            await interaction.response.send_message(
                f"Current check_length: {s.check_length}", ephemeral=True
            )
            return
        await self.cmd_set_setting(interaction, "check_length", str(value))

    async def cmd_set_min_nick_length(
        self, interaction: discord.Interaction, value: Optional[int] = None
    ):
        if value is None:
            s = await self.db.get_settings(interaction.guild.id)  # type: ignore
            await interaction.response.send_message(
                f"Current min_nick_length: {s.min_nick_length}", ephemeral=True
            )
            return
        await self.cmd_set_setting(interaction, "min_nick_length", str(value))

    async def cmd_set_max_nick_length(
        self, interaction: discord.Interaction, value: Optional[int] = None
    ):
        if value is None:
            s = await self.db.get_settings(interaction.guild.id)  # type: ignore
            await interaction.response.send_message(
                f"Current max_nick_length: {s.max_nick_length}", ephemeral=True
            )
            return
        await self.cmd_set_setting(interaction, "max_nick_length", str(value))

    async def cmd_set_preserve_spaces(
        self, interaction: discord.Interaction, value: Optional[bool] = None
    ):
        if value is None:
            s = await self.db.get_settings(interaction.guild.id)  # type: ignore
            await interaction.response.send_message(
                f"Current preserve_spaces: {s.preserve_spaces}", ephemeral=True
            )
            return
        await self.cmd_set_setting(
            interaction, "preserve_spaces", "true" if value else "false"
        )

    async def cmd_set_cooldown_seconds(
        self, interaction: discord.Interaction, value: Optional[int] = None
    ):
        if value is None:
            s = await self.db.get_settings(interaction.guild.id)  # type: ignore
            await interaction.response.send_message(
                f"Current cooldown_seconds: {s.cooldown_seconds}", ephemeral=True
            )
            return
        await self.cmd_set_setting(interaction, "cooldown_seconds", str(value))

    async def cmd_set_sanitize_emoji(
        self, interaction: discord.Interaction, value: Optional[bool] = None
    ):
        if value is None:
            s = await self.db.get_settings(interaction.guild.id)  # type: ignore
            await interaction.response.send_message(
                f"Current sanitize_emoji: {s.sanitize_emoji}", ephemeral=True
            )
            return
        await self.cmd_set_setting(
            interaction, "sanitize_emoji", "true" if value else "false"
        )

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

    async def cmd_clear_logging_channel(self, interaction: discord.Interaction):
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
        await self.db.set_setting(interaction.guild.id, "logging_channel_id", None)
        text = "Logging channel cleared."
        if warn_disabled:
            text = f"{text}\n{warn_disabled}"
        await interaction.response.send_message(text, ephemeral=True)

    async def cmd_clear_bypass_role(self, interaction: discord.Interaction):
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
        await self.db.set_setting(interaction.guild.id, "bypass_role_id", None)
        text = "Bypass role cleared."
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
            text = f"Current fallback_label: {cur}"
            if warn_disabled:
                text = f"{text}\n{warn_disabled}"
            await interaction.response.send_message(text, ephemeral=True)
            return
        lab = value.strip()
        if not (1 <= len(lab) <= 20) or not re.fullmatch(r"[A-Za-z0-9 \-]+", lab):
            await interaction.response.send_message(
                "fallback_label must be 1-20 characters: letters, numbers, spaces, or dashes.",
                ephemeral=True,
            )
            return
        await self.db.set_setting(interaction.guild.id, "fallback_label", lab)
        text = f"fallback_label set to '{lab}'."
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
        text = "fallback_label cleared."
        if warn_disabled:
            text = f"{text}\n{warn_disabled}"
        await interaction.response.send_message(text, ephemeral=True)

    async def cmd_reset_settings(self, interaction: discord.Interaction):
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
        note = "The sanitizer is disabled by default; A bot admin needs to run `/enable-sanitizer` to re-enable it."
        await interaction.response.send_message(
            f"Reset settings to defaults for this server. {note}", ephemeral=True
        )

    async def cmd_global_reset_settings(self, interaction: discord.Interaction):
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
                    "No stored entries found for you in this server.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"Deleted your stored entries in this server (cooldowns: {c1}, admin entries: {c2}).",
                    ephemeral=True,
                )
        except Exception as e:
            msg = str(e).strip()
            detail = f": {msg}" if msg else "."
            await interaction.response.send_message(
                f"Failed to delete your data{detail}", ephemeral=True
            )

    async def cmd_delete_user_data(
        self, interaction: discord.Interaction, user: discord.User
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

    async def cmd_nuke_bot_admins(self, interaction: discord.Interaction):
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
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can perform this action.", ephemeral=True
            )
            return
        deleted = await self.db.clear_admins(interaction.guild.id)
        await interaction.response.send_message(
            f"Removed {deleted} bot admin(s) from this server.", ephemeral=True
        )

    async def cmd_global_bot_disable(self, interaction: discord.Interaction):
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

    async def cmd_global_nuke_bot_admins(self, interaction: discord.Interaction):
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

    async def cmd_add_admin(
        self, interaction: discord.Interaction, user: discord.Member
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
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can manage admins.", ephemeral=True
            )
            return
        await self.db.add_admin(interaction.guild.id, user.id)
        await interaction.response.send_message(
            f"Added {user.mention} as bot admin for this server.", ephemeral=True
        )

    async def cmd_remove_admin(
        self, interaction: discord.Interaction, user: discord.Member
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
        if not OWNER_ID or interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Only the bot owner can manage admins.", ephemeral=True
            )
            return
        await self.db.remove_admin(interaction.guild.id, user.id)
        await interaction.response.send_message(
            f"Removed {user.mention} as bot admin for this server.", ephemeral=True
        )


bot = SanitizerBot()


def _graceful_exit(signame):
    log.info("Received %s, shutting down.", signame)
    loop = asyncio.get_event_loop()
    loop.create_task(bot.close())


for _sig in ("SIGINT", "SIGTERM"):
    try:
        signal.signal(getattr(signal, _sig), lambda *_: _graceful_exit(_sig))
    except Exception:
        pass

if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN, log_handler=None)
    except KeyboardInterrupt:
        pass
