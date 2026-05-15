#!/usr/bin/env python3
"""
claude-stick daemon: polls claude.ai for the subscription usage panel
and pushes the result to the M5StickC Plus over BLE.

Data source: the same endpoint Claude Desktop's Settings → Usage page
hits (https://claude.ai/api/organizations/{org}/usage). Authenticated
via the sessionKey cookie that claude.ai sets on login. This is a
separate auth system from Claude Code's OAuth token; the two don't
interchange.

Reads sessionKey and orgUuid from a local config file (see README for
how to extract them via DevTools). Polls every 60 seconds by default,
also responds to the stick's 0x01 refresh request immediately on
(re)connect. Logs to stderr (journalctl captures it under systemd).
"""

import asyncio
import fcntl
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

# ─── Configuration ─────────────────────────────────────────────────────

DEVICE_NAME = "claude-stick"
SVC_UUID    = "8e7c1a32-5b4d-4c2e-9e3f-1a2b3c4d5e6f"
CHR_RX_UUID = "8e7c1a33-5b4d-4c2e-9e3f-1a2b3c4d5e6f"
CHR_TX_UUID = "8e7c1a34-5b4d-4c2e-9e3f-1a2b3c4d5e6f"

POLL_INTERVAL_SEC     = int(os.environ.get("POLL_INTERVAL_SEC", "60"))
SCAN_TIMEOUT_SEC      = int(os.environ.get("SCAN_TIMEOUT_SEC", "10"))
RECONNECT_BACKOFF_SEC = int(os.environ.get("RECONNECT_BACKOFF_SEC", "5"))

CONFIG_DIR  = Path.home() / ".config" / "claude-stick"
MAC_CACHE   = CONFIG_DIR / "ble-address"
LOCK_FILE   = CONFIG_DIR / "daemon.lock"
SESSION_CFG = Path(os.environ.get(
    "CLAUDE_STICK_CONFIG",
    str(CONFIG_DIR / "session.json"),
))

# Claude.ai backend endpoint, same one the Settings → Usage page uses.
# Authenticated via the sessionKey cookie that claude.ai sets on login.
USAGE_URL_FMT = "https://claude.ai/api/organizations/{org}/usage"

