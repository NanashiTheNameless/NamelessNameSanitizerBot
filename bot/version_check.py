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
import urllib.parse
import urllib.request
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_VERSION_FILE = "/app/.image_version"
_DEFAULT_GIT_SHA_FILE = "/app/.git_sha"
_GITHUB_API_BASE_URL = (
    "https://api.github.com/repos/NanashiTheNameless/NamelessNameSanitizerBot"
)
_GITHUB_LATEST_RELEASE_URL = f"{_GITHUB_API_BASE_URL}/releases/latest"
_GITHUB_LATEST_WORKFLOW_FILE = "image-publish-latest.yml"
_GITHUB_TAG_WORKFLOW_FILE = "image-publish-tag.yml"


def _github_workflow_runs_url(workflow_file: str, **params: object) -> str:
    query = urllib.parse.urlencode(params)
    url = f"{_GITHUB_API_BASE_URL}/actions/workflows/{workflow_file}/runs"
    return f"{url}?{query}" if query else url


def _github_tag_ref_url(tag: str) -> str:
    return f"{_GITHUB_API_BASE_URL}/git/ref/tags/{urllib.parse.quote(tag, safe='')}"


def _github_tag_object_url(tag_sha: str) -> str:
    return f"{_GITHUB_API_BASE_URL}/git/tags/{urllib.parse.quote(tag_sha, safe='')}"


