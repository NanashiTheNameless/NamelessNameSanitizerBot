# This software uses NNCL 1.0 see LICENSE.md for more info
"""
Database operations for NamelessNameSanitizerBot.

This module handles all PostgreSQL database interactions including guild settings,
user cooldowns, bot admins, and blacklisted guilds.
"""

import time
from typing import Optional

from psycopg import rows  # type: ignore
from psycopg_pool import AsyncConnectionPool  # type: ignore

from .config import (
    CHECK_LENGTH,
    COOLDOWN_SECONDS,
    ENFORCE_BOTS,
    FALLBACK_MODE,
    MAX_NICK_LENGTH,
    MIN_NICK_LENGTH,
    OWNER_ID,
    PRESERVE_SPACES,
    SANITIZE_EMOJI,
    GuildSettings,
)


def now():
    return time.time()


class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[AsyncConnectionPool] = None

    async def connect(self):
        if not self.dsn:
            raise RuntimeError("DATABASE_URL is not configured")
        # Create an async psycopg connection pool and open it explicitly
        self.pool = AsyncConnectionPool(self.dsn, min_size=1, max_size=5, open=False)
        await self.pool.open()  # type: ignore

    async def init(self):
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    check_length INTEGER NOT NULL DEFAULT {CHECK_LENGTH},
                    min_nick_length INTEGER NOT NULL DEFAULT {MIN_NICK_LENGTH},
                    max_nick_length INTEGER NOT NULL DEFAULT {MAX_NICK_LENGTH},
                    preserve_spaces BOOLEAN NOT NULL DEFAULT {'TRUE' if PRESERVE_SPACES else 'FALSE'},
                    cooldown_seconds INTEGER NOT NULL DEFAULT {COOLDOWN_SECONDS},
                    sanitize_emoji BOOLEAN NOT NULL DEFAULT {'TRUE' if SANITIZE_EMOJI else 'FALSE'},
                    enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    logging_channel_id BIGINT,
                    bypass_role_id BIGINT,
                    fallback_label TEXT,
                    enforce_bots BOOLEAN NOT NULL DEFAULT {'TRUE' if ENFORCE_BOTS else 'FALSE'},
                    fallback_mode TEXT NOT NULL DEFAULT '{FALLBACK_MODE}'
                );
                """
                )
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                CREATE TABLE IF NOT EXISTS user_cooldowns (
                    user_id BIGINT PRIMARY KEY,
                    timestamp DOUBLE PRECISION NOT NULL
                );
                """
                )
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                CREATE TABLE IF NOT EXISTS guild_admins (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
                """
                )
            async with conn.cursor() as cur:
                await cur.execute(
                    f"ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS sanitize_emoji BOOLEAN NOT NULL DEFAULT {'TRUE' if SANITIZE_EMOJI else 'FALSE'}"
                )
            async with conn.cursor() as cur:
                await cur.execute(
                    f"ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS enforce_bots BOOLEAN NOT NULL DEFAULT {'TRUE' if ENFORCE_BOTS else 'FALSE'}"
                )
            # Blacklist table for guilds the bot should automatically leave/avoid
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                CREATE TABLE IF NOT EXISTS blacklist_guilds (
                    guild_id BIGINT PRIMARY KEY,
                    name TEXT,
                    reason TEXT
                );
                """
                )
            # Ensure 'name' column exists for older installs
            async with conn.cursor() as cur:
                await cur.execute(
                    "ALTER TABLE blacklist_guilds ADD COLUMN IF NOT EXISTS name TEXT"
                )
            async with conn.cursor(row_factory=rows.tuple_row) as cur:
                await cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'guild_settings'
                    """
                )
                cols = await cur.fetchall()
            colset = {r[0] for r in cols}
            renames = {
                "check_n": "check_length",
                "min_len": "min_nick_length",
                "max_len": "max_nick_length",
                "cooldown_sec": "cooldown_seconds",
            }
            for old, new in renames.items():
                if old in colset and new not in colset:
                    try:
                        async with conn.cursor() as cur2:
                            await cur2.execute(
                                f"ALTER TABLE guild_settings RENAME COLUMN {old} TO {new}"
                            )
                        colset.remove(old)
                        colset.add(new)
                    except Exception:
                        pass
            for stmt in (
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS logging_channel_id BIGINT",
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS bypass_role_id BIGINT",
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS fallback_label TEXT",
                f"ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS enforce_bots BOOLEAN NOT NULL DEFAULT {'TRUE' if ENFORCE_BOTS else 'FALSE'}",
                f"ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS fallback_mode TEXT NOT NULL DEFAULT '{FALLBACK_MODE}'",
            ):
                async with conn.cursor() as cur:
                    await cur.execute(stmt)

    async def get_cooldown(self, user_id: int) -> Optional[float]:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=rows.dict_row) as cur:
                await cur.execute(
                    "SELECT timestamp FROM user_cooldowns WHERE user_id=%s",
                    (user_id,),
                )
                row = await cur.fetchone()
                return float(row["timestamp"]) if row else None

    async def set_cooldown(self, user_id: int, timestamp: float):
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO user_cooldowns (user_id, timestamp) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET timestamp = EXCLUDED.timestamp",
                    (user_id, timestamp),
                )

    async def clear_expired_cooldowns(self, ttl: int):
        assert self.pool is not None
        cutoff = now() - ttl
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM user_cooldowns WHERE timestamp < %s",
                    (cutoff,),
                )
            for stmt in (
                f"ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS sanitize_emoji BOOLEAN NOT NULL DEFAULT {'TRUE' if SANITIZE_EMOJI else 'FALSE'}",
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS logging_channel_id BIGINT",
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS bypass_role_id BIGINT",
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS fallback_label TEXT",
                f"ALTER TABLE guild_settings ALTER COLUMN check_length SET DEFAULT {CHECK_LENGTH}",
                f"ALTER TABLE guild_settings ALTER COLUMN min_nick_length SET DEFAULT {MIN_NICK_LENGTH}",
                f"ALTER TABLE guild_settings ALTER COLUMN max_nick_length SET DEFAULT {MAX_NICK_LENGTH}",
                f"ALTER TABLE guild_settings ALTER COLUMN preserve_spaces SET DEFAULT {'TRUE' if PRESERVE_SPACES else 'FALSE'}",
                f"ALTER TABLE guild_settings ALTER COLUMN cooldown_seconds SET DEFAULT {COOLDOWN_SECONDS}",
                f"ALTER TABLE guild_settings ALTER COLUMN sanitize_emoji SET DEFAULT {'TRUE' if SANITIZE_EMOJI else 'FALSE'}",
                f"ALTER TABLE guild_settings ALTER COLUMN enforce_bots SET DEFAULT {'TRUE' if ENFORCE_BOTS else 'FALSE'}",
                """
                CREATE TABLE IF NOT EXISTS guild_admins (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
                """,
            ):
                async with conn.cursor() as cur:
                    await cur.execute(stmt)

    async def delete_user_data_global(self, user_id: int) -> tuple[int, int]:
        """Delete stored data for a user across all guilds.

        Returns (cooldowns_deleted, admin_rows_deleted).
        """
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM user_cooldowns WHERE user_id=%s", (user_id,)
                )
                n1 = cur.rowcount or 0
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM guild_admins WHERE user_id=%s", (user_id,)
                )
                n2 = cur.rowcount or 0
            return int(n1), int(n2)

    async def delete_user_data_in_guild(
        self, guild_id: int, user_id: int
    ) -> tuple[int, int]:
        """Delete stored data for a user in a single guild.

        Cooldowns are global, so this also clears any cooldown entry if present.
        Returns (cooldowns_deleted, admin_rows_deleted_in_guild).
        """
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM user_cooldowns WHERE user_id=%s", (user_id,)
                )
                n1 = cur.rowcount or 0
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM guild_admins WHERE guild_id=%s AND user_id=%s",
                    (guild_id, user_id),
                )
                n2 = cur.rowcount or 0
            return int(n1), int(n2)

    async def clear_all_user_data(self) -> tuple[int, int]:
        """Delete all user-related data across all servers.

        Returns (cooldowns_deleted, admin_rows_deleted).
        """
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM user_cooldowns")
                n1 = cur.rowcount or 0
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM guild_admins")
                n2 = cur.rowcount or 0
            return int(n1), int(n2)

    async def get_settings(self, guild_id: int) -> GuildSettings:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=rows.dict_row) as cur:
                await cur.execute(
                    "SELECT guild_id, check_length, min_nick_length, max_nick_length, preserve_spaces, cooldown_seconds, sanitize_emoji, enabled, logging_channel_id, bypass_role_id, fallback_label, enforce_bots, fallback_mode FROM guild_settings WHERE guild_id=%s",
                    (guild_id,),
                )
                row = await cur.fetchone()
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
                        fallback_mode=row.get("fallback_mode", FALLBACK_MODE),
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
            "fallback_mode": "fallback_mode",
        }
        col = columns.get(key)
        if not col:
            raise ValueError(f"Unsupported setting: {key}")
        if col == "min_nick_length":
            try:
                iv = int(value)
            except Exception:
                raise ValueError("min_nick_length must be an integer")
            # Clamp to upper bound 8 if exceeded
            if iv > 8:
                value = 8
            else:
                value = iv
        if col == "max_nick_length":
            try:
                iv = int(value)
            except Exception:
                raise ValueError("max_nick_length must be an integer")
            # Clamp to upper bound 32 if exceeded
            if iv > 32:
                value = 32
            else:
                value = iv
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO guild_settings (guild_id) VALUES (%s) ON CONFLICT (guild_id) DO NOTHING",
                    (guild_id,),
                )

            try:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"UPDATE guild_settings SET {col} = %s WHERE guild_id=%s",
                        (value, guild_id),
                    )
            except Exception as e:
                if col == "fallback_label" and isinstance(e, Exception):
                    try:
                        async with conn.cursor() as cur:
                            await cur.execute(
                                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS fallback_label TEXT"
                            )
                        async with conn.cursor() as cur:
                            await cur.execute(
                                f"UPDATE guild_settings SET {col} = %s WHERE guild_id=%s",
                                (value, guild_id),
                            )
                        return
                    except Exception:
                        pass
                raise

    async def add_admin(self, guild_id: int, user_id: int):
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO guild_admins (guild_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (guild_id, user_id),
                )

    async def remove_admin(self, guild_id: int, user_id: int):
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM guild_admins WHERE guild_id=%s AND user_id=%s",
                    (guild_id, user_id),
                )

    async def list_admins(self, guild_id: int) -> list[int]:
        """Return a list of user IDs who are bot admins for the given guild."""
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=rows.tuple_row) as cur:
                await cur.execute(
                    "SELECT user_id FROM guild_admins WHERE guild_id=%s ORDER BY user_id ASC",
                    (guild_id,),
                )
                rows_ = await cur.fetchall()
                return [int(r[0]) for r in rows_]

    async def add_blacklisted_guild(
        self, guild_id: int, reason: Optional[str] = None, name: Optional[str] = None
    ):
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO blacklist_guilds (guild_id, name, reason) VALUES (%s, %s, %s) "
                    "ON CONFLICT (guild_id) DO UPDATE SET "
                    "name = COALESCE(EXCLUDED.name, blacklist_guilds.name), "
                    "reason = COALESCE(EXCLUDED.reason, blacklist_guilds.reason)",
                    (guild_id, name, reason),
                )

    async def remove_blacklisted_guild(self, guild_id: int) -> int:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM blacklist_guilds WHERE guild_id=%s",
                    (guild_id,),
                )
                return int(cur.rowcount or 0)

    async def is_guild_blacklisted(self, guild_id: int) -> bool:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM blacklist_guilds WHERE guild_id=%s",
                    (guild_id,),
                )
                return (await cur.fetchone()) is not None

    async def list_blacklisted_guilds(
        self,
    ) -> list[tuple[int, Optional[str], Optional[str]]]:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=rows.tuple_row) as cur:
                await cur.execute(
                    "SELECT guild_id, name, reason FROM blacklist_guilds ORDER BY guild_id ASC"
                )
                rows_ = await cur.fetchall()
                return [(int(r[0]), r[1], r[2]) for r in rows_]

    async def get_blacklisted_guild(
        self, guild_id: int
    ) -> Optional[tuple[Optional[str], Optional[str]]]:
        """Return (name, reason) for a blacklisted guild, or None if not present."""
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=rows.tuple_row) as cur:
                await cur.execute(
                    "SELECT name, reason FROM blacklist_guilds WHERE guild_id=%s",
                    (guild_id,),
                )
                row = await cur.fetchone()
                if not row:
                    return None
                return row[0], row[1]

    async def is_admin(self, guild_id: int, user_id: int) -> bool:
        if OWNER_ID and user_id == OWNER_ID:
            return True
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM guild_admins WHERE guild_id=%s AND user_id=%s",
                    (guild_id, user_id),
                )
                row = await cur.fetchone()
                return row is not None

    async def clear_admins(self, guild_id: int) -> int:
        """Remove all bot admins for a given guild. Returns number of rows deleted."""
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM guild_admins WHERE guild_id=%s", (guild_id,)
                )
                return int(cur.rowcount or 0)

    async def clear_admins_global(self) -> int:
        """Remove all bot admins across all guilds. Returns number of rows deleted."""
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM guild_admins")
                return int(cur.rowcount or 0)

    async def disable_all(self) -> int:
        """Globally disable the sanitizer across all guilds. Returns rows updated."""
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE guild_settings SET enabled=FALSE")
                return int(cur.rowcount or 0)

    async def reset_guild_settings(self, guild_id: int) -> int:
        """Delete settings row for a guild so defaults apply next time. Returns rows deleted."""
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM guild_settings WHERE guild_id=%s", (guild_id,)
                )
                return int(cur.rowcount or 0)

    async def reset_all_settings(self) -> int:
        """Delete all guild settings so defaults apply for all guilds. Returns rows deleted."""
        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM guild_settings")
                return int(cur.rowcount or 0)

    async def purge_unknown_guilds(self, known_guild_ids: set[int]) -> int:
        """Delete stored data for guilds that are not in known_guild_ids.

        Removes from guild_admins and guild_settings. Returns total rows deleted.
        """
        assert self.pool is not None
        if not known_guild_ids:
            return 0
        total = 0
        async with self.pool.connection() as conn:
            # Delete admin rows first
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM guild_admins WHERE guild_id NOT IN (SELECT UNNEST(%s::BIGINT[]))",
                    (list(known_guild_ids),),
                )
                total += int(cur.rowcount or 0)
            # Delete settings rows
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM guild_settings WHERE guild_id NOT IN (SELECT UNNEST(%s::BIGINT[]))",
                    (list(known_guild_ids),),
                )
                total += int(cur.rowcount or 0)
        return total
