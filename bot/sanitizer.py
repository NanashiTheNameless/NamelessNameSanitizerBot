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
# When preserving emoji, exclude ZWJ (U+200D) and variation selector (U+FE0F)
_rm_marks_preserve_emoji = re.compile(r"(?![\u200D\uFE0F])[\p{Cf}\p{Cc}\p{Mn}\p{Me}]")
_allow_ascii = re.compile(r"[^\x20-\x7E]")
_allow_ascii_or_emoji = re.compile(r"[^\x20-\x7E\p{Emoji}\u200D\uFE0F]")
_has_letters_numbers = re.compile(r"[\p{L}\p{N}]")
_has_emoji = re.compile(r"\p{Emoji}")
# Extended_Pictographic avoids counting ASCII digits as emoji in length checks.
_has_emoji_cluster = re.compile(r"\p{Extended_Pictographic}")


def remove_marks_and_controls(s: str, sanitize_emoji: bool = True) -> str:
    """Remove control, format, and combining marks (Cf, Cc, Mn, Me).

    When sanitize_emoji=False, preserve ZWJ (U+200D) and variation selectors (U+FE0F)
    as they are essential for emoji sequences.
    """
    if sanitize_emoji:
        return _rm_marks.sub("", s)
    else:
        return _rm_marks_preserve_emoji.sub("", s)


def filter_allowed_chars(s: str, sanitize_emoji: bool) -> str:
    """Apply character policy: ASCII-only when sanitize_emoji=True; otherwise allow emoji."""
    if sanitize_emoji:
        return _allow_ascii.sub("", s)
    return _allow_ascii_or_emoji.sub("", s)


def has_meaningful_chars(s: str, sanitize_emoji: bool) -> bool:
    """Ensure the sanitized result isn't just punctuation/whitespace.

    When emoji sanitization is disabled, emoji-only names are allowed.
    """
    if _has_letters_numbers.search(s):
        return True
    if not sanitize_emoji and _has_emoji.search(s):
        return True
    return False


def normalize_spaces(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def clean_orphaned_modifiers(s: str) -> str:
    """Remove ZWJ and variation selectors that aren't part of emoji sequences.

    Processes each grapheme cluster and removes ZWJ/variation selectors from
    clusters that don't contain emoji.
    """
    clusters = re.findall(r"\X", s)
    result = []
    for cluster in clusters:
        if "\u200d" in cluster or "\ufe0f" in cluster:
            # This cluster has emoji modifiers
            if not _has_emoji.search(cluster):
                # No emoji in this cluster, remove the orphaned modifiers
                cluster = cluster.replace("\u200d", "").replace("\ufe0f", "")
        result.append(cluster)
    return "".join(result)


def count_non_emoji_clusters(s: str) -> int:
    """Count grapheme clusters excluding spaces, emoji, ZWJ, and variation selectors."""
    clusters = re.findall(r"\X", s)
    count = 0
    for cluster in clusters:
        if cluster == " ":
            continue
        # Skip emoji clusters
        if _has_emoji_cluster.search(cluster):
            continue
        # Skip clusters that are only ZWJ and/or variation selectors
        stripped = cluster.replace("\u200d", "").replace("\ufe0f", "")
        if not stripped:
            continue
        count += 1
    return count


def sanitize_name(name: str, settings: GuildSettings) -> Tuple[str, bool]:
    _full = remove_marks_and_controls(name, settings.sanitize_emoji)
    _full = filter_allowed_chars(_full, settings.sanitize_emoji)
    if not settings.preserve_spaces:
        _full = normalize_spaces(_full)
    # Remove orphaned ZWJ/variation selectors when emoji are allowed
    if not settings.sanitize_emoji:
        _full = clean_orphaned_modifiers(_full)
    if not _full.strip() or not has_meaningful_chars(_full, settings.sanitize_emoji):
        mode = getattr(settings, "fallback_mode", "default")
        if mode == "randomized":
            candidate = f"User{random.randrange(10000):04d}"
        elif mode == "static":
            candidate = settings.fallback_label or "Illegal Name"
            candidate = remove_marks_and_controls(
                candidate, settings.sanitize_emoji
            )
            candidate = filter_allowed_chars(candidate, settings.sanitize_emoji)
            if not settings.preserve_spaces:
                candidate = normalize_spaces(candidate)
            if not candidate.strip():
                candidate = "Illegal Name"
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

    head = remove_marks_and_controls(head, settings.sanitize_emoji)
    head = filter_allowed_chars(head, settings.sanitize_emoji)
    if not settings.preserve_spaces:
        head = normalize_spaces(head)
    # Remove orphaned ZWJ/variation selectors when emoji are allowed
    if not settings.sanitize_emoji:
        head = clean_orphaned_modifiers(head)

    candidate = f"{head}{tail}"

    if not settings.preserve_spaces:
        candidate = normalize_spaces(candidate)

    # Final full-string filtering only when enforcing the entire name
    if settings.check_length <= 0:
        candidate = remove_marks_and_controls(candidate, settings.sanitize_emoji)
        candidate = filter_allowed_chars(candidate, settings.sanitize_emoji)
        if not settings.preserve_spaces:
            candidate = normalize_spaces(candidate)
        # Remove orphaned ZWJ/variation selectors when emoji are allowed
        if not settings.sanitize_emoji:
            candidate = clean_orphaned_modifiers(candidate)

    # If entire result is empty after filtering, use the configured fallback label
    used_fallback = False
    if not candidate or not candidate.strip():
        used_fallback = True
        mode = getattr(settings, "fallback_mode", "default")
        if mode == "randomized":
            candidate = f"User{random.randrange(10000):04d}"
        elif mode == "static":
            candidate = settings.fallback_label or "Illegal Name"
            candidate = remove_marks_and_controls(
                candidate, settings.sanitize_emoji
            )
            candidate = filter_allowed_chars(candidate, settings.sanitize_emoji)
            if not settings.preserve_spaces:
                candidate = normalize_spaces(candidate)
            if not candidate.strip():
                candidate = "Illegal Name"
        else:
            # default mode returns empty to trigger username attempt
            candidate = ""

    if len(candidate) > settings.max_nick_length:
        candidate = candidate[: settings.max_nick_length]

    # Strip candidate before min length validation to match what Discord will store
    # Discord normalizes nicknames by trimming whitespace
    stripped_candidate = candidate.strip()

    min_len = getattr(settings, "min_nick_length", 0)
    if min_len > 0:
        # Count grapheme clusters excluding spaces and emoji for minimum length validation
        cluster_count = count_non_emoji_clusters(stripped_candidate)
        if cluster_count < min_len:
            used_fallback = True
            mode = getattr(settings, "fallback_mode", "default")
            if mode == "randomized":
                candidate = f"User{random.randrange(10000):04d}"
            elif mode == "static":
                candidate = settings.fallback_label or "Illegal Name"
                candidate = remove_marks_and_controls(
                    candidate, settings.sanitize_emoji
                )
                candidate = filter_allowed_chars(candidate, settings.sanitize_emoji)
                if not settings.preserve_spaces:
                    candidate = normalize_spaces(candidate)
                if not candidate.strip():
                    candidate = "Illegal Name"
            else:
                # default mode returns empty to trigger username attempt
                candidate = ""
            if len(candidate) > settings.max_nick_length:
                candidate = candidate[: settings.max_nick_length]

    return candidate, used_fallback
