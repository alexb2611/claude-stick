#!/usr/bin/env python3
"""
claude-stick-pi: single-process Inky pHAT service.

Loops every POLL_INTERVAL_SEC seconds:
  1. poller.poll_usage()        → payload dict
  2. state.snapshot_from_payload → Snapshot
  3. should_redraw(...)         → bool
  4. renderer.render()/push()   if redraw
"""

import asyncio
import fcntl
import logging
import os
import signal
import sys
import time
from typing import Optional

import httpx

import config
import poller
import renderer
import state


log = logging.getLogger("claude-stick-pi")


# ── Redraw decision (pure, testable) ───────────────────────────────────

def should_redraw(*, last_redraw_at: Optional[int],
                  last_signature: Optional[tuple],
                  signature: tuple,
                  now: int) -> bool:
    if last_redraw_at is None:
        return True                                 # first run
    age = now - last_redraw_at
    if signature == last_signature:
        return age >= config.MAX_INK_AGE_SEC        # keep-alive ceiling
    return age >= config.MIN_REFRESH_INTERVAL_SEC   # floor on changes


# ── Single-instance lock ───────────────────────────────────────────────

def acquire_lock() -> Optional[int]:
    """flock the config dir's lock file. Returns fd on success, None if held."""
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(config.LOCK_FILE, os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError as e:
        log.error("could not open lock file %s: %s", config.LOCK_FILE, e)
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
    try:
        return int(config.LOCK_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


# ── Display detection ──────────────────────────────────────────────────

def _detect_display():
    """Auto-detect the connected Inky panel. Hardware-only — import is lazy
    so unit tests don't need inky installed."""
    from inky.auto import auto
    return auto()


# ── Service ────────────────────────────────────────────────────────────

class Service:
    def __init__(self, session_key: str, org_uuid: str, display):
        self.session_key = session_key
        self.org_uuid    = org_uuid
        self.display     = display
        self.stop_event  = asyncio.Event()
        self.last_redraw_at: Optional[int] = None
        self.last_signature: Optional[tuple] = None

    async def run(self) -> None:
        async with httpx.AsyncClient() as client:
            while not self.stop_event.is_set():
                await self._cycle(client)
                try:
                    await asyncio.wait_for(self.stop_event.wait(),
                                           timeout=config.POLL_INTERVAL_SEC)
                except asyncio.TimeoutError:
                    pass

    async def _cycle(self, client: httpx.AsyncClient) -> None:
        now = int(time.time())
        payload = await poller.poll_usage(
            session_key=self.session_key, org_uuid=self.org_uuid,
            client=client, now=now,
        )
        snapshot = state.snapshot_from_payload(payload, now=now)
        log.info("poll: s=%s w=%s st=%s",
                 snapshot.session_pct_text, snapshot.weekly_pct_text,
                 snapshot.status_text)

        sig = snapshot.visible_signature()
        if not should_redraw(last_redraw_at=self.last_redraw_at,
                             last_signature=self.last_signature,
                             signature=sig, now=now):
            log.debug("redraw skipped: signature %s, age %s",
                      "unchanged" if sig == self.last_signature else "changed",
                      None if self.last_redraw_at is None
                              else now - self.last_redraw_at)
            return

        was_change = sig != self.last_signature

        try:
            img = renderer.render(snapshot)
            renderer.push(img, self.display)
        except Exception:
            log.exception("render/push failed; will retry next cycle")
            return

        self.last_redraw_at = now
        self.last_signature = sig
        log.info("redrew (signature %s)",
                 "changed" if was_change else "keep-alive")

    def stop(self) -> None:
        log.info("stopping…")
        self.stop_event.set()


# ── Entry point ────────────────────────────────────────────────────────

async def main() -> int:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    lock_fd = acquire_lock()
    if lock_fd is None:
        holder = read_lock_holder_pid()
        if holder is not None:
            log.error("another claude-stick service is running (PID %d). "
                      "systemctl --user stop claude-stick-pi", holder)
        else:
            log.error("could not acquire lock at %s", config.LOCK_FILE)
        return 1

    try:
        try:
            session_key, org_uuid = poller.load_session_config()
        except (FileNotFoundError, ValueError) as e:
            log.error("%s", e)
            return 1

        try:
            display = _detect_display()
        except Exception:
            log.exception("could not detect Inky display; check SPI and venv")
            return 1

        log.info("starting; org %s…, poll every %ds",
                 org_uuid[:8], config.POLL_INTERVAL_SEC)

        service = Service(session_key, org_uuid, display)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, service.stop)

        await service.run()
        log.info("exited cleanly")
        return 0
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
