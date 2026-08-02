"""Tests for renderer.py — dimensions, palette mode, and golden PNGs."""

import json
from pathlib import Path

import pytest
from PIL import Image

from layout import PANEL_H, PANEL_W
from renderer import push, render
from state import snapshot_from_payload


FIXTURES_DIR = Path(__file__).parent.parent / "tools" / "fixtures"
GOLDEN_DIR   = Path(__file__).parent / "golden"


def _load_fixture_snapshot(name: str):
    data = json.loads((FIXTURES_DIR / f"{name}.json").read_text())
    return snapshot_from_payload(data["payload"], now=data["now"])


def test_render_returns_correct_dimensions():
    s = _load_fixture_snapshot("default")
    img = render(s)
    assert img.size == (PANEL_W, PANEL_H)

def test_render_returns_palette_mode():
    s = _load_fixture_snapshot("default")
    img = render(s)
    assert img.mode == "P"


def test_render_palette_matches_jd79661_order():
    """Exactly four pure colours, in the driver's native index order.
    InkyJD79661.set_image only skips dithering for 4-colour P images,
    and quantizes against black/white/yellow/red — so both the count
    and the order are load-bearing."""
    s = _load_fixture_snapshot("default")
    img = render(s)
    assert img.palette.colors == {
        (0, 0, 0):       0,   # black
        (255, 255, 255): 1,   # white
        (255, 255, 0):   2,   # yellow
        (255, 0, 0):     3,   # red
    }


@pytest.mark.parametrize("fixture_name", [
    "default", "first_run", "auth_failed", "high_session", "weekly_just_reset",
])
def test_render_matches_golden(fixture_name):
    s = _load_fixture_snapshot(fixture_name)
    img = render(s)
    expected = Image.open(GOLDEN_DIR / f"{fixture_name}.png")
    assert list(img.getdata()) == list(expected.getdata()), (
        f"render diverged from {fixture_name}.png — if intentional, "
        f"regenerate via: python tools/preview.py --update-golden"
    )


class FakeDisplay:
    """Stand-in for inky.InkyPHAT in unit tests — captures the writes."""
    def __init__(self):
        self.set_image_called_with = None
        self.show_called = False

    def set_image(self, image):
        self.set_image_called_with = image

    def show(self):
        self.show_called = True


def test_push_calls_set_image_then_show():
    s = _load_fixture_snapshot("default")
    img = render(s)
    fake = FakeDisplay()
    push(img, fake)
    assert fake.set_image_called_with is img
    assert fake.show_called is True
