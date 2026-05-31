"""Slide generation: editorial-style 4:5 portrait deck (Pillow).

Renders a 5-slide Instagram carousel (1080x1350) for a short-form clip:
    COVER (hook) -> INSIGHT 01/02/03 -> QUOTE

Shared design system (see the constants block): dark #0D0D12 canvas, a yellow
accent, near-white left-anchored body, a persistent footer (wordmark + 5
progress dots), DejaVu Sans Bold for body and DejaVu Serif Bold for the giant
ghosted insight numbers. Body text auto-fits (shrink + re-wrap) into the safe
area between the eyebrow and the footer so short and long insights both fit.

Public contract is unchanged: ``build_slides(highlights) -> list[str]`` returns
the 5 ordered PNG paths (consumed by main.py).
"""

import logging
import os

from PIL import Image, ImageDraw, ImageFont

import config

logger = logging.getLogger(__name__)

# --- Canvas (Instagram 4:5 portrait; independent of the 9:16 video frame) ---
W, H = 1080, 1350

# --- Palette ---
BG = (13, 13, 18)          # #0D0D12 dark canvas
ACCENT = (245, 197, 66)    # #F5C542 yellow
BODY = (240, 240, 245)     # #F0F0F5 near-white
MUTED = (110, 110, 120)    # #6E6E78 footer wordmark
MUTED_YELLOW = (176, 142, 53)  # dimmed accent for the quote attribution
DOT_OFF = (60, 60, 70)     # #3C3C46 inactive progress dots
GHOST = (32, 32, 42)       # #20202A barely-there serif numbers

# --- Geometry ---
LEFT = 90                  # left margin everything is anchored to
RIGHT = 90                 # right safe margin
BODY_W = W - LEFT - RIGHT  # 900px text column
FOOTER_CY = H - 64         # vertical center of the footer row
BODY_BOTTOM = H - 128      # body must not cross below this (clears the footer)
SAFE_TOP = 128             # top of the safe area (mirrors the bottom gap)
SAFE_BOTTOM = BODY_BOTTOM
SAFE_H = SAFE_BOTTOM - SAFE_TOP
EYEBROW_GAP = 40           # gap between the eyebrow block and the body

# --- Type ---
BODY_SIZE_MAX = 62
BODY_SIZE_MIN = 40
BODY_LINE_SPACING = 1.25
EYEBROW_SIZE = 30
FOOTER_SIZE = 24
ATTR_SIZE = 36             # quote attribution ("— Name")
ATTR_GAP = 36              # gap between the quote and its attribution
GHOST_SIZE = 520
EYEBROW_TRACKING = 6       # uniform letter spacing for every eyebrow label
FOOTER_TRACKING = 4
TICK_W, TICK_H = 70, 8     # the little accent bar above each eyebrow

# --- Fonts: bundled DejaVu (assets/fonts), with Windows fallbacks ---
_FONTS_DIR = os.path.join(config.BASE_DIR, "assets", "fonts")
_WIN_FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_SANS_CANDIDATES = [
    os.path.join(_FONTS_DIR, "DejaVuSans-Bold.ttf"),
    os.path.join(_WIN_FONTS, "arialbd.ttf"),
    os.path.join(_WIN_FONTS, "segoeuib.ttf"),
]
_SERIF_CANDIDATES = [
    os.path.join(_FONTS_DIR, "DejaVuSerif-Bold.ttf"),
    os.path.join(_WIN_FONTS, "georgiab.ttf"),
    os.path.join(_WIN_FONTS, "timesbd.ttf"),
]
_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    """Load the first available font at ``size`` (cached)."""
    for path in candidates:
        key = (path, size)
        if key in _FONT_CACHE:
            return _FONT_CACHE[key]
        if os.path.exists(path):
            font = ImageFont.truetype(path, size)
            _FONT_CACHE[key] = font
            return font
    return ImageFont.load_default(size)


def _sans(size: int) -> ImageFont.FreeTypeFont:
    return _font(_SANS_CANDIDATES, size)


def _serif(size: int) -> ImageFont.FreeTypeFont:
    return _font(_SERIF_CANDIDATES, size)


