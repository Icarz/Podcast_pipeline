"""Slide generation: editorial-style 4:5 portrait deck (Pillow).

Renders a 6-slide Instagram carousel (1080x1350) for a short-form clip:
    COVER (hook) -> INSIGHT 01/02/03 -> QUOTE -> FOLLOW (CTA)

Shared design system (see the constants block): dark #0D0D12 canvas, a yellow
accent, near-white left-anchored body, a persistent footer (wordmark + 6
progress dots), DejaVu Sans Bold for body and DejaVu Serif Bold for the giant
ghosted insight numbers. Body text auto-fits (shrink + re-wrap) into the safe
area between the eyebrow and the footer so short and long insights both fit.
The closing FOLLOW slide is a solid-background CTA (no photo query spent on it).

Public contract is unchanged: ``build_slides(highlights) -> list[str]`` returns
the 6 ordered PNG paths (consumed by main.py).
"""

import logging
import os

from PIL import Image, ImageDraw, ImageFont

import config
from modules import pexels_bg

logger = logging.getLogger(__name__)

# --- Canvas (Instagram 4:5 portrait; independent of the 9:16 video frame) ---
W, H = 1080, 1350
TOTAL_SLIDES = 6  # COVER, INSIGHT x3, QUOTE, FOLLOW

# --- Palette ---
BG = (13, 13, 18)          # #0D0D12 dark canvas
ACCENT = (245, 197, 66)    # #F5C542 yellow
BODY = (240, 240, 245)     # #F0F0F5 near-white
MUTED = (110, 110, 120)    # #6E6E78 footer wordmark
MUTED_YELLOW = (176, 142, 53)  # dimmed accent for the quote attribution
DOT_OFF = (60, 60, 70)     # #3C3C46 inactive progress dots
GHOST = (32, 32, 42)       # #20202A barely-there serif numbers (solid-bg fallback only)

# --- Photo background treatment (full-bleed Pexels photo behind every slide) ---
# Readability is non-negotiable: a flat black overlay sets a dark floor, and a
# vertical gradient scrim deepens it further over the text band so body copy
# stays legible no matter how bright the photo is.
OVERLAY_BASE_ALPHA = 0.60   # flat 60% black over the whole photo
BAND_PEAK_ALPHA = 0.75      # ~75% black in the region where the text sits
INSIGHT_BAND_PEAK_ALPHA = 0.82  # insight photos are busiest -> deepen their text band
BAND_FEATHER = 220          # px ramp from base->peak above/below the text band
GHOST_WHITE = (255, 255, 255)       # ghost number now reads as a watermark...
GHOST_ALPHA = int(0.12 * 255)       # ...at ~12% opacity over the photo
TEXT_STROKE_W = 2                   # black stroke/shadow behind body text
TEXT_STROKE = (0, 0, 0)

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
    stroke_width: int = 0, stroke_fill=TEXT_STROKE,
) -> float:
    """Draw ``text`` char-by-char with extra ``tracking`` px between glyphs.
    Returns the x just past the last glyph."""
    cx = float(x)
    for ch in text:
        draw.text((cx, y), ch, font=font, fill=fill, anchor=anchor,
                  stroke_width=stroke_width, stroke_fill=stroke_fill)
        cx += draw.textlength(ch, font=font) + tracking
    return cx


# --- Photo background helpers ---------------------------------------------

def _fill_photo(path: str) -> Image.Image | None:
    """Open ``path``, scale + center-crop to exactly WxH (fill, no distortion)."""
    try:
        photo = Image.open(path).convert("RGB")
    except (OSError, ValueError) as exc:
        logger.warning("Could not open slide photo %s (%s); using solid bg", path, exc)
        return None
    pw, ph = photo.size
    scale = max(W / pw, H / ph)
    photo = photo.resize((max(1, round(pw * scale)), max(1, round(ph * scale))), Image.LANCZOS)
    sw, sh = photo.size
    left = (sw - W) // 2
    top = (sh - H) // 2
    return photo.crop((left, top, left + W, top + H))


