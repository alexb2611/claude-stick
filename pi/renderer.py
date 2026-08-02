"""
Pillow composition of the unified screen. Pure function from Snapshot
to PIL.Image; the panel-pushing step is separate so tests can use a
FakeDisplay and tools/preview.py can render off-Pi via inky.mock or
just save the PNG.
"""

from PIL import Image, ImageDraw

from layout import (
    BADGE_SIZE,
    BADGE_X,
    BAR_BORDER,
    BAR_H,
    BAR_W,
    BAR_X,
    BLACK_IDX,
    MARGIN_X,
    PANEL_H,
    PANEL_W,
    PCT_RIGHT_X,
    RED_IDX,
    REGIONS,
    WHITE_IDX,
    YELLOW_IDX,
    assert_fonts_fit,
    load_fonts,
)
from state import Badge, Snapshot


# Module-level: load fonts once and verify they fit. Failing here at
# import time is the desired fail-fast behaviour — claude_stick_pi.py
# can't even start if fonts don't match the layout.
_FONTS = load_fonts()
assert_fonts_fit(_FONTS)


# Exactly four pure colours in InkyJD79661's native index order. Keeping
# the palette at precisely four entries matters: the driver's set_image
# sees a 4-colour P-mode image and maps it 1:1 with dithering disabled.
_PALETTE = [
    0,   0,   0,    # 0: black
    255, 255, 255,  # 1: white
    255, 255, 0,    # 2: yellow
    255, 0,   0,    # 3: red
]

# Utilisation-level colours: badge fill and bar fill share this mapping.
_LEVEL_FILL = {
    Badge.OK:   BLACK_IDX,   # bar only; OK badge stays hollow
    Badge.WARN: YELLOW_IDX,
    Badge.HIGH: RED_IDX,
}


def render(snapshot: Snapshot) -> Image.Image:
    """Compose the full 250×122 panel as a P-mode PIL image."""
    img = Image.new("P", (PANEL_W, PANEL_H), color=WHITE_IDX)
    img.putpalette(_PALETTE)
    draw = ImageDraw.Draw(img)

    _draw_section(draw,
                  region_key="session_row",
                  bar_region_key="session_bar",
                  reset_region_key="session_reset",
                  label="SESSION",
                  pct_text=snapshot.session_pct_text,
                  pct_int=snapshot.session_pct_int,
                  badge=snapshot.session_badge,
                  reset_text=snapshot.session_reset_text)

    _draw_section(draw,
                  region_key="weekly_row",
                  bar_region_key="weekly_bar",
                  reset_region_key="weekly_reset",
                  label="WEEKLY",
                  pct_text=snapshot.weekly_pct_text,
                  pct_int=snapshot.weekly_pct_int,
                  badge=snapshot.weekly_badge,
                  reset_text=snapshot.weekly_reset_text)

    _draw_footer(draw, snapshot.status_text)
    return img


def push(image: Image.Image, display) -> None:
    """Hand the image to an Inky display (or duck-typed substitute)."""
    display.set_image(image)
    display.show()


# ── Section composition ────────────────────────────────────────────────

def _draw_section(draw: ImageDraw.ImageDraw, *,
                  region_key: str,
                  bar_region_key: str,
                  reset_region_key: str,
                  label: str,
                  pct_text: str,
                  pct_int: int,
                  badge: Badge,
                  reset_text: str) -> None:

    row_y, row_h = REGIONS[region_key]
    bar_y, _     = REGIONS[bar_region_key]
    reset_y, _   = REGIONS[reset_region_key]

    # Label (left-aligned, near top of row)
    draw.text((MARGIN_X, row_y), label, font=_FONTS["label"], fill=BLACK_IDX)

    # Percentage (right-aligned by computing width)
    pct_w, _ = _text_size(_FONTS["pct"], pct_text)
    draw.text((PCT_RIGHT_X - pct_w, row_y), pct_text,
              font=_FONTS["pct"], fill=BLACK_IDX)

    # Badge, vertically centred in the row
    badge_y = row_y + (row_h - BADGE_SIZE) // 2
    _draw_badge(draw, BADGE_X, badge_y, badge)

    # Bar (outline + fill coloured by utilisation level)
    _draw_bar(draw, BAR_X, bar_y, pct_int, _LEVEL_FILL[badge])

    # Reset text
    draw.text((MARGIN_X, reset_y), reset_text,
              font=_FONTS["small"], fill=BLACK_IDX)


def _draw_footer(draw: ImageDraw.ImageDraw, status_text: str) -> None:
    y, _ = REGIONS["status_footer"]
    prefix = "status: "
    draw.text((MARGIN_X, y), prefix, font=_FONTS["small"], fill=BLACK_IDX)
    prefix_w, _ = _text_size(_FONTS["small"], prefix)
    # Any non-ok pipeline state gets the red word treatment. Yellow is
    # avoided for text — a 9 px yellow word on white e-paper is unreadable.
    word_colour = BLACK_IDX if status_text == "ok" else RED_IDX
    draw.text((MARGIN_X + prefix_w, y), status_text,
              font=_FONTS["small"], fill=word_colour)


def _draw_badge(draw: ImageDraw.ImageDraw, x: int, y: int, level: Badge) -> None:
    """13×13 box; OK=hollow with dot, WARN=yellow with !, HIGH=red with white !."""
    if level == Badge.OK:
        draw.rectangle((x, y, x + BADGE_SIZE - 1, y + BADGE_SIZE - 1),
                       fill=WHITE_IDX, outline=BLACK_IDX)
        char = "·"
        text_colour = BLACK_IDX
    else:
        draw.rectangle((x, y, x + BADGE_SIZE - 1, y + BADGE_SIZE - 1),
                       fill=_LEVEL_FILL[level], outline=BLACK_IDX)
        char = "!"
        text_colour = BLACK_IDX if level == Badge.WARN else WHITE_IDX

    # Centre the character inside the badge using font metrics.
    left, top, right, bottom = _FONTS["small"].getbbox(char)
    w = right - left
    h = bottom - top
    cx = x + (BADGE_SIZE - w) // 2 - left
    cy = y + (BADGE_SIZE - h) // 2 - top
    draw.text((cx, cy), char, font=_FONTS["small"], fill=text_colour)


def _draw_bar(draw: ImageDraw.ImageDraw, x: int, y: int, pct_int: int,
              fill_idx: int) -> None:
    # Outer rectangle (border).
    draw.rectangle((x, y, x + BAR_W - 1, y + BAR_H - 1),
                   fill=WHITE_IDX, outline=BLACK_IDX, width=BAR_BORDER)
    if pct_int <= 0:
        return
    pct = min(pct_int, 100) / 100.0
    inner_w = BAR_W - 2 * BAR_BORDER
    fill_w  = max(1, int(pct * inner_w))
    draw.rectangle((x + BAR_BORDER, y + BAR_BORDER,
                    x + BAR_BORDER + fill_w - 1,
                    y + BAR_H - 1 - BAR_BORDER),
                   fill=fill_idx, outline=fill_idx)


def _text_size(font, text: str) -> tuple[int, int]:
    left, top, right, bottom = font.getbbox(text)
    return right - left, bottom - top
