"""Slide generation: render highlight text into vertical slide images via Pillow.

Produces a 9:16 slide deck for a short-form clip: a hook opener, one slide per
insight, and a quote slide. Text auto-wraps and auto-shrinks to fit the frame.
"""

import logging
import os

from PIL import Image, ImageDraw, ImageFont

import config

logger = logging.getLogger(__name__)

# Candidate Windows font files, tried in order (bold, then regular).
_BOLD_FONTS = ["arialbd.ttf", "segoeuib.ttf", "calibrib.ttf"]
_REGULAR_FONTS = ["arial.ttf", "segoeui.ttf", "calibri.ttf"]
_FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a TrueType font at ``size``, falling back to Pillow's default."""
    for name in (_BOLD_FONTS if bold else _REGULAR_FONTS):
        path = os.path.join(_FONTS_DIR, name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedy word-wrap ``text`` to lines no wider than ``max_width`` px."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    start_size: int,
    bold: bool,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """Find the largest font size at which ``text`` fits in the box.

    Returns (font, wrapped_lines, line_height).
    """
    size = start_size
    while size >= config.SLIDE_MIN_FONT_SIZE:
        font = _load_font(size, bold=bold)
        lines = _wrap(draw, text, font, max_width)
        ascent, descent = font.getmetrics()
        line_height = int((ascent + descent) * 1.25)
        if line_height * len(lines) <= max_height:
            return font, lines, line_height
        size -= 4
    # Floor reached — use the minimum size regardless.
    font = _load_font(config.SLIDE_MIN_FONT_SIZE, bold=bold)
    lines = _wrap(draw, text, font, max_width)
    ascent, descent = font.getmetrics()
    return font, lines, int((ascent + descent) * 1.25)


def _render_slide(path: str, eyebrow: str, body: str) -> None:
    """Render one slide: an accent ``eyebrow`` label above centered ``body`` text."""
    w, h = config.SLIDE_WIDTH, config.SLIDE_HEIGHT
    margin = config.SLIDE_MARGIN
    max_width = w - 2 * margin

    img = Image.new("RGB", (w, h), config.SLIDE_BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Reserve vertical space for the eyebrow so body stays centered in the rest.
    eyebrow_font = _load_font(config.SLIDE_EYEBROW_FONT_SIZE, bold=True)
    eyebrow_h = int(sum(eyebrow_font.getmetrics()) * 1.5) if eyebrow else 0

    body_font, lines, line_height = _fit_text(
        draw, body, max_width, h - 2 * margin - eyebrow_h, config.SLIDE_BODY_FONT_SIZE, bold=True
    )

    block_h = line_height * len(lines)
    start_y = (h - block_h) // 2

    if eyebrow:
        ex = (w - draw.textlength(eyebrow, font=eyebrow_font)) // 2
        draw.text((ex, start_y - eyebrow_h), eyebrow, font=eyebrow_font, fill=config.SLIDE_ACCENT_COLOR)

    y = start_y
    for line in lines:
        lx = (w - draw.textlength(line, font=body_font)) // 2
        draw.text((lx, y), line, font=body_font, fill=config.SLIDE_TEXT_COLOR)
        y += line_height

    img.save(path)
    logger.info("Rendered slide: %s", os.path.basename(path))


def build_slides(highlights: dict) -> list[str]:
    """Render a slide deck from a ``highlights`` dict; return ordered slide paths.

    Expects keys: hook, insights (list), best_quote.
    """
    os.makedirs(config.SLIDE_DIR, exist_ok=True)

    specs: list[tuple[str, str]] = []
    if highlights.get("hook"):
        specs.append(("WATCH THIS", highlights["hook"]))
    for i, insight in enumerate(highlights.get("insights", []), start=1):
        specs.append((f"INSIGHT {i}", insight))
    if highlights.get("best_quote"):
        specs.append(("QUOTE", f"“{highlights['best_quote']}”"))

    if not specs:
        raise ValueError("highlights has no hook, insights, or best_quote to render")

    paths: list[str] = []
    for idx, (eyebrow, body) in enumerate(specs):
        path = os.path.join(config.SLIDE_DIR, f"slide_{idx:02d}.png")
        _render_slide(path, eyebrow, body)
        paths.append(path)

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
