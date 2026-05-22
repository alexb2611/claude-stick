"""Tests for state.py — Snapshot, signature, badges, time formatters."""

import pytest

from state import (
    Badge,
    Snapshot,
    Status,
    badge_for_pct,
    format_duration,
    format_reset,
    snapshot_from_payload,
)


# ── format_duration ────────────────────────────────────────────────────

def test_format_duration_under_minute_is_empty():
    assert format_duration(0) == ""
    assert format_duration(59) == ""

def test_format_duration_minutes_only():
    assert format_duration(60) == "1m"
    assert format_duration(42 * 60) == "42m"
    assert format_duration(59 * 60 + 30) == "59m"

def test_format_duration_hours_minutes():
    assert format_duration(60 * 60) == "1h 0m"
    assert format_duration(3 * 3600 + 12 * 60) == "3h 12m"
    assert format_duration(23 * 3600 + 59 * 60) == "23h 59m"

def test_format_duration_days_hours():
    assert format_duration(86400) == "1d 0h"
    assert format_duration(4 * 86400 + 6 * 3600) == "4d 6h"

def test_format_duration_negative_is_empty():
    # We never want to show a negative countdown; if reset is in the
    # past treat it as "imminent" and show nothing.
    assert format_duration(-10) == ""


# ── format_reset ───────────────────────────────────────────────────────

def test_format_reset_same_day():
    # 2026-05-22 17:42 vs same date 09:00 — should be "17:42"
    now_epoch = 1748022000     # 2026-05-22 11:00 UTC (approx; the test
                               # is timezone-sensitive — see comment in
                               # format_reset itself)
    when_epoch = now_epoch + 5 * 3600  # ~16:00 same day
    out = format_reset(when_epoch, now_epoch)
    assert ":" in out and len(out) == 5      # "HH:MM"

def test_format_reset_other_day():
    now_epoch  = 1748022000             # arbitrary fixed time
    when_epoch = now_epoch + 4 * 86400  # 4 days from now
    out = format_reset(when_epoch, now_epoch)
    # Format: "Day HH:MM", e.g. "Mon 09:00"
    assert " " in out
    day, hhmm = out.split(" ")
    assert len(day) == 3
    assert ":" in hhmm and len(hhmm) == 5


# ── badge_for_pct ──────────────────────────────────────────────────────

def test_badge_thresholds():
    assert badge_for_pct(0.0)    == Badge.OK
    assert badge_for_pct(49.99)  == Badge.OK
    assert badge_for_pct(50.0)   == Badge.WARN
    assert badge_for_pct(79.99)  == Badge.WARN
    assert badge_for_pct(80.0)   == Badge.HIGH
    assert badge_for_pct(100.0)  == Badge.HIGH

def test_badge_none_is_ok():
    # No data yet → don't show alarm.
    assert badge_for_pct(None) == Badge.OK


# ── effective_status promotion ─────────────────────────────────────────

def test_effective_status_ok_within_stale_window():
    s = Snapshot(
        session_pct=42.0, session_reset_at=2000,
        weekly_pct=67.0, weekly_reset_at=10000,
        last_poll_at=1000, status=Status.OK, now=1100,
    )
    assert s.effective_status == Status.OK   # 100 s elapsed, < 180

def test_effective_status_ok_promotes_to_stale_after_window():
    s = Snapshot(
        session_pct=42.0, session_reset_at=2000,
        weekly_pct=67.0, weekly_reset_at=10000,
        last_poll_at=1000, status=Status.OK, now=1300,
    )
    assert s.effective_status == Status.STALE  # 300 s elapsed, > 180

def test_effective_status_auth_is_sticky():
    # Daemon-flagged statuses don't auto-promote.
    s = Snapshot(
        session_pct=42.0, session_reset_at=2000,
        weekly_pct=67.0, weekly_reset_at=10000,
        last_poll_at=1000, status=Status.AUTH, now=99999,
    )
    assert s.effective_status == Status.AUTH