def _fetch_github_json_sync(url: str) -> Optional[object]:
    headers = {
        "User-Agent": "NamelessNameSanitizerBot/VersionCheck",
        "Accept": "application/vnd.github+json",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


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


def _fetch_latest_successful_workflow_sha_sync() -> Optional[str]:
    """Fetch the latest successful image-publish-latest.yml workflow SHA."""
    url = _github_workflow_runs_url(
        _GITHUB_LATEST_WORKFLOW_FILE,
        branch="main",
        status="completed",
        per_page=5,
    )
    payload = _fetch_github_json_sync(url)
    if isinstance(payload, dict):
        runs = payload.get("workflow_runs")
        if isinstance(runs, list):
            for run in runs:
                if not isinstance(run, dict):
                    continue
                if run.get("conclusion") != "success":
                    continue
                sha = run.get("head_sha")
                if isinstance(sha, str) and sha.strip():
                    return sha.strip()
    return None


def _fetch_latest_release_sync() -> Optional[str]:
    """Fetch latest release tag from GitHub."""
    url = _GITHUB_LATEST_RELEASE_URL
    payload = _fetch_github_json_sync(url)
    if isinstance(payload, dict):
        tag = payload.get("tag_name")
        if isinstance(tag, str) and tag.strip():
            return tag.strip()
    return None


def _fetch_tag_commit_sha_sync(tag: str) -> Optional[str]:
    payload = _fetch_github_json_sync(_github_tag_ref_url(tag))
    if not isinstance(payload, dict):
        return None

    git_object = payload.get("object")
    if not isinstance(git_object, dict):
        return None

    object_type = git_object.get("type")
    object_sha = git_object.get("sha")
    if not isinstance(object_sha, str) or not object_sha.strip():
        return None

    if object_type == "commit":
        return object_sha.strip()
    if object_type != "tag":
        return None

    tag_payload = _fetch_github_json_sync(_github_tag_object_url(object_sha.strip()))
    if not isinstance(tag_payload, dict):
        return None

    tagged_object = tag_payload.get("object")
    if not isinstance(tagged_object, dict):
        return None

    tagged_object_type = tagged_object.get("type")
    tagged_object_sha = tagged_object.get("sha")
    if tagged_object_type != "commit":
        return None
    if not isinstance(tagged_object_sha, str) or not tagged_object_sha.strip():
        return None
    return tagged_object_sha.strip()


def _verify_release_build_success_sync(tag: str) -> bool:
    """Verify that a release tag has a successful workflow build.

    Checks if the tag has a corresponding successful image-publish-tag.yml workflow run.
    """
    tag_commit_sha = _fetch_tag_commit_sha_sync(tag)
    url = _github_workflow_runs_url(
        _GITHUB_TAG_WORKFLOW_FILE,
        status="completed",
        per_page=50,
    )
    payload = _fetch_github_json_sync(url)
    if isinstance(payload, dict):
        runs = payload.get("workflow_runs")
        if isinstance(runs, list):
            for run in runs:
                if not isinstance(run, dict):
                    continue
                if run.get("conclusion") != "success":
                    continue

                head_branch = run.get("head_branch")
                if head_branch == tag:
                    return True

                # workflow_dispatch runs can still be tied back to a release tag
                # when the workflow itself runs against that tag's commit.
                head_sha = run.get("head_sha")
                if tag_commit_sha and head_sha == tag_commit_sha:
                    return True
    return False


def _is_release_tag(tag: str) -> bool:
    parsed = _parse_semver(tag)
    return parsed is not None


async def check_outdated() -> tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """Return (is_outdated, current, latest, error_message).

    Uses GitHub API for efficiency (single API calls per check).
    Release tags (e.g., v0.1.2.3) query /releases/latest.
    Latest/dev builds use the last successful image-publish-latest.yml run.

    DEVELOPMENT is always considered outdated.
    """
    if _env_truthy(os.getenv("NNSB_VERSION_CHECK", "1")) is False:
        return False, _get_current_version(), None, "version check disabled"

    current_git = _get_current_git_sha()
    current_version = _get_current_version()

    # DEVELOPMENT is always outdated
    if current_version and current_version.upper() == "DEVELOPMENT":
        try:
            latest_git = await asyncio.to_thread(
                _fetch_latest_successful_workflow_sha_sync
            )
            latest_git = latest_git.strip() if latest_git else None
        except Exception:
            latest_git = None
        return True, current_version, latest_git or "unknown", None

    # If current_version is "latest" or we have a git SHA, use SHA-based comparison
    # This takes priority over release tag checking
    if current_version and current_version.lower() == "latest":
        if current_git and current_git.upper() != "DEVELOPMENT":
            try:
                latest_git = await asyncio.to_thread(
                    _fetch_latest_successful_workflow_sha_sync
                )
            except Exception as e:
                return False, current_git, None, f"failed to fetch latest git sha: {e}"
            latest_git = latest_git.strip() if latest_git else None
            if not latest_git:
                return False, current_git, None, "latest workflow build not available"
            if _same_version(current_git, latest_git):
                return False, current_git, latest_git, None
            # allow short vs full sha
            if current_git.startswith(latest_git) or latest_git.startswith(current_git):
                return False, current_git, latest_git, None
            return True, current_git, latest_git, None

    # If we have a release tag (e.g., v1.2.3), only check against latest release
    if current_version and _is_release_tag(current_version):
        try:
            latest_tag = await asyncio.to_thread(_fetch_latest_release_sync)
        except Exception:
            return False, current_version, None, "failed to fetch latest release"
        if not latest_tag:
            return False, current_version, None, "latest release unknown"
        if not _is_release_tag(latest_tag):
            return False, current_version, latest_tag, "latest release incomplete"
        # Verify the latest release has a successful build before marking as outdated
        if not _same_version(current_version, latest_tag):
            try:
                build_success = await asyncio.to_thread(
                    _verify_release_build_success_sync, latest_tag
                )
            except Exception:
                return (
                    False,
                    current_version,
                    latest_tag,
                    "failed to verify latest release build status",
                )
            if not build_success:
                return (
                    False,
                    current_version,
                    latest_tag,
                    "latest release build not completed successfully",
                )
            return True, current_version, latest_tag, None
        return False, current_version, latest_tag, None

    # For latest/dev builds, compare git SHA
    if current_git:
        try:
            latest_git = await asyncio.to_thread(
                _fetch_latest_successful_workflow_sha_sync
            )
        except Exception as e:
            return False, current_git, None, f"failed to fetch latest git sha: {e}"
        latest_git = latest_git.strip() if latest_git else None
        if not latest_git:
            return False, current_git, None, "latest workflow build not available"
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
