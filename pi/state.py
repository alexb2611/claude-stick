"""
Application state — Snapshot dataclass and the pure helpers it depends
on. No I/O, no Pillow, no inky imports here.

The Snapshot is constructed by claude_stick_pi.py after each successful
(or failed) poll, then handed to renderer.py. Its visible_signature()
method drives the "should we redraw" decision.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

import config


# ── Enums ──────────────────────────────────────────────────────────────

class Status(enum.Enum):
    OK    = "ok"
    STALE = "stale"
    RATE  = "rate"
    AUTH  = "auth"
    NET   = "net"


class Badge(enum.Enum):
    OK   = "ok"
    WARN = "warn"
    HIGH = "high"


# ── Pure helpers ───────────────────────────────────────────────────────

_POLLING_TEXT = "polling..."


def format_duration(seconds: int) -> str:
    """
    "42m" / "3h 12m" / "4d 6h" for positive seconds at least a minute.
    Empty string for under a minute or negative input — e-paper can't
    usefully show seconds-precision countdowns, and a negative value
    means the reset has passed.
    """
    if seconds < 60:
        return ""
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def format_reset(when_epoch: int, now_epoch: int) -> str:
    """
    "HH:MM" if the reset is later today (local time), "Day HH:MM"
    otherwise. Uses the system timezone — the spec assumes the Pi is
    configured to local time, which the OS handles.
    """
    when = datetime.fromtimestamp(when_epoch).astimezone()
    now  = datetime.fromtimestamp(now_epoch).astimezone()
    if when.date() == now.date():
        return when.strftime("%H:%M")
    return f"{_DOW[when.weekday()]} {when.strftime('%H:%M')}"


def badge_for_pct(pct: float | None) -> Badge:
    if pct is None:
        return Badge.OK
    if pct >= config.BADGE_HIGH_PCT:
        return Badge.HIGH
    if pct >= config.BADGE_WARN_PCT:
        return Badge.WARN
    return Badge.OK


# ── Snapshot ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Snapshot:
    session_pct:      float | None
    session_reset_at: int   | None
    weekly_pct:       float | None
    weekly_reset_at:  int   | None
    last_poll_at:     int          # 0 = never polled successfully
    status:           Status       # daemon-reported (pre-promotion)
    now:              int          # epoch seconds at snapshot build time

    # ── Effective status (with stale-promotion) ───────────────────────
    @property
    def effective_status(self) -> Status:
        if (self.status == Status.OK
                and self.last_poll_at > 0
                and (self.now - self.last_poll_at) > config.STALE_AFTER_SEC):
            return Status.STALE
        return self.status

    # ── Per-bar derivations ───────────────────────────────────────────
    @property
    def session_badge(self) -> Badge:
        return badge_for_pct(self.session_pct)

    @property
    def weekly_badge(self) -> Badge:
        return badge_for_pct(self.weekly_pct)

    @property
    def session_pct_text(self) -> str:
        return "--" if self.session_pct is None else f"{int(self.session_pct + 0.5)}%"

    @property
    def weekly_pct_text(self) -> str:
        return "--" if self.weekly_pct is None else f"{int(self.weekly_pct + 0.5)}%"

    @property
    def session_pct_int(self) -> int:
        # For bar fill width and signature stability. -1 sentinel when
        # no data yet so the signature is distinct from "0%".
        return -1 if self.session_pct is None else int(self.session_pct + 0.5)

    @property
    def weekly_pct_int(self) -> int:
        return -1 if self.weekly_pct is None else int(self.weekly_pct + 0.5)

    @property
    def session_countdown_text(self) -> str:
        if self.session_reset_at is None:
            return ""
        return format_duration(self.session_reset_at - self.now)

    @property
    def weekly_countdown_text(self) -> str:
        if self.weekly_reset_at is None:
            return ""
        return format_duration(self.weekly_reset_at - self.now)

    @property
    def session_reset_text(self) -> str:
        if self.session_reset_at is None:
            return _POLLING_TEXT
        when = format_reset(self.session_reset_at, self.now)
        dur  = self.session_countdown_text
        return f"resets {when} · {dur}" if dur else f"resets {when}"

    @property
    def weekly_reset_text(self) -> str:
        if self.weekly_reset_at is None:
            return _POLLING_TEXT
        when = format_reset(self.weekly_reset_at, self.now)
        dur  = self.weekly_countdown_text
        return f"resets {when} · {dur}" if dur else f"resets {when}"

    @property
    def status_text(self) -> str:
        return self.effective_status.value

    # ── Visible signature ─────────────────────────────────────────────
    def visible_signature(self) -> tuple:
        """
        Hashable tuple of every renderer-visible value. If two snapshots
        share a signature, the resulting frames will be byte-identical.
        """
        return (
            self.session_pct_int, self.session_badge,
            self.session_countdown_text, self.session_reset_text,
            self.weekly_pct_int,  self.weekly_badge,
            self.weekly_countdown_text,  self.weekly_reset_text,
            self.status_text,
        )


# ── Payload → Snapshot ─────────────────────────────────────────────────

_STATUS_BY_WORD = {s.value: s for s in Status}


def snapshot_from_payload(payload: dict, now: int) -> Snapshot:
    """
    Build a Snapshot from the daemon-shape payload dict that the poller
    returns. Status words not in the canonical set degrade to NET so an
    unrecognised value never silently looks like "ok".
    """
    return Snapshot(
        session_pct      = payload.get("s"),
        session_reset_at = payload.get("sr"),
        weekly_pct       = payload.get("w"),
        weekly_reset_at  = payload.get("wr"),
        last_poll_at     = int(payload.get("ts") or 0),
        status           = _STATUS_BY_WORD.get(payload.get("st", "net"), Status.NET),
        now              = now,
    )