def _scrim_mask(band_top: int, band_bottom: int, peak_alpha: float = BAND_PEAK_ALPHA) -> Image.Image:
    """Vertical black-alpha mask: OVERLAY_BASE_ALPHA everywhere, ramping up to
    ``peak_alpha`` across the text band [band_top, band_bottom]."""
    band_top = max(0, min(H, band_top))
    band_bottom = max(band_top, min(H, band_bottom))
    base = int(OVERLAY_BASE_ALPHA * 255)
    peak = int(peak_alpha * 255)
    col = Image.new("L", (1, H))
    px = col.load()
    for y in range(H):
        if band_top <= y <= band_bottom:
            a = peak
        else:
            d = (band_top - y) if y < band_top else (y - band_bottom)
            a = peak - (peak - base) * min(d / BAND_FEATHER, 1.0)
        px[0, y] = int(a)
    return col.resize((W, H))


def _photo_canvas(bg_path: str | None, band_top: int, band_bottom: int,
                  peak_alpha: float = BAND_PEAK_ALPHA) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """Build a darkened full-bleed photo canvas, or fall back to the solid BG.

    The flat overlay + gradient scrim guarantee a dark, readable surface; the
    text band is pushed to ``peak_alpha`` black so body copy is legible over any
    photo.
    """
    photo = _fill_photo(bg_path) if bg_path else None
    if photo is None:
        img = Image.new("RGB", (W, H), BG)
        return img, ImageDraw.Draw(img)
    black = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.composite(black, photo, _scrim_mask(band_top, band_bottom, peak_alpha))
    return img, ImageDraw.Draw(img)


def _draw_ghost_number_overlay(img: Image.Image, n: int) -> None:
    """Composite the giant serif number as a faint white watermark (mutates img)."""
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).text((50, -120), f"{n:02d}", font=_serif(GHOST_SIZE),
                                 fill=(*GHOST_WHITE, GHOST_ALPHA), anchor="la")
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))


def _eyebrow_block_h() -> int:
    """Height of the tick + eyebrow label block (from tick top to text bottom)."""
    ascent, descent = _sans(EYEBROW_SIZE).getmetrics()
    return TICK_H + 24 + ascent + descent


def _draw_eyebrow(draw: ImageDraw.ImageDraw, label: str, y: int) -> int:
    """Accent tick bar + tracked yellow eyebrow at top ``y``. Returns its bottom y."""
    draw.rectangle([LEFT, y, LEFT + TICK_W, y + TICK_H], fill=ACCENT)
    font = _sans(EYEBROW_SIZE)
    text_y = y + TICK_H + 24
    _draw_tracked(draw, LEFT, text_y, label.upper(), font, ACCENT, EYEBROW_TRACKING,
                  anchor="la", stroke_width=TEXT_STROKE_W)
    ascent, descent = font.getmetrics()
    return text_y + ascent + descent


def _draw_body(draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.FreeTypeFont, lh: int, top: int) -> None:
    y = top
    for line in lines:
        draw.text((LEFT, y), line, font=font, fill=BODY, anchor="la",
                  stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE)
        y += lh


def _draw_footer(draw: ImageDraw.ImageDraw, active: int, total: int = TOTAL_SLIDES) -> None:
    """Wordmark bottom-left + ``total`` progress dots bottom-right (``active`` yellow)."""
    _draw_tracked(draw, LEFT, FOOTER_CY, config.BRAND_NAME.upper(), _sans(FOOTER_SIZE),
                  MUTED, FOOTER_TRACKING, anchor="lm", stroke_width=TEXT_STROKE_W)
    r, gap = 7, 26
    for i in range(total):
        cx = (W - RIGHT) - (total - 1 - i) * gap
        color = ACCENT if i == active else DOT_OFF
        draw.ellipse([cx - r, FOOTER_CY - r, cx + r, FOOTER_CY + r], fill=color)


def _measure_draw() -> ImageDraw.ImageDraw:
    """A throwaway draw context for text measurement before the canvas exists."""
    return ImageDraw.Draw(Image.new("RGB", (W, H)))


def _render_cover(path: str, hook: str, idx: int, bg_path: str | None) -> None:
    eb_h = _eyebrow_block_h()
    font, lines, lh = _fit_body(_measure_draw(), hook, BODY_W, SAFE_H - eb_h - EYEBROW_GAP)
    # Center the whole (eyebrow + body) block vertically in the safe area.
    block_h = eb_h + EYEBROW_GAP + lh * len(lines)
    top = SAFE_TOP + (SAFE_H - block_h) // 2

    img, draw = _photo_canvas(bg_path, top, top + block_h)
    _draw_eyebrow(draw, "WATCH THIS", top)
    _draw_body(draw, lines, font, lh, top + eb_h + EYEBROW_GAP)
    _draw_footer(draw, idx)
    img.save(path)
    logger.info("Rendered slide: %s (cover, %s)", os.path.basename(path),
                "photo" if bg_path else "solid")


