# This software uses NNCL 1.0 see LICENSE.md for more info
# Helper decorators for allowed installs and contexts
from discord import app_commands  # type: ignore


def _ai(guilds: bool = True, users: bool = True):
    """Return app_commands.allowed_installs if available, else identity decorator.

    This makes commands usable via bot DMs when the library supports it.
    """
    AI = getattr(app_commands, "allowed_installs", None)
    if AI:
        return AI(guilds=guilds, users=False)

    def deco(f):
        return f

    return deco


def _acx(guilds: bool = True, dms: bool = True, private_channels: bool = True):
    """Return app_commands.allowed_contexts if available, else identity decorator."""
    ACX = getattr(app_commands, "allowed_contexts", None)
    if ACX:
        return ACX(guilds=guilds, dms=dms, private_channels=private_channels)

    def deco(f):
        return f

    return deco