def test_effective_status_never_polled_stays_input():
    # last_poll_at=0 means "never". Don't promote — the status was
    # already set to NET by the first-run path.
    s = Snapshot(
        session_pct=None, session_reset_at=None,
        weekly_pct=None, weekly_reset_at=None,
        last_poll_at=0, status=Status.NET, now=99999,
    )
    assert s.effective_status == Status.NET


# ── visible_signature ──────────────────────────────────────────────────

def _baseline_snapshot(**overrides):
    fields = dict(
        session_pct=42.3, session_reset_at=2000,
        weekly_pct=67.0, weekly_reset_at=10000,
        last_poll_at=1000, status=Status.OK, now=1100,
    )
    fields.update(overrides)
    return Snapshot(**fields)

def test_signature_stable_under_subinteger_changes():
    s1 = _baseline_snapshot(session_pct=42.3)
    s2 = _baseline_snapshot(session_pct=42.4)  # still rounds to 42%
    assert s1.visible_signature() == s2.visible_signature()

def test_signature_changes_on_integer_flip():
    s1 = _baseline_snapshot(session_pct=42.4)
    s2 = _baseline_snapshot(session_pct=42.6)  # rounds to 43%
    assert s1.visible_signature() != s2.visible_signature()

def test_signature_changes_on_status_change():
    s1 = _baseline_snapshot(status=Status.OK)
    s2 = _baseline_snapshot(status=Status.AUTH)
    assert s1.visible_signature() != s2.visible_signature()

def test_signature_changes_when_status_auto_promotes():
    s1 = _baseline_snapshot(now=1100)   # effective = OK
    s2 = _baseline_snapshot(now=1300)   # effective = STALE
    assert s1.visible_signature() != s2.visible_signature()


# ── snapshot_from_payload ──────────────────────────────────────────────

def test_snapshot_from_payload_full():
    payload = {"s": 42.3, "sr": 2000, "w": 67.0, "wr": 10000,
               "st": "ok", "ts": 1000}
    s = snapshot_from_payload(payload, now=1100)
    assert s.session_pct == 42.3
    assert s.weekly_pct == 67.0
    assert s.status == Status.OK
    assert s.last_poll_at == 1000

def test_snapshot_from_payload_missing_buckets():
    payload = {"s": None, "sr": None, "w": 8.0, "wr": 10000,
               "st": "ok", "ts": 1000}
    s = snapshot_from_payload(payload, now=1100)
    assert s.session_pct is None
    assert s.weekly_pct == 8.0

def test_snapshot_from_payload_status_words():
    for word, expected in [("ok", Status.OK), ("stale", Status.STALE),
                           ("rate", Status.RATE), ("auth", Status.AUTH),
                           ("net", Status.NET)]:
        payload = {"s": 42.0, "sr": 2000, "w": 67.0, "wr": 10000,
                   "st": word, "ts": 1000}
        s = snapshot_from_payload(payload, now=1100)
        assert s.status == expected, f"{word} → {expected}"

def test_snapshot_from_payload_unknown_status_defaults_to_net():
    payload = {"s": 42.0, "sr": 2000, "w": 67.0, "wr": 10000,
               "st": "wat", "ts": 1000}
    s = snapshot_from_payload(payload, now=1100)
    assert s.status == Status.NET

def test_snapshot_from_payload_null_ts_does_not_crash():
    # Defensive: an explicit null `ts` must not raise TypeError.
    payload = {"s": 42.0, "sr": 2000, "w": 67.0, "wr": 10000,
               "st": "ok", "ts": None}
    s = snapshot_from_payload(payload, now=1100)
    assert s.last_poll_at == 0


# ── First-run text rendering ───────────────────────────────────────────

def test_first_run_snapshot_renders_dashes():
    s = Snapshot(
        session_pct=None, session_reset_at=None,
        weekly_pct=None, weekly_reset_at=None,
        last_poll_at=0, status=Status.NET, now=1000,
    )
    assert s.session_pct_text == "--"
    assert s.weekly_pct_text  == "--"
    assert s.session_countdown_text == ""
    assert s.session_reset_text == "polling..."
    assert s.status_text == "net"