def _render_insight(path: str, number: int, text: str, idx: int, bg_path: str | None) -> None:
    md = _measure_draw()
    ghost_bottom = md.textbbox((50, -120), f"{number:02d}", font=_serif(GHOST_SIZE), anchor="la")[3]
    eyebrow_top = int(ghost_bottom) + 30
    body_top = eyebrow_top + _eyebrow_block_h() + 36
    font, lines, lh = _fit_body(md, text, BODY_W, BODY_BOTTOM - body_top)
    body_bottom = body_top + lh * len(lines)

    img, draw = _photo_canvas(bg_path, eyebrow_top, body_bottom, peak_alpha=INSIGHT_BAND_PEAK_ALPHA)
    _draw_ghost_number_overlay(img, number)  # faint white watermark under the text
    _draw_eyebrow(draw, "INSIGHT", eyebrow_top)
    _draw_body(draw, lines, font, lh, body_top)
    _draw_footer(draw, idx)
    img.save(path)
    logger.info("Rendered slide: %s (insight %02d, %s)", os.path.basename(path), number,
                "photo" if bg_path else "solid")


def _render_quote(path: str, quote: str, attribution: str | None, idx: int, bg_path: str | None) -> None:
    eb_h = _eyebrow_block_h()
    text = f"“{quote}”"  # curly opening/closing quotes

    # Reserve the attribution's height up front so the full block centers together.
    attr_h = 0
    if attribution:
        a_ascent, a_descent = _sans(ATTR_SIZE).getmetrics()
        attr_h = ATTR_GAP + a_ascent + a_descent

    font, lines, lh = _fit_body(_measure_draw(), text, BODY_W, SAFE_H - eb_h - EYEBROW_GAP - attr_h)
    body_h = lh * len(lines)
    block_h = eb_h + EYEBROW_GAP + body_h + attr_h
    top = SAFE_TOP + (SAFE_H - block_h) // 2

    img, draw = _photo_canvas(bg_path, top, top + block_h)
    _draw_eyebrow(draw, "QUOTE", top)
    body_top = top + eb_h + EYEBROW_GAP
    _draw_body(draw, lines, font, lh, body_top)
    if attribution:
        ay = body_top + body_h + ATTR_GAP
        draw.text((LEFT, ay), f"— {attribution}", font=_sans(ATTR_SIZE), fill=MUTED_YELLOW,
                  anchor="la", stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE)
    _draw_footer(draw, idx)
    img.save(path)
    logger.info("Rendered slide: %s (quote, %s%s)", os.path.basename(path),
                "photo" if bg_path else "solid", ", attributed" if attribution else "")


def _render_cta(path: str, idx: int, bg_path: str | None = None) -> None:
    """Closing 'follow for more' CTA slide.

    Renders on ``bg_path`` (the wolf payoff-in-action image in the branded
    deck) with the usual dark scrim, or the solid brand background when no
    image is available (legacy Pexels decks always pass None here).
    """
    eb_h = _eyebrow_block_h()
    text = "FOLLOW FOR MORE"

    font, lines, lh = _fit_body(_measure_draw(), text, BODY_W, SAFE_H - eb_h - EYEBROW_GAP)
    block_h = eb_h + EYEBROW_GAP + lh * len(lines)
    top = SAFE_TOP + (SAFE_H - block_h) // 2

    img, draw = _photo_canvas(bg_path, top, top + block_h)
    _draw_eyebrow(draw, "STAY IN THE LOOP", top)
    y = top + eb_h + EYEBROW_GAP
    for line in lines:
        draw.text((LEFT, y), line, font=font, fill=ACCENT, anchor="la",
                  stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE)
        y += lh
    _draw_footer(draw, idx)
    img.save(path)
    logger.info("Rendered slide: %s (cta, %s)", os.path.basename(path),
                "photo" if bg_path else "solid")


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


def _slide_queries(queries: list[str], n_insights: int) -> list[str | None]:
    """Map ai_extract's search_queries 1:1 onto the slide order.

    ai_extract now emits exactly one art-directed query per slide, in slide
    order: [0] cover, [1..n] insights, [last] quote. Each slide takes its own
    query (no cycling); a missing entry stays None so that slide falls back to
    the solid background.
    """
    total = 2 + n_insights
    queries = [q for q in queries if isinstance(q, str) and q.strip()]
    return [queries[i] if i < len(queries) else None for i in range(total)]


