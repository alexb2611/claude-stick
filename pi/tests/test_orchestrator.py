"""Tests for the redraw-decision logic in claude_stick_pi.py.

The asyncio loop itself is integration-test territory; we verify it on
the Pi via the hardware checklist. Pure decision logic lives in
`should_redraw()` and can be unit-tested in isolation.
"""

import pytest

from claude_stick_pi import should_redraw


def test_first_run_always_redraws():
    assert should_redraw(
        last_redraw_at=None, last_signature=None,
        signature=("a",), now=1000,
    ) is True


def test_same_signature_within_keepalive_skips():
    assert should_redraw(
        last_redraw_at=1000, last_signature=("a",),
        signature=("a",), now=1500,    # 500s elapsed, < MAX_INK_AGE (3600)
    ) is False


def test_same_signature_past_keepalive_redraws():
    assert should_redraw(
        last_redraw_at=1000, last_signature=("a",),
        signature=("a",), now=5000,    # 4000s elapsed, > 3600
    ) is True


def test_different_signature_within_floor_skips():
    assert should_redraw(
        last_redraw_at=1000, last_signature=("a",),
        signature=("b",), now=1100,    # 100s elapsed, < MIN_REFRESH_INTERVAL (300)
    ) is False


def test_different_signature_past_floor_redraws():
    assert should_redraw(
        last_redraw_at=1000, last_signature=("a",),
        signature=("b",), now=1400,    # 400s elapsed, > 300
    ) is True
