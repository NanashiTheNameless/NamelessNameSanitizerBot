# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Reporting helpers for SanitizerBot."""

from io import BytesIO
from typing import Optional

import discord  # type: ignore

from .config import FALLBACK_LABEL, OWNER_ID, GuildSettings


async def dm_blacklisted_servers(
    self, interaction: discord.Interaction, attach_file: Optional[bool] = False
):
    if not OWNER_ID or interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "Only the bot owner can perform this action.", ephemeral=True
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
        await interaction.response.send_message("Blacklist is empty.", ephemeral=True)
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
                    await interaction.followup.send("\n".join(chunk), ephemeral=True)
        else:
            # Split between entries and send ephemerally via response/followup (~1800-char chunks)
            header = "Blacklisted servers:\n"
            if not interaction.response.is_done():
                await interaction.response.send_message(header.rstrip(), ephemeral=True)
            else:
                await interaction.followup.send(header.rstrip(), ephemeral=True)
            chunk: list[str] = []
            cur_len = 0
            for line in lines or ["<none>"]:
                add_len = (1 if chunk else 0) + len(line)
                if cur_len + add_len > 1800:
                    await interaction.followup.send("\n".join(chunk), ephemeral=True)
                    chunk = [line]
                    cur_len = len(line)
                else:
                    chunk.append(line)
                    cur_len += add_len
            if chunk:
                await interaction.followup.send("\n".join(chunk), ephemeral=True)


async def dm_admin_report(
    self, interaction: discord.Interaction, attach_file: Optional[bool] = False
):
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


async def dm_server_settings(
    self, interaction: discord.Interaction, attach_file: Optional[bool] = False
):
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
            return "True" if v else "False"

        def q(v: str | int | bool | None) -> str:
            return f'"{str(v)}"'

        bypass_ids: list[int] = []
        if getattr(s, "bypass_role_id", None):
            raw = str(s.bypass_role_id)
            tokens = [t for t in raw.replace(",", " ").split() if t]
            for tok in tokens:
                try:
                    bypass_ids.append(int(tok))
                except Exception:
                    pass
        bypass_val = ",".join(str(i) for i in bypass_ids) if bypass_ids else "none"

        bypass_ids: list[int] = []
        if getattr(s, "bypass_role_id", None):
            raw = str(s.bypass_role_id)
            tokens = [t for t in raw.replace(",", " ").split() if t]
            for tok in tokens:
                try:
                    bypass_ids.append(int(tok))
                except Exception:
                    pass
        bypass_val = ",".join(str(i) for i in bypass_ids) if bypass_ids else "none"

        tokens: list[str] = [
            f"enabled={q(b(s.enabled))}",
            f"check_length={q(s.check_length)}",
            f"enforce_bots={q(b(s.enforce_bots))}",
            f"sanitize_emoji={q(b(s.sanitize_emoji))}",
            f"preserve_spaces={q(b(s.preserve_spaces))}",
            f"min_nick_length={q(s.min_nick_length)}",
            f"max_nick_length={q(s.max_nick_length)}",
            f"cooldown_seconds={q(s.cooldown_seconds)}",
            f"bypass_role_id={q(bypass_val)}",
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


async def dm_all_reports(
    self, interaction: discord.Interaction, attach_file: Optional[bool] = False
):
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
            return "True" if v else "False"

        def q(v: str | int | bool | None) -> str:
            return f'"{str(v)}"'

        bypass_ids: list[int] = []
        if getattr(s, "bypass_role_id", None):
            raw = str(s.bypass_role_id)
            tokens_bypass = [t for t in raw.replace(",", " ").split() if t]
            for tok in tokens_bypass:
                try:
                    bypass_ids.append(int(tok))
                except Exception:
                    pass
        bypass_val = ",".join(str(i) for i in bypass_ids) if bypass_ids else "none"

        tokens: list[str] = [
            f"enabled={q(b(s.enabled))}",
            f"check_length={q(s.check_length)}",
            f"enforce_bots={q(b(s.enforce_bots))}",
            f"sanitize_emoji={q(b(s.sanitize_emoji))}",
            f"preserve_spaces={q(b(s.preserve_spaces))}",
            f"min_nick_length={q(s.min_nick_length)}",
            f"max_nick_length={q(s.max_nick_length)}",
            f"cooldown_seconds={q(s.cooldown_seconds)}",
            f"bypass_role_id={q(bypass_val)}",
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