def _fetch_slide_backgrounds(queries: list[str], n_insights: int, force: bool) -> list[str | None]:
    """Fetch one portrait Pexels photo per slide -> tmp/slide_bg_<query-hash>.jpg.

    Cached by query hash, not slide index, so a new episode's different
    search_queries fetch fresh photos instead of reusing the prior deck.
    Degrades per slide: a query that yields nothing (or a Pexels outage) leaves
    that slide's entry None so it renders on the solid background instead.
    """
    os.makedirs(config.TMP_DIR, exist_ok=True)
    paths: list[str | None] = []
    for query in _slide_queries(queries, n_insights):
        cache = os.path.join(config.TMP_DIR, f"slide_bg_{pexels_bg.query_slug(query)}.jpg")
        paths.append(pexels_bg.fetch_photo(query, cache, force=force) if query else None)
    return paths


def _map_photos_to_slides(photos: list[str], n_insights: int) -> tuple[list[str | None], str | None]:
    """Map the clip's generated background images onto the slide deck.

    ``photos`` is the (usually 6-image) story-arc set from the video render,
    in beat order [problem, problem, stakes, reframe, payoff-in-action,
    payoff-resolved]. Returns ``(bgs, follow_bg)`` where ``bgs`` is in slide
    order [cover, insight 1..n, quote]: cover = scene 1 (the hook IS the
    problem), insights = scenes 2-4, quote = scene 6 (resolved closure), and
    the FOLLOW slide gets scene 5 — so all 6 images are used and every slide
    is branded. Missing entries stay None (solid-background fallback).
    """
    def _at(i: int) -> str | None:
        return photos[i] if 0 <= i < len(photos) else None

    bgs = [_at(0)] + [_at(1 + k) for k in range(n_insights)]
    bgs.append(_at(5) if len(photos) > 5 else _at(len(photos) - 1))  # quote
    follow_bg = _at(4) if len(photos) > 5 else None
    return bgs, follow_bg


def build_slides(highlights: dict, force: bool = False, photo_paths: list[str] | None = None) -> list[str]:
    """Render the editorial deck on full-bleed image backgrounds.

    Order: COVER (hook), INSIGHT 01-03 (insights), QUOTE (best_quote), FOLLOW (CTA).
    Requires keys: hook, insights (>=1, uses up to 3), best_quote.

    ``photo_paths`` (the branded path, since 2026-07-31): the clip's generated
    wolf background images, passed straight from the video render — video and
    carousel become one body of work (see :func:`_map_photos_to_slides`; the
    FOLLOW slide gets an image too). Without ``photo_paths`` (old cached plans,
    the ``__main__`` harness) the legacy Pexels flow fetches one stock photo
    per ``search_queries`` entry and the FOLLOW slide stays solid. Either way
    each slide degrades to the solid dark background when its image is
    missing. ``force`` re-downloads photos (legacy path only).
    """
    os.makedirs(config.SLIDE_DIR, exist_ok=True)

    hook = (highlights.get("hook") or "").strip()
    insights = [str(i).strip() for i in (highlights.get("insights") or []) if str(i).strip()]
    quote = (highlights.get("best_quote") or "").strip()
    if not hook or not insights or not quote:
        raise ValueError("highlights needs hook, insights, and best_quote to build the deck")

    insights = insights[:3]
    if photo_paths:
        bgs, follow_bg = _map_photos_to_slides(photo_paths, len(insights))
        logger.info("Slide backgrounds: %d generated wolf image(s) (branded deck)", len(photo_paths))
    else:
        bgs = _fetch_slide_backgrounds(highlights.get("search_queries") or [], len(insights), force)
        follow_bg = None

    paths: list[str] = []

    def _path(i: int) -> str:
        p = os.path.join(config.SLIDE_DIR, f"slide_{i:02d}.png")
        paths.append(p)
        return p

    _render_cover(_path(0), hook, idx=0, bg_path=bgs[0])
    for n, insight in enumerate(insights, start=1):
        i = len(paths)
        _render_insight(_path(i), n, insight, idx=i, bg_path=bgs[i])
    qi = len(paths)
    _render_quote(_path(qi), quote, _attribution(highlights), idx=qi, bg_path=bgs[qi])

    ci = len(paths)
    _render_cta(_path(ci), idx=ci, bg_path=follow_bg)

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
    "search_queries": [
        "person sitting alone sunset",
        "city crowd phone screens",
        "reading book natural light",
        "solo forest walk morning",
    ],
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
