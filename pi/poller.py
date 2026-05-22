"""
claude.ai polling — fetches the usage panel data and translates it to
the same payload dict the M5StickC firmware consumes.

Ported from daemon/claude_stick_daemon.py with BLE-related code stripped.
The public entry points are poll_usage() (async, takes an httpx client)
and load_session_config() (sync, reads ~/.config/claude-stick/session.json).
"""

import json
import logging
from datetime import datetime
from typing import Optional

import httpx

from config import (
    CLIENT_HEADERS,
    HTTP_TIMEOUT_SEC,
    SESSION_CFG,
    USAGE_URL_FMT,
)

log = logging.getLogger("claude-stick-pi.poller")


# ── Session config loading ─────────────────────────────────────────────

def load_session_config() -> tuple[str, str]:
    """
    Read session_key and org_uuid from the JSON config file. Raises
    FileNotFoundError if absent, ValueError if malformed or incomplete.
    """
    if not SESSION_CFG.exists():
        raise FileNotFoundError(
            f"Session config not found at {SESSION_CFG}.\n"
            f"Create it as JSON with two values from your browser:\n"
            f"  {{\n"
            f'    "session_key": "sk-ant-sid02-...",\n'
            f'    "org_uuid":    "..."\n'
            f"  }}\n"
            f"See daemon/README.md for how to extract these from DevTools."
        )
    try:
        data = json.loads(SESSION_CFG.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Session config is not valid JSON: {e}") from e

    key = data.get("session_key")
    org = data.get("org_uuid")
    if not key or not org:
        raise ValueError(
            f"Session config missing required fields. "
            f"Expected session_key and org_uuid in {SESSION_CFG}."
        )
    return key, org


# ── Timestamp parsing ──────────────────────────────────────────────────

def parse_epoch(value: Optional[str]) -> Optional[int]:
    """ISO-8601 (with 'Z' or offset) OR numeric-string epoch → int epoch."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.replace(".", "", 1).lstrip("-").isdigit():
        try:
            return int(float(s))
        except ValueError:
            return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except ValueError:
        log.warning("unparseable timestamp: %r", value)
        return None


# ── Payload construction ───────────────────────────────────────────────

def build_payload(*, now: int, usage: Optional[dict] = None,
                  status: str = "ok") -> dict:
    """
    Map a claude.ai usage dict (or None on failure) into the payload
    schema the firmware speaks. Values that fail range validation are
    nulled rather than poisoning the snapshot.
    """
    usage = usage or {}

    def _pct(name: str) -> Optional[float]:
        b = usage.get(name)
        if not isinstance(b, dict):
            return None
        v = b.get("utilization")
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        # firmware/src/payload.cpp:72 — same range, same rejection.
        if f < 0.0 or f > 100.0:
            log.warning("utilization out of range for %s: %r", name, v)
            return None
        return f

    def _reset(name: str) -> Optional[int]:
        b = usage.get(name)
        if not isinstance(b, dict):
            return None
        return parse_epoch(b.get("resets_at"))

    return {
        "s":  _pct("five_hour"),
        "sr": _reset("five_hour"),
        "w":  _pct("seven_day"),
        "wr": _reset("seven_day"),
        "st": status,
        "ts": now,
    }


# ── HTTP poll ──────────────────────────────────────────────────────────

async def poll_usage(*, session_key: str, org_uuid: str,
                     client: httpx.AsyncClient, now: int) -> dict:
    """
    Single poll cycle. Always returns a valid payload dict — failures
    are encoded into the "st" field, never raised. The caller owns the
    httpx client so it can be reused across cycles.
    """
    url = USAGE_URL_FMT.format(org=org_uuid)

    try:
        response = await client.get(
            url,
            # TODO(task-6): httpx ≥0.27 deprecates per-request cookies; the
            # orchestrator should construct the AsyncClient with this cookie
            # already attached and remove the cookies= kwarg here.
            cookies={"sessionKey": session_key},
            headers=CLIENT_HEADERS,
            timeout=HTTP_TIMEOUT_SEC,
        )
    except httpx.RequestError as e:
        log.warning("network error calling usage endpoint: %s", e)
        return build_payload(now=now, status="net")

    if response.status_code in (401, 403):
        log.error(
            "session key rejected (%d). Refresh sessionKey via DevTools "
            "on claude.ai/settings/usage and update %s, then restart.",
            response.status_code, SESSION_CFG,
        )
        return build_payload(now=now, status="auth")

    if response.status_code == 429:
        log.warning("rate limited by claude.ai (429)")
        return build_payload(now=now, status="rate")

    if response.status_code >= 400:
        log.warning("usage endpoint returned %d: %s",
                    response.status_code, response.text[:200])
        return build_payload(now=now, status="net")

    try:
        usage = response.json()
    except json.JSONDecodeError:
        log.warning("usage response was not JSON (Cloudflare challenge?): %s",
                    response.text[:200])
        return build_payload(now=now, status="net")

    return build_payload(now=now, usage=usage, status="ok")
