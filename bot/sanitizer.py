# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""
Nickname sanitization helpers using the 'regex' package for Unicode handling.

This module provides functions to sanitize member nicknames according to guild policies.
"""

import random
from typing import Tuple

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


def sanitize_name(name: str, settings: GuildSettings) -> Tuple[str, bool]:
    _full = remove_marks_and_controls(name)
    _full = filter_allowed_chars(_full, settings.sanitize_emoji)
    if not settings.preserve_spaces:
        _full = normalize_spaces(_full)
    if not _full.strip():
        mode = getattr(settings, "fallback_mode", "default")
        if mode == "randomized":
            candidate = f"User{random.randrange(10000):04d}"
        elif mode == "static":
            candidate = settings.fallback_label or "Illegal Name"
        else:
            # default mode returns empty to trigger username attempt
            candidate = ""
        if len(candidate) > settings.max_nick_length:
            candidate = candidate[: settings.max_nick_length]
        return candidate, True

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

    # Final full-string filtering only when enforcing the entire name
    if settings.check_length <= 0:
        candidate = remove_marks_and_controls(candidate)
        candidate = filter_allowed_chars(candidate, settings.sanitize_emoji)
        if not settings.preserve_spaces:
            candidate = normalize_spaces(candidate)

    # If entire result is empty after filtering, use the configured fallback label
    used_fallback = False
    if not candidate or not candidate.strip():
        used_fallback = True
        mode = getattr(settings, "fallback_mode", "default")
        if mode == "randomized":
            candidate = f"User{random.randrange(10000):04d}"
        elif mode == "static":
            candidate = settings.fallback_label or "Illegal Name"
        else:
            # default mode returns empty to trigger username attempt
            candidate = ""

    if len(candidate) > settings.max_nick_length:
        candidate = candidate[: settings.max_nick_length]

    min_len = getattr(settings, "min_nick_length", 0)
    if min_len > 0:
        cluster_count = len(re.findall(r"\X", candidate))
        if cluster_count < min_len:
            used_fallback = True
            mode = getattr(settings, "fallback_mode", "default")
            if mode == "randomized":
                candidate = f"User{random.randrange(10000):04d}"
            elif mode == "static":
                candidate = settings.fallback_label or "Illegal Name"
            else:
                # default mode returns empty to trigger username attempt
                candidate = ""
            if len(candidate) > settings.max_nick_length:
                candidate = candidate[: settings.max_nick_length]

    return candidate, used_fallback
