"""Tests for layout.py — pixel allocation invariants and font fit."""

import pytest

from layout import (
    PANEL_H,
    PANEL_W,
    REGIONS,
    assert_fonts_fit,
    load_fonts,
)


def test_panel_dimensions_match_hardware():
    assert PANEL_W == 250
    assert PANEL_H == 122


def test_regions_cover_panel_exactly():
    """Every device pixel row 0..121 should be accounted for, with no
    overlaps and no gaps beyond the explicit gap entries."""
    rows = []
    for name, (y, h) in REGIONS.items():
        for r in range(y, y + h):
            rows.append((r, name))
    assert len(rows) == PANEL_H, f"regions sum to {len(rows)} rows, expected {PANEL_H}"
    rs = [r for r, _ in rows]
    assert sorted(rs) == list(range(PANEL_H)), "overlap or gap in REGIONS"


def test_load_fonts_returns_all_keys():
    fonts = load_fonts()
    assert {"pct", "label", "small"}.issubset(fonts.keys())


def test_assert_fonts_fit_passes_for_installed_fonts():
    # The whole point: if a font upgrade ships, this assertion catches
    # it at startup rather than during a silent rendering corruption.
    fonts = load_fonts()
    assert_fonts_fit(fonts)   # raises ValueError on overflow
