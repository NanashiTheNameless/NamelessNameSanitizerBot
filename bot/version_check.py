# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/main/LICENSE.md
"""Check for newer commits and report when out of date.

Compares embedded git commit SHA to the latest on GitHub main.
For release tags, compares against the latest semantic version tag.
Uses GitHub API for efficient, authenticated access.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_VERSION_FILE = "/app/.image_version"
_DEFAULT_GIT_SHA_FILE = "/app/.git_sha"
_DEFAULT_GITHUB_LATEST_SHA_URL = (
    "https://api.github.com/repos/NanashiTheNameless/NamelessNameSanitizerBot/commits/main"
)
_GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/NanashiTheNameless/NamelessNameSanitizerBot/releases/latest"
)


def _env_truthy(value: Optional[str]) -> bool:
    s = (value or "").strip().lower()
    return s in {"1", "true", "yes", "on", "y", "t"}


def _get_version_file() -> str:
    return _DEFAULT_VERSION_FILE


def _get_git_sha_file() -> str:
    return _DEFAULT_GIT_SHA_FILE


def _get_current_version() -> Optional[str]:
    env_version = os.getenv("NNSB_IMAGE_VERSION")
    if env_version and env_version.strip():
        return env_version.strip()
    path = _get_version_file()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
                return raw or None
    except Exception as e:
        log.debug("[VERSION] Error reading version file %s: %s", path, e)
    # Fallback to DEVELOPMENT when running locally (no version file)
    return "DEVELOPMENT"


def _get_current_git_sha() -> Optional[str]:
    """Read current git SHA from environment or file."""
    env_sha = os.getenv("NNSB_GIT_SHA") or os.getenv("GITHUB_SHA")
    # Treat "unknown" as invalid (placeholder from local Dockerfile)
    if env_sha and env_sha.strip() and env_sha.lower() != "unknown":
        log.debug("[VERSION] Current git SHA from env: %s", env_sha[:12])
        return env_sha.strip()
    path = _get_git_sha_file()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
                if raw and raw.lower() != "unknown":
                    log.debug("[VERSION] Current git SHA from file: %s", raw[:12])
                    return raw
        else:
            log.debug("[VERSION] Git SHA file not found: %s", path)
    except Exception as e:
        log.debug("[VERSION] Error reading git SHA file: %s", e)
    # Fallback to DEVELOPMENT when running locally (no git SHA file)
    return "DEVELOPMENT"


def _same_version(current: str, latest: str) -> bool:
    """Check if two versions are the same (case-insensitive comparison).

    Strips leading 'v' and whitespace before comparing.
    """
    c = current.strip().lower()
    latest_normalized = latest.strip().lower()
    if c.startswith("v"):
        c = c[1:]
    if latest_normalized.startswith("v"):
        latest_normalized = latest_normalized[1:]
    return c == latest_normalized if c and latest_normalized else False


def _parse_semver(tag: str) -> Optional[tuple[int, int, int, int]]:
    """Parse semantic version tag into (major, minor, patch, build).

    Expected format: vN.N.N.N (e.g., v0.0.4.10). Returns None otherwise.
    """
    t = tag.strip().lower()
    if not t.startswith("v"):
        return None
    nums = t[1:].split(".")
    if len(nums) != 4:
        return None
    try:
        major, minor, patch, build = (int(n) for n in nums)
        return major, minor, patch, build
    except Exception:
        return None


def _pick_latest_tag(tags: list[str]) -> Optional[str]:
    if not tags:
        return None
    latest_env = os.getenv("NNSB_LATEST_TAG")
    if latest_env:
        return latest_env.strip() or None
    semver_tags = []
    for t in tags:
        parsed = _parse_semver(t)
        if parsed:
            semver_tags.append((parsed, t))
    if semver_tags:
        semver_tags.sort(key=lambda item: item[0], reverse=True)
        return semver_tags[0][1]
    if "latest" in [t.lower() for t in tags]:
        return "latest"
    return tags[0]


def _get_latest_git_sha_url() -> str:
    return _DEFAULT_GITHUB_LATEST_SHA_URL


def _fetch_latest_git_sha_sync() -> Optional[str]:
    """Fetch latest git SHA from GitHub main branch."""
    url = _get_latest_git_sha_url()
    headers = {
        "User-Agent": "NamelessNameSanitizerBot/VersionCheck",
        "Accept": "application/vnd.github+json",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode("utf-8")
            payload = json.loads(data)
            if isinstance(payload, dict):
                sha = payload.get("sha")
                if isinstance(sha, str) and sha.strip():
                    return sha.strip()
    except Exception:
        pass
    return None


def _fetch_latest_release_sync() -> Optional[str]:
    """Fetch latest release tag from GitHub."""
    url = _GITHUB_LATEST_RELEASE_URL
    headers = {
        "User-Agent": "NamelessNameSanitizerBot/VersionCheck",
        "Accept": "application/vnd.github+json",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode("utf-8")
            payload = json.loads(data)
            if isinstance(payload, dict):
                tag = payload.get("tag_name")
                if isinstance(tag, str) and tag.strip():
                    return tag.strip()
    except Exception:
        pass
    return None


def _is_release_tag(tag: str) -> bool:
    parsed = _parse_semver(tag)
    return parsed is not None


async def check_outdated() -> tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """Return (is_outdated, current, latest, error_message).
    
    Uses GitHub API for efficiency (single API calls per check).
    Release tags (e.g., v0.0.4.10) query /releases/latest.
    Latest/dev builds query /commits/main.
    
    DEVELOPMENT is always considered outdated.
    """
    if _env_truthy(os.getenv("NNSB_VERSION_CHECK", "1")) is False:
        return False, _get_current_version(), None, "version check disabled"

    current_git = _get_current_git_sha()
    current_version = _get_current_version()

    # DEVELOPMENT is always outdated
    if current_version and current_version.upper() == "DEVELOPMENT":
        try:
            latest_git = await asyncio.to_thread(_fetch_latest_git_sha_sync)
            latest_git = latest_git.strip() if latest_git else None
        except Exception:
            latest_git = None
        return True, current_version, latest_git or "unknown", None

    # If we have a release tag (e.g., v1.2.3), only check against latest release
    if current_version and _is_release_tag(current_version):
        try:
            latest_tag = await asyncio.to_thread(_fetch_latest_release_sync)
        except Exception:
            return False, current_version, None, "failed to fetch latest release"
        if not latest_tag:
            return False, current_version, None, "latest release unknown"
        if _same_version(current_version, latest_tag):
            return False, current_version, latest_tag, None
        return True, current_version, latest_tag, None

    # For latest/dev builds, compare git SHA
    if current_git:
        try:
            latest_git = await asyncio.to_thread(_fetch_latest_git_sha_sync)
        except Exception as e:
            return False, current_git, None, f"failed to fetch latest git sha: {e}"
        latest_git = latest_git.strip() if latest_git else None
        if not latest_git:
            return False, current_git, None, "latest git sha unknown"
        if _same_version(current_git, latest_git):
            return False, current_git, latest_git, None
        # allow short vs full sha
        if current_git.startswith(latest_git) or latest_git.startswith(current_git):
            return False, current_git, latest_git, None
        return True, current_git, latest_git, None

    if not current_version:
        return False, None, None, "current version unknown"
    
    # Fallback: try latest release
    try:
        latest_tag = await asyncio.to_thread(_fetch_latest_release_sync)
    except Exception as e:
        return False, current_version, None, f"failed to fetch latest release: {e}"
    if not latest_tag:
        return False, current_version, None, "latest release unknown"
    if not _same_version(current_version, latest_tag):
        return True, current_version, latest_tag, None
    return False, current_version, latest_tag, None
