# SPDX-License-Identifier: LicenseRef-OQL-1.3
"""
Configuration and environment variable handling for NamelessNameSanitizerBot.

This module loads and validates environment variables and defines the GuildSettings dataclass.
"""

import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv  # type: ignore

log = logging.getLogger("sanitizerbot")
load_dotenv()


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


# Configuration values from environment
COOLDOWN_SECONDS = getenv_int("COOLDOWN_SECONDS", 30)
CHECK_LENGTH = getenv_int("CHECK_LENGTH", 0)
MIN_NICK_LENGTH = getenv_int("MIN_NICK_LENGTH", 2)
MAX_NICK_LENGTH = getenv_int("MAX_NICK_LENGTH", 32)
PRESERVE_SPACES = getenv_bool("PRESERVE_SPACES", True)
SANITIZE_EMOJI = getenv_bool("SANITIZE_EMOJI", True)
ENFORCE_BOTS = getenv_bool("ENFORCE_BOTS", False)
FALLBACK_MODE = os.getenv("FALLBACK_MODE", "default").strip().lower()
if FALLBACK_MODE not in ("default", "randomized", "username"):
    FALLBACK_MODE = "default"
FALLBACK_LABEL = os.getenv("FALLBACK_LABEL", "Illegal Name").strip()
COOLDOWN_TTL_SEC = getenv_int("COOLDOWN_TTL_SEC", max(86400, COOLDOWN_SECONDS * 10))
DM_OWNER_ON_GUILD_EVENTS = getenv_bool("DM_OWNER_ON_GUILD_EVENTS", True)
COMMAND_COOLDOWN_SECONDS = getenv_int("COMMAND_COOLDOWN_SECONDS", 2)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "221701506561212416") or "221701506561212416")
DATABASE_URL = os.getenv("DATABASE_URL")
_APP_ID = os.getenv("APPLICATION_ID", "").strip()
APPLICATION_ID = int(_APP_ID) if _APP_ID.isdigit() else None

SWEEP_INTERVAL_SEC = getenv_int("SWEEP_INTERVAL_SEC", 60)
SWEEP_BATCH = getenv_int("SWEEP_BATCH", 512)


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
    fallback_label: Optional[str] = FALLBACK_LABEL
    enforce_bots: bool = ENFORCE_BOTS
    fallback_mode: str = FALLBACK_MODE


def validate_discord_token(token: str):
    """Validate Discord token format and provide helpful error messages."""
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
