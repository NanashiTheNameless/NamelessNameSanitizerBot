# SPDX-License-Identifier: LicenseRef-OQL-1.3
"""Simple, opt-out, privacy-respecting census for self-hosted instances.

Design goals:
- Opt-out by default; disable with env var.
- Minimal data: hashed instance id, coarse UTC date, python version/platform, optional project version, project name.
- Non-blocking and fail-silent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Optional

_DEFAULT_ENDPOINT = "https://telemetry.namelessnanashi.dev/census"
_HAS_SCHEDULED_SEND = False
_DAILY_INTERVAL = 24 * 3600


def _env_truthy(v: Optional[str]) -> bool:
    s = (v or "").strip().lower()
    return s in ("1", "true", "yes", "on", "y", "t")


def _env_falsy(v: Optional[str]) -> bool:
    s = (v or "").strip().lower()
    return s in ("0", "false", "no", "off", "n", "f")


def _env_opt_out() -> bool:
    if _env_truthy(os.getenv("NNSB_TELEMETRY_OPTOUT")) or _env_truthy(
        os.getenv("TELEMETRY_OPTOUT")
    ):
        return True
    tel = os.getenv("TELEMETRY")
    if tel is not None and _env_falsy(tel):
        return True
    return False


def _get_endpoint() -> str:
    return os.getenv("TELEMETRY_ENDPOINT") or _DEFAULT_ENDPOINT


def _get_project_name() -> str:
    name = os.getenv("PROJECT_NAME")
    if name:
        return name
    # Fall back to repository directory name
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.basename(repo_root) or "NamelessNameSanitizerBot"


def _get_state_file() -> str:
    # Allow overriding the state file path for containerized deployments
    env_file = os.getenv("TELEMETRY_STATE_FILE")
    if env_file:
        return env_file
    env_dir = os.getenv("TELEMETRY_STATE_DIR")
    if env_dir:
        return os.path.join(env_dir, ".telemetry_id")
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".telemetry_id")
    )


def _ensure_instance_id() -> str:
    path = _get_state_file()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
                if raw:
                    return raw
        new = str(uuid.uuid4())
        # Ensure parent directory exists when using a custom path/dir
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        except Exception:
            pass
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        return new
    except Exception:
        return str(uuid.uuid4())


def _hash_id(raw: str) -> str:
    h = hashlib.sha256()
    h.update(raw.encode("utf-8"))
    return h.hexdigest()


def _get_version() -> Optional[str]:
    try:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        vf = os.path.join(repo_root, "VERSION")
        if os.path.exists(vf):
            with open(vf, "r", encoding="utf-8") as fh:
                return fh.read().strip()
    except Exception:
        pass
    return None


def _make_payload() -> dict:
    rid = _ensure_instance_id()
    payload = {
        "id": _hash_id(rid),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "python": platform.python_version(),
        "platform": platform.system(),
        "project_version": _get_version(),
        "projectname": _get_project_name(),
        "project": _get_project_name(),
        "count": 1,
    }
    return payload


def _post_sync(url: str, data: bytes, timeout: float = 2.0) -> None:
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            _ = resp.status
    except urllib.error.URLError:
        return
    except Exception:
        return


async def maybe_send_telemetry_async() -> None:
    endpoint = _get_endpoint()
    if not endpoint or _env_opt_out():
        return
    data = json.dumps(_make_payload(), separators=(",", ":")).encode("utf-8")
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _post_sync, endpoint, data)
    except Exception:
        return


async def _daily_ping_loop() -> None:
    """Background loop that sends telemetry every 24 hours.

    Runs until the process exits or the event loop stops. Exceptions are
    swallowed to keep it fail-silent.
    """
    while True:
        try:
            await asyncio.sleep(_DAILY_INTERVAL)
            if _env_opt_out():
                continue
            await maybe_send_telemetry_async()
        except asyncio.CancelledError:
            return
        except Exception:
            # Swallow errors and continue the loop
            try:
                await asyncio.sleep(60)
            except Exception:
                # If even sleeping fails, bail out to avoid a tight loop
                return


def maybe_send_telemetry_background() -> None:
    global _HAS_SCHEDULED_SEND
    if _HAS_SCHEDULED_SEND:
        return
    _HAS_SCHEDULED_SEND = True
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Fire-and-forget immediate send
            asyncio.ensure_future(maybe_send_telemetry_async())
            # Also schedule a daily background ping (once per process)
            try:
                asyncio.ensure_future(_daily_ping_loop())
            except Exception:
                # If scheduling fails, don't prevent the immediate send
                pass
    except Exception:
        try:
            _post_sync(_get_endpoint(), json.dumps(_make_payload()).encode("utf-8"))
        except Exception:
            pass


__all__ = ("maybe_send_telemetry_background", "maybe_send_telemetry_async")
