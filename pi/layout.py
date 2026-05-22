"""
Pixel coordinates, font sizes, and startup invariants for the 250×122
Inky pHAT panel. Spec §6.1 / §6.2.

REGIONS is the source of truth: it lists every region's (y_start, height)
in half-open form (`y_start..y_start+height-1` inclusive of both ends).
A startup test verifies the regions tile rows 0..121 exactly.
"""

from __future__ import annotations

from PIL import ImageFont

import font_hanken_grotesk as _fhg


# ── Panel ──────────────────────────────────────────────────────────────

PANEL_W = 250
PANEL_H = 122

# ── Region table (y_start, height); see spec §6.1 ──────────────────────
# Every row 0..121 is covered exactly once.

REGIONS: dict[str, tuple[int, int]] = {
    "top_pad":         (0,   2),
    "session_row":     (2,   24),
    "gap_a":           (26,  2),
    "session_bar":     (28,  12),
    "gap_b":           (40,  2),
    "session_reset":   (42,  12),
    "gap_c":           (54,  2),
    "weekly_row":      (56,  24),
    "gap_d":           (80,  2),
    "weekly_bar":      (82,  12),
    "gap_e":           (94,  2),
    "weekly_reset":    (96,  12),
    "gap_f":           (108, 2),
    "status_footer":   (110, 12),
}

# ── Horizontal layout ──────────────────────────────────────────────────

MARGIN_X       = 6           # left and right
BADGE_X        = 170         # badge box top-left x
BADGE_SIZE     = 13          # 13×13 box
PCT_RIGHT_X    = PANEL_W - MARGIN_X    # right edge of right-aligned %

# ── Bar geometry ───────────────────────────────────────────────────────

BAR_X      = MARGIN_X    # 6 — bar starts at the left margin
BAR_W      = 234         # spec §6.1 — 4 px shorter than the full inter-margin
                         # width (250-12=238) so the bar doesn't visually
                         # collide with the right-aligned percentage column
BAR_H      = 12
BAR_BORDER = 1

# ── Palette indices (Inky standard) ────────────────────────────────────

WHITE_IDX = 0
BLACK_IDX = 1
ACCENT_IDX = 2   # unused on the black-only pHAT

# ── Fonts ──────────────────────────────────────────────────────────────
# `font-hanken-grotesk` exposes TTF paths as module attributes.

FONT_SIZE_PCT    = 20    # percentages
FONT_SIZE_LABEL  = 10    # SESSION / WEEKLY labels
FONT_SIZE_SMALL  = 9     # reset countdown + status footer


def load_fonts() -> dict[str, ImageFont.FreeTypeFont]:
    return {
        "pct":   ImageFont.truetype(_fhg.HankenGroteskBold,    FONT_SIZE_PCT),
        "label": ImageFont.truetype(_fhg.HankenGroteskMedium,  FONT_SIZE_LABEL),
        "small": ImageFont.truetype(_fhg.HankenGrotesk,        FONT_SIZE_SMALL),
    }


# ── Startup assertion ──────────────────────────────────────────────────

# (font key, region key, sample string used to measure)
_FONT_ROW_CHECKS = [
    ("pct",   "session_row",   "100%"),
    ("pct",   "weekly_row",    "100%"),
    ("label", "session_row",   "SESSION"),
    ("label", "weekly_row",    "WEEKLY"),
    ("small", "session_reset", "resets Mon 09:00 · 4d 6h"),
    ("small", "weekly_reset",  "resets Mon 09:00 · 4d 6h"),
    ("small", "status_footer", "status: stale"),
]


def assert_fonts_fit(fonts: dict[str, ImageFont.FreeTypeFont]) -> None:
    """
    Verify every font + sample text fits its allocated region height.
    Raises ValueError with a specific message naming the offender, so
    a font-package upgrade can't silently corrupt the layout.
    """
    for font_key, region_key, sample in _FONT_ROW_CHECKS:
        font = fonts[font_key]
        left, top, right, bottom = font.getbbox(sample)
        rendered_h = bottom - top
        allocated_h = REGIONS[region_key][1]
        if rendered_h > allocated_h:
            raise ValueError(
                f"font '{font_key}' renders {rendered_h}px for sample "
                f"{sample!r}, but region '{region_key}' is only "
                f"{allocated_h}px tall — font upgrade broke the layout"
            )
