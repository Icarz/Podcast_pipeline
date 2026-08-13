"""YouTube thumbnail generation (Pillow + a dedicated 7th AI image).

Added 2026-08-13 after noticing the video/slide art (6 illustrated wolf
scenes, composed for the 9:16 video frame / 4:5 carousel) has no headroom
reserved for bold thumbnail text, and YouTube's own 16:9 crop doesn't suit
any of those 6 compositions well.

Design, mirroring a competitor-style reference the user supplied: giant,
chunky, rounded poster lettering (Lilita One) sitting over an illustrated
hero shot. The AI image supplies ONLY the backdrop -- the headline itself is
drawn with Pillow, never rendered by the image model, because exact text
from an image model is failure-prone (misspellings, wrong words) and a
wrong thumbnail headline is the one asset everyone sees before they click.
See modules/image_gen.py's THUMBNAIL_HEADROOM_BLOCK for the composition
instruction that reserves clean space at the top of the frame for this text.

Public contract: ``build_thumbnail(highlights, basename="", force=False) ->
str`` returns the single PNG path (consumed by main.py, uploaded by hand to
YouTube Studio's thumbnail placeholder -- there is no upload API here, same
manual-publishing philosophy as everything else in this pipeline).
"""

import glob
import json
import logging
import os

from PIL import Image, ImageDraw, ImageFont

import config
from modules import image_gen

logger = logging.getLogger(__name__)

# --- Canvas (YouTube's spec; independent of the 9:16 video / 4:5 slide frames) ---
W, H = config.THUMBNAIL_WIDTH, config.THUMBNAIL_HEIGHT

# --- Geometry ---
LEFT, RIGHT = 64, 64
BODY_W = W - LEFT - RIGHT
HEADROOM_H = int(H * 0.42)  # matches image_gen.THUMBNAIL_HEADROOM_BLOCK's "top ~40%"
TOP_MARGIN = 36

# --- Type: chunky rounded poster lettering, auto-fit within the headroom ---
FONT_SIZE_MAX = 200
FONT_SIZE_MIN = 84
LINE_SPACING = 0.92          # tight leading -- the reference sits lines close together
STROKE_RATIO = 0.09          # stroke width as a fraction of font size
FILL = (15, 15, 15)          # near-black
STROKE = (255, 255, 255)     # white halo keeps it legible on any illustrated background
LINE_ROTATIONS = [-3, 2, -2, 3]  # alternating tilt per line, faking hand-lettering

_FONTS_DIR = os.path.join(config.BASE_DIR, "assets", "fonts")
_WIN_FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_FONT_CANDIDATES = [
    os.path.join(_FONTS_DIR, "LilitaOne.ttf"),
    os.path.join(_WIN_FONTS, "impact.ttf"),
    os.path.join(_WIN_FONTS, "arialbd.ttf"),
]
_FONT_CACHE: dict[int, ImageFont.FreeTypeFont] = {}


def _font(size: int) -> ImageFont.FreeTypeFont:
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            font = ImageFont.truetype(path, size)
            _FONT_CACHE[size] = font
            return font
    font = ImageFont.load_default(size)
    _FONT_CACHE[size] = font
    return font


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
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


def _fit(text: str, max_w: int, max_h: int) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """Largest size in [MIN, MAX] whose wrap fits max_w x max_h. Mirrors
    slide_gen._fit_body's shrink-to-fit approach."""
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for size in range(FONT_SIZE_MAX, FONT_SIZE_MIN - 1, -4):
        font = _font(size)
        lines = _wrap(probe, text, font, max_w)
        ascent, descent = font.getmetrics()
        lh = int((ascent + descent) * LINE_SPACING)
        if lh * len(lines) <= max_h:
            return font, lines, lh
    font = _font(FONT_SIZE_MIN)
    ascent, descent = font.getmetrics()
    return font, _wrap(probe, text, font, max_w), int((ascent + descent) * LINE_SPACING)


def _line_layer(text: str, font: ImageFont.FreeTypeFont, angle: float) -> Image.Image:
    """Render one line to its own transparent, rotated layer (black fill,
    white stroke) so each line can tilt independently -- Pillow can't rotate
    text drawn directly onto the main canvas."""
    stroke_w = max(2, int(font.size * STROKE_RATIO))
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    bbox = probe.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
    pad = stroke_w + 12
    w = (bbox[2] - bbox[0]) + pad * 2
    h = (bbox[3] - bbox[1]) + pad * 2
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=FILL,
            stroke_width=stroke_w, stroke_fill=STROKE)
    if angle:
        layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
    return layer