# Headers that identify us as the web app to the backend. The
# sessionKey cookie does the heavy lifting for auth; these mostly help
# the request look like a legitimate claude.ai call.
CLIENT_HEADERS = {
    "anthropic-client-platform": "web_claude_ai",
    "anthropic-client-version": "1.0.0",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# ─── Logging ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("claude-stick")

# ─── Single-instance lock ─────────────────────────────────────
# Prevents the very specific footgun of accidentally running a
# foreground daemon while a systemd-managed instance is still active,
# which fights over the single BLE connection slot and looks like a
# firmware bug.

def acquire_lock():
    """
    Try to claim an exclusive flock on the lock file. Returns the open
    file descriptor (must stay open for the lock to hold) or None if
    another instance is already running. The kernel releases the lock
    automatically when the process exits, however it exits.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK_FILE, os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError as e:
        log.error("could not open lock file %s: %s", LOCK_FILE, e)
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def read_lock_holder_pid() -> Optional[int]:
    """Return the PID stored in the lock file, if any."""
    try:
        with open(LOCK_FILE) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None

# ─── Session config loading ────────────────────────────────────────────

def load_session_config() -> tuple[str, str]:
    """Read sessionKey and orgUuid from the local config file."""
    if not SESSION_CFG.exists():
        raise FileNotFoundError(
            f"Session config not found at {SESSION_CFG}.\n"
            f"Create it as JSON with the two values from your browser:\n"
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

# ─── Timestamp parsing ─────────────────────────────────────────────────

def parse_epoch(value: Optional[str]) -> Optional[int]:
    """
    Convert either an ISO-8601 timestamp or epoch-seconds string to an
    integer epoch. Returns None on failure.
    """
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    if s.replace(".", "", 1).isdigit():
        try:
            return int(float(s))
        except ValueError:
            return None
    try:
        # Python 3.11+ handles "Z" suffix natively; older needs +00:00.
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except ValueError:
        log.warning("unparseable timestamp: %r", value)
        return None

# ─── Usage polling ─────────────────────────────────────────────────────

async def poll_usage(session_key: str, org_uuid: str) -> dict:
    """
    Fetch the subscription usage JSON from claude.ai and convert it
    into the payload shape the firmware expects.
    """
    now = int(time.time())
    url = USAGE_URL_FMT.format(org=org_uuid)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                url,
                cookies={"sessionKey": session_key},
                headers=CLIENT_HEADERS,
            )
    except httpx.RequestError as e:
        log.warning("network error calling usage endpoint: %s", e)
        return build_payload(now=now, status="net")

    if response.status_code in (401, 403):
        log.error(
            "session key rejected (%d): %s",
            response.status_code, response.text[:300],
        )
        log.error(
            "Refresh sessionKey via DevTools on claude.ai/settings/usage "
            "and update %s, then restart the daemon.", SESSION_CFG,
        )
        return build_payload(now=now, status="auth")

    if response.status_code == 429:
        log.warning("rate limited by claude.ai (429)")
        return build_payload(now=now, status="rate")

    if response.status_code >= 400:
        log.warning(
            "usage endpoint returned %d: %s",
            response.status_code, response.text[:200],
        )
        return build_payload(now=now, status="net")

    try:
        usage = response.json()
    except json.JSONDecodeError:
        log.warning(
            "usage response was not JSON (Cloudflare challenge?): %s",
            response.text[:200],
        )
        return build_payload(now=now, status="net")

    return build_payload(now=now, usage=usage, status="ok")


def build_payload(
    now: int,
    usage: Optional[dict] = None,
    status: str = "ok",
) -> dict:
    """Map the claude.ai usage JSON into the firmware's compact payload."""
    usage = usage or {}

    def bucket_pct(name: str) -> Optional[float]:
        b = usage.get(name)
        if not isinstance(b, dict):
            return None
        v = b.get("utilization")
        return float(v) if v is not None else None

    def bucket_reset(name: str) -> Optional[int]:
        b = usage.get(name)
        if not isinstance(b, dict):
            return None
        return parse_epoch(b.get("resets_at"))

    s_pct   = bucket_pct("five_hour")
    s_reset = bucket_reset("five_hour")
    w_pct   = bucket_pct("seven_day")
    w_reset = bucket_reset("seven_day")

    # If the bucket is missing entirely (auth fail, network blip), fall
    # back to zero with far-future resets so the firmware still gets
    # valid numeric fields; the status enum tells it not to trust them.
    return {
        "s":  s_pct   if s_pct   is not None else 0.0,
        "sr": s_reset if s_reset is not None else now + 5 * 3600,
        "w":  w_pct   if w_pct   is not None else 0.0,
        "wr": w_reset if w_reset is not None else now + 7 * 86400,
        "st": status,
        "ts": now,
    }

# ─── BLE daemon ────────────────────────────────────────────────────────

class StickDaemon:
    def __init__(self, session_key: str, org_uuid: str) -> None:
        self.session_key = session_key
        self.org_uuid = org_uuid
        self.client: Optional[BleakClient] = None
        self.refresh_event = asyncio.Event()
        self.stop_event = asyncio.Event()

    async def find_device(self) -> str:
        """
        Return the device MAC, having verified it's currently advertising.

        On Linux, BleakClient(mac).connect() asks BlueZ to reach the
        given address. BlueZ can only do that if it has seen the
        device's advertisement recently; otherwise the connect hangs
        until timeout. A short scan (by address if we have one cached,
        otherwise by name) refreshes BlueZ's view and confirms the
        stick is actually reachable before we commit to the connect.
        """
        cached_mac: Optional[str] = None
        if MAC_CACHE.exists():
            cached_mac = MAC_CACHE.read_text().strip() or None

        if cached_mac:
            log.info("verifying cached MAC %s is advertising...", cached_mac)
            device = await BleakScanner.find_device_by_address(
                cached_mac, timeout=SCAN_TIMEOUT_SEC
            )
            if device is not None:
                log.info("cached MAC verified")
                return cached_mac
            log.info("cached MAC not advertising, falling back to name scan")

        log.info("scanning for %s...", DEVICE_NAME)
        device = await BleakScanner.find_device_by_name(
            DEVICE_NAME, timeout=SCAN_TIMEOUT_SEC
        )
        if device is None:
            raise RuntimeError(f"device '{DEVICE_NAME}' not found in scan")

        log.info("found %s at %s", DEVICE_NAME, device.address)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        MAC_CACHE.write_text(device.address)
        return device.address

    def on_tx_notify(self, sender, data: bytearray) -> None:
        """TX characteristic notification handler (called by bleak)."""
        if len(data) >= 1 and data[0] == 0x01:
            log.info("device requested refresh")
            self.refresh_event.set()

    async def session(self) -> None:
        """One BLE session: connect, subscribe, poll, write, until lost."""
        mac = await self.find_device()

        log.info("connecting to %s...", mac)
        async with BleakClient(mac, timeout=15.0) as client:
            self.client = client
            log.info("connected")

            await client.start_notify(CHR_TX_UUID, self.on_tx_notify)

            # The device fires its 0x01 refresh on subscribe, which sets
            # refresh_event. Clear it after the explicit initial poll so
            # we don't double-poll on first connect.
            await self.poll_and_send()
            self.refresh_event.clear()

            while client.is_connected and not self.stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self.refresh_event.wait(),
                        timeout=POLL_INTERVAL_SEC,
                    )
                    self.refresh_event.clear()
                    log.debug("polling early (refresh request)")
                except asyncio.TimeoutError:
                    log.debug("polling on interval")

                if not client.is_connected:
                    break

                await self.poll_and_send()

        log.info("disconnected")
        self.client = None

    async def poll_and_send(self) -> None:
        if self.client is None or not self.client.is_connected:
            return

        payload = await poll_usage(self.session_key, self.org_uuid)
        data = json.dumps(payload, separators=(",", ":")).encode()

        log.debug("sending: %s", data.decode())
        try:
            await self.client.write_gatt_char(
                CHR_RX_UUID, data, response=False
            )
            log.info(
                "sent s=%.1f%% w=%.1f%% (st=%s)",
                payload["s"], payload["w"], payload["st"],
            )
        except BleakError as e:
            log.warning("BLE write failed: %s", e)

    async def run(self) -> None:
        """Top-level loop with reconnect-on-failure."""
        while not self.stop_event.is_set():
            try:
                await self.session()
            except (BleakError, RuntimeError, OSError, asyncio.TimeoutError) as e:
                log.warning("session ended: %s", e)
                if MAC_CACHE.exists():
                    try:
                        MAC_CACHE.unlink()
                    except OSError:
                        pass

            if self.stop_event.is_set():
                break

            log.info("reconnecting in %ds...", RECONNECT_BACKOFF_SEC)
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=RECONNECT_BACKOFF_SEC,
                )
                break
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        log.info("stopping...")
        self.stop_event.set()

# ─── Entry point ───────────────────────────────────────────────────────

async def main() -> int:
    lock_fd = acquire_lock()
    if lock_fd is None:
        holder = read_lock_holder_pid()
        if holder is not None:
            log.error(
                "another claude-stick daemon is already running (PID %d). "
                "Stop it before starting a new one.", holder
            )
            log.error(
                "If it's the systemd unit:  systemctl --user stop claude-stick-daemon"
            )
            log.error(
                "If it's a stale process:   kill %d", holder
            )
        else:
            log.error("could not acquire daemon lock at %s", LOCK_FILE)
        return 1

    try:
        session_key, org_uuid = load_session_config()
    except (FileNotFoundError, ValueError) as e:
        log.error("%s", e)
        return 1

    log.info(
        "starting; org %s..., session %s..., poll interval %ds",
        org_uuid[:8], session_key[:13], POLL_INTERVAL_SEC,
    )

    daemon = StickDaemon(session_key, org_uuid)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, daemon.stop)

    await daemon.run()
    log.info("exited cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
