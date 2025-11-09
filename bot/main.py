# This software uses NNCL v1.1 see LICENSE.md for more info
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

import discord  # type: ignore

from .bot import SanitizerBot
from .config import DISCORD_TOKEN, validate_discord_token

log = logging.getLogger("sanitizerbot")
logging.basicConfig(level=logging.INFO)

try:
    pass  # type: ignore
except Exception:
    log.warning(
        "[TELEMETRY] Disabled: failed to import telemetry module. Running without census."
    )

_LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "INFO").strip().upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_NAME, logging.INFO)
logging.getLogger().setLevel(_LOG_LEVEL)
log.setLevel(_LOG_LEVEL)

# Validate Discord token on startup
validate_discord_token(DISCORD_TOKEN)  # type: ignore

# Set up Discord intents
intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = False
intents.presences = False

bot = SanitizerBot(intents)


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