def _fill_photo(path: str) -> Image.Image | None:
    """Scale + center-crop ``path`` to exactly WxH. Same fill-crop approach
    as slide_gen._fill_photo, just at the 16:9 thumbnail canvas."""
    try:
        photo = Image.open(path).convert("RGB")
    except (OSError, ValueError) as exc:
        logger.warning("Could not open thumbnail background %s (%s)", path, exc)
        return None
    pw, ph = photo.size
    scale = max(W / pw, H / ph)
    photo = photo.resize((max(1, round(pw * scale)), max(1, round(ph * scale))), Image.LANCZOS)
    sw, sh = photo.size
    left, top = (sw - W) // 2, (sh - H) // 2
    return photo.crop((left, top, left + W, top + H))


def _thumbnail_text(highlights: dict) -> str:
    text = (highlights.get("thumbnail_text") or "").strip()
    if text:
        return text.upper()
    # Old cached plans predate this field -- fall back to a trimmed hook
    # rather than crashing the render.
    hook = (highlights.get("hook") or "").strip()
    words = hook.split()[:5]
    logger.warning("No 'thumbnail_text' in highlights -- falling back to trimmed hook")
    return " ".join(words).upper()


def _generate_background(highlights: dict, basename: str, force: bool) -> str | None:
    scenes = highlights.get("image_scenes") or []
    idx = image_gen.THUMBNAIL_SCENE_INDEX
    scene = scenes[idx] if len(scenes) > idx else (scenes[0] if scenes else {})
    prompt = image_gen.compose_thumbnail_prompt(scene, highlights.get("wolf_outfit", ""))
    fname = f"{basename}{config.THUMBNAIL_BG_SUFFIX}" if basename else f"thumb{config.THUMBNAIL_BG_SUFFIX}"
    paths = image_gen.generate_backgrounds(
        [prompt],
        force=force,
        size=config.OPENAI_THUMBNAIL_IMAGE_SIZE,
        dims=(W, H),
        file_names=[fname],
    )
    return paths[0] if paths else None


def build_thumbnail(highlights: dict, basename: str = "", force: bool = False) -> str:
    """Generate (or reuse the cached) 16:9 thumbnail: a dedicated AI hero
    image with poster-style Pillow text on top. Returns the PNG path."""
    os.makedirs(config.THUMBNAIL_DIR, exist_ok=True)

    bg_path = _generate_background(highlights, basename, force)
    img = _fill_photo(bg_path) if bg_path else None
    if img is None:
        img = Image.new("RGB", (W, H), config.VIDEO_BG_COLOR)
    canvas = img.convert("RGBA")

    text = _thumbnail_text(highlights)
    font, lines, lh = _fit(text, BODY_W, HEADROOM_H - TOP_MARGIN)

    y = TOP_MARGIN
    for i, line in enumerate(lines):
        angle = LINE_ROTATIONS[i % len(LINE_ROTATIONS)]
        layer = _line_layer(line, font, angle)
        x = LEFT + (BODY_W - layer.width) // 2
        canvas.alpha_composite(layer, (max(0, x), y))
        y += lh

    out = canvas.convert("RGB")
    out_name = f"{basename}_thumbnail.png" if basename else "thumbnail.png"
    out_path = os.path.join(config.THUMBNAIL_DIR, out_name)
    out.save(out_path)
    logger.info("Rendered thumbnail: %s (%s)", out_path, "AI hero image" if bg_path else "solid fallback")
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    plans = sorted(glob.glob(os.path.join(config.TMP_DIR, "*.plan.json")), key=os.path.getmtime, reverse=True)
    if not plans:
        raise SystemExit(f"No *.plan.json found in {config.TMP_DIR} - run the pipeline once first.")
    with open(plans[0], encoding="utf-8") as f:
        highlights = json.load(f)["highlights"]
    basename = os.path.basename(plans[0]).removesuffix(".plan.json")

    print(f"Building thumbnail from {os.path.basename(plans[0])} (basename={basename!r})", flush=True)
    path = build_thumbnail(highlights, basename=basename, force=True)

    with Image.open(path) as im:
        print(f"\nThumbnail: {path}  {im.size[0]}x{im.size[1]}  {os.path.getsize(path) // 1024} KB")
