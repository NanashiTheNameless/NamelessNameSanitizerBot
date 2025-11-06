# SPDX-License-Identifier: LicenseRef-OQL-1.3
"""
Nickname sanitization helpers using the 'regex' package for Unicode handling.

This module provides functions to sanitize member nicknames according to guild policies.
"""

import time

import regex as re  # type: ignore

from .config import GuildSettings

# Regular expressions for sanitization
_rm_marks = re.compile(r"[\p{Cf}\p{Cc}\p{Mn}\p{Me}]")
_allow_ascii = re.compile(r"[^\x20-\x7E]")
_allow_ascii_or_emoji = re.compile(r"[^\x20-\x7E\p{Emoji}\u200D\uFE0F]")


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
    _full = remove_marks_and_controls(name)
    _full = filter_allowed_chars(_full, settings.sanitize_emoji)
    if not settings.preserve_spaces:
        _full = normalize_spaces(_full)
    if not _full.strip():
        candidate = settings.fallback_label or "Illegal Name"
        if len(candidate) < settings.min_nick_length:
            candidate = f"user{int(time.time() * 1000) % 10000:04d}"
        if len(candidate) > settings.max_nick_length:
            candidate = candidate[: settings.max_nick_length]
        return candidate

    head = name
    tail = ""
    if settings.check_length > 0:
        clusters = re.findall(r"\X", name)
        head = "".join(clusters[: settings.check_length])
        tail = "".join(clusters[settings.check_length :])
    else:
        head = _full
        tail = ""

    head = remove_marks_and_controls(head)
    head = filter_allowed_chars(head, settings.sanitize_emoji)
    if not settings.preserve_spaces:
        head = normalize_spaces(head)

    candidate = f"{head}{tail}"

    if not settings.preserve_spaces:
        candidate = normalize_spaces(candidate)

    # Final full-string filtering to guarantee policy is applied across the whole result
    candidate = remove_marks_and_controls(candidate)
    candidate = filter_allowed_chars(candidate, settings.sanitize_emoji)
    if not settings.preserve_spaces:
        candidate = normalize_spaces(candidate)

    # If entire result is empty after filtering, use the configured fallback label
    if not candidate or not candidate.strip():
        candidate = settings.fallback_label or "Illegal Name"

    if len(candidate) < settings.min_nick_length:
        candidate = f"user{int(time.time() * 1000) % 10000:04d}"

    if len(candidate) > settings.max_nick_length:
        candidate = candidate[: settings.max_nick_length]

    return candidate
