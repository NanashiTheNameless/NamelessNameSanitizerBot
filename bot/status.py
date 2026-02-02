# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Status and health handling for SanitizerBot."""

import asyncio
import logging
import os

import commentjson  # type: ignore
import discord  # type: ignore

from .config import DM_OWNER_ON_ERRORS

log = logging.getLogger("sanitizerbot")


def load_status_messages(self):
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
                            req for req in required_statuses if req not in status_texts
                        ]

                        if missing_statuses:
                            # Missing required author/license credits
                            log.error(
                                f"[STATUS] Missing required statuses: {', '.join(missing_statuses)}"
                            )
                            self._config_error = True
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
                                # Queue DM to send when event loop is ready
                                self._pending_owner_dms.append(
                                    f"**Bot Configuration Error**\n"
                                    f"Status file is missing required credits:\n"
                                    f"{', '.join(missing_statuses)}\n"
                                    f"Status: Bot is now in DnD mode (red status)"
                                )
                            return

                        log.info(
                            f"[STATUS] Loaded {len(self._status_messages)} status messages"
                        )
                        self._config_error = False
                        return
                except ValueError as e:
                    # Invalid JSON or validation errors - set 400 status
                    log.error(f"[STATUS] Invalid bot_statuses.json: {e}")
                    self._config_error = True
                    self._status_messages = [
                        {
                            "text": "400 Invalid Flavortext",
                            "duration": 30,
                            "type": "watching",
                        }
                    ]
                    if DM_OWNER_ON_ERRORS:
                        # Queue DM to send when event loop is ready
                        self._pending_owner_dms.append(
                            f"**Bot Configuration Error**\n"
                            f"Invalid bot_statuses.jsonc file: {e}\n"
                            f"Status: Bot is now in DnD mode (red status)"
                        )
                    return

        # File not found - set 404 status
        log.error("[STATUS] bot_statuses.json not found")
        self._config_error = True
        self._status_messages = [
            {"text": "404 Flavortext not found", "duration": 30, "type": "watching"}
        ]
        if DM_OWNER_ON_ERRORS:
            # Queue DM to send when event loop is ready
            self._pending_owner_dms.append(
                "**Bot Configuration Error**\n"
                "bot_statuses.jsonc file not found\n"
                "Status: Bot is now in DnD mode (red status)"
            )
    except Exception as e:
        log.error(f"[STATUS] Failed to load status messages: {e}")
        self._config_error = True
        self._status_messages = [
            {"text": "404 Flavortext not found", "duration": 30, "type": "watching"}
        ]
        if DM_OWNER_ON_ERRORS:
            # Queue DM to send when event loop is ready
            self._pending_owner_dms.append(
                f"**Bot Configuration Error**\n"
                f"Failed to load status messages: {e}\n"
                f"Status: Bot is now in DnD mode (red status)"
            )


def track_error(self, error_msg: str = "Unknown error", guild_id: int | None = None):
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


def get_bot_status(self) -> discord.Status:
    """Determine bot status color based on error rate, configuration issues, and version.

    Returns:
        discord.Status.online (green) if healthy
        discord.Status.idle (yellow) if out of date (no errors)
        discord.Status.dnd (red) if experiencing errors or configuration issues

    Note: Once red status is triggered, it persists until bot restart.
    """
    # If configuration error (missing status messages, DB failure, etc.), always red
    if self._config_error:
        return discord.Status.dnd

    # If more than 2 errors, show red status (persists until restart)
    if self._error_count > 2:
        return discord.Status.dnd

    # If outdated, show yellow/idle status
    if self._outdated_message:
        return discord.Status.idle

    return discord.Status.online


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
            status_color = get_bot_status(self)

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
            track_error(self, f"Status cycle update failed: {e}")
            await asyncio.sleep(30)  # Wait before retrying
