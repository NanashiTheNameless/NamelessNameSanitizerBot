# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Command registration for SanitizerBot."""

from .commands_admin import register_admin_commands
from .commands_owner import register_owner_commands
from .commands_public import register_public_commands


def register_all_commands(self):
    register_public_commands(self)
    register_admin_commands(self)
    register_owner_commands(self)