def _line_height(font: ImageFont.FreeTypeFont) -> int:
    ascent, descent = font.getmetrics()
    return int((ascent + descent) * BODY_LINE_SPACING)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """Greedy whole-word wrap of ``text`` to lines no wider than ``max_w`` px."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if draw.textlength(trial, font=font) <= max_w:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _fit_body(
    draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """Largest body size in [MIN, MAX] (step 2) whose wrap fits ``max_w`` x ``max_h``.

    Returns (font, wrapped_lines, line_height). Falls back to the floor size if
    nothing fits, so long insights stay bounded rather than overflowing.
    """
    for size in range(BODY_SIZE_MAX, BODY_SIZE_MIN - 1, -2):
        font = _sans(size)
        lines = _wrap(draw, text, font, max_w)
        lh = _line_height(font)
        if lh * len(lines) <= max_h:
            return font, lines, lh
    font = _sans(BODY_SIZE_MIN)
    return font, _wrap(draw, text, font, max_w), _line_height(font)


def _draw_tracked(
    draw: ImageDraw.ImageDraw, x: int, y: int, text: str,
    font: ImageFont.FreeTypeFont, fill, tracking: int, anchor: str = "lm",
) -> float:
    """Draw ``text`` char-by-char with extra ``tracking`` px between glyphs.
    Returns the x just past the last glyph."""
    cx = float(x)
    for ch in text:
        draw.text((cx, y), ch, font=font, fill=fill, anchor=anchor)
        cx += draw.textlength(ch, font=font) + tracking
    return cx


def _eyebrow_block_h() -> int:
    """Height of the tick + eyebrow label block (from tick top to text bottom)."""
    ascent, descent = _sans(EYEBROW_SIZE).getmetrics()
    return TICK_H + 24 + ascent + descent


def _draw_eyebrow(draw: ImageDraw.ImageDraw, label: str, y: int) -> int:
    """Accent tick bar + tracked yellow eyebrow at top ``y``. Returns its bottom y."""
    draw.rectangle([LEFT, y, LEFT + TICK_W, y + TICK_H], fill=ACCENT)
    font = _sans(EYEBROW_SIZE)
    text_y = y + TICK_H + 24
    _draw_tracked(draw, LEFT, text_y, label.upper(), font, ACCENT, EYEBROW_TRACKING, anchor="la")
    ascent, descent = font.getmetrics()
    return text_y + ascent + descent


def _draw_body(draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.FreeTypeFont, lh: int, top: int) -> None:
    y = top
    for line in lines:
        draw.text((LEFT, y), line, font=font, fill=BODY, anchor="la")
        y += lh


def _draw_ghost_number(draw: ImageDraw.ImageDraw, n: int) -> int:
    """Giant ghosted serif number bleeding off the top edge. Returns its bottom y."""
    font = _serif(GHOST_SIZE)
    text = f"{n:02d}"
    x, y = 50, -120  # negative y bleeds the number off the top
    draw.text((x, y), text, font=font, fill=GHOST, anchor="la")
    return draw.textbbox((x, y), text, font=font, anchor="la")[3]


def _draw_footer(draw: ImageDraw.ImageDraw, active: int, total: int = 5) -> None:
    """Wordmark bottom-left + ``total`` progress dots bottom-right (``active`` yellow)."""
    _draw_tracked(draw, LEFT, FOOTER_CY, "THE MINDSET MENTOR", _sans(FOOTER_SIZE),
                  MUTED, FOOTER_TRACKING, anchor="lm")
    r, gap = 7, 26
    for i in range(total):
        cx = (W - RIGHT) - (total - 1 - i) * gap
        color = ACCENT if i == active else DOT_OFF
        draw.ellipse([cx - r, FOOTER_CY - r, cx + r, FOOTER_CY + r], fill=color)


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    return img, ImageDraw.Draw(img)


def _render_cover(path: str, hook: str, idx: int) -> None:
    img, draw = _canvas()
    eb_h = _eyebrow_block_h()
    font, lines, lh = _fit_body(draw, hook, BODY_W, SAFE_H - eb_h - EYEBROW_GAP)
    # Center the whole (eyebrow + body) block vertically in the safe area.
    block_h = eb_h + EYEBROW_GAP + lh * len(lines)
    top = SAFE_TOP + (SAFE_H - block_h) // 2
    _draw_eyebrow(draw, "WATCH THIS", top)
    _draw_body(draw, lines, font, lh, top + eb_h + EYEBROW_GAP)
    _draw_footer(draw, idx)
    img.save(path)
    logger.info("Rendered slide: %s (cover, centered)", os.path.basename(path))


def _render_insight(path: str, number: int, text: str, idx: int) -> None:
    img, draw = _canvas()
    ghost_bottom = _draw_ghost_number(draw, number)
    eyebrow_bottom = _draw_eyebrow(draw, "INSIGHT", int(ghost_bottom) + 30)
    body_top = eyebrow_bottom + 36
    font, lines, lh = _fit_body(draw, text, BODY_W, BODY_BOTTOM - body_top)
    _draw_body(draw, lines, font, lh, body_top)
    _draw_footer(draw, idx)
    img.save(path)
    logger.info("Rendered slide: %s (insight %02d)", os.path.basename(path), number)


def _render_quote(path: str, quote: str, attribution: str | None, idx: int) -> None:
    img, draw = _canvas()
    eb_h = _eyebrow_block_h()
    text = f"“{quote}”"  # curly opening/closing quotes

    # Reserve the attribution's height up front so the full block centers together.
    attr_h = 0
    if attribution:
        a_ascent, a_descent = _sans(ATTR_SIZE).getmetrics()
        attr_h = ATTR_GAP + a_ascent + a_descent

    font, lines, lh = _fit_body(draw, text, BODY_W, SAFE_H - eb_h - EYEBROW_GAP - attr_h)
    body_h = lh * len(lines)
    block_h = eb_h + EYEBROW_GAP + body_h + attr_h
    top = SAFE_TOP + (SAFE_H - block_h) // 2

    _draw_eyebrow(draw, "QUOTE", top)
    body_top = top + eb_h + EYEBROW_GAP
    _draw_body(draw, lines, font, lh, body_top)
    if attribution:
        ay = body_top + body_h + ATTR_GAP
        draw.text((LEFT, ay), f"— {attribution}", font=_sans(ATTR_SIZE), fill=MUTED_YELLOW, anchor="la")
    _draw_footer(draw, idx)
    img.save(path)
    logger.info("Rendered slide: %s (quote, centered%s)", os.path.basename(path),
                ", attributed" if attribution else "")


# Optional attribution keys the extraction MIGHT carry. The current ai_extract
# schema produces none of these, so the quote slide shows no attribution unless
# a real one is present — we never invent a speaker.
_ATTR_KEYS = ("quote_author", "quote_attribution", "attribution", "speaker", "author")


def _attribution(highlights: dict) -> str | None:
    """Return a real quote attribution if the extraction supplies one, else None."""
    for key in _ATTR_KEYS:
        value = highlights.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def build_slides(highlights: dict) -> list[str]:
    """Render the 5-slide editorial deck; return ordered PNG paths.

    Order: COVER (hook), INSIGHT 01-03 (insights), QUOTE (best_quote).
    Requires keys: hook, insights (>=1, uses up to 3), best_quote.
    """
    os.makedirs(config.SLIDE_DIR, exist_ok=True)

    hook = (highlights.get("hook") or "").strip()
    insights = [str(i).strip() for i in (highlights.get("insights") or []) if str(i).strip()]
    quote = (highlights.get("best_quote") or "").strip()
    if not hook or not insights or not quote:
        raise ValueError("highlights needs hook, insights, and best_quote to build the deck")

    paths: list[str] = []

    def _path(i: int) -> str:
        p = os.path.join(config.SLIDE_DIR, f"slide_{i:02d}.png")
        paths.append(p)
        return p

    _render_cover(_path(0), hook, idx=0)
    for n, insight in enumerate(insights[:3], start=1):
        _render_insight(_path(len(paths)), n, insight, idx=len(paths) - 1)
    _render_quote(_path(len(paths)), quote, _attribution(highlights), idx=len(paths) - 1)

    logger.info("Built %d slides in %s", len(paths), config.SLIDE_DIR)
    return paths


# A real ai_extract output, used to test rendering without spending API calls.
SAMPLE_HIGHLIGHTS = {
    "hook": "Your nervous system is rejecting modern life — and the people who fix it will dominate the next decade.",
    "insights": [
        "Your brain is 200,000 years old trying to survive in a world of infinite notifications, and it's triggering low-grade fight-or-flight mode 24/7.",
        "The people who will thrive most over the next decade aren't those who adapt to modern culture — they're the ones who consciously choose to do the opposite.",
        "Rare traits like deep focus, emotional regulation, and independent thinking are about to become the most valuable assets a person can have.",
    ],
    "best_quote": "In a world that's addicted to noise, being peaceful becomes your superpower.",
    "title": "Your Nervous System Is Rejecting Modern Life — Here's How to Fix It",
    "clip_start": 2355,
    "clip_end": 2415,
    "hashtags": ["#MindsetMentor", "#NervousSystem", "#DigitalDetox"],
}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    slides = build_slides(SAMPLE_HIGHLIGHTS)

    print(f"\nGenerated {len(slides)} slides:")
    for p in slides:
        with Image.open(p) as im:
            print(f"  {os.path.basename(p)}  {im.size[0]}x{im.size[1]}  {os.path.getsize(p) // 1024} KB")
