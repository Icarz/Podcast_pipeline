"""Video generation: word-level karaoke captioned vertical clip.

Builds a 1080x1920 MP4 from a window of episode audio, in the style of viral
podcast clip channels:
  - dark (or blurred-image) background
  - word-level karaoke captions: 4-5 words at a time, the currently-spoken
    word highlighted, each group appearing/disappearing on its timestamps
  - the extracted hook across the top for the first few seconds
  - a muted podcast-name watermark bottom-right
"""

import glob
import logging
import os
import re

import numpy as np
from moviepy import AudioFileClip, CompositeVideoClip, ImageClip, TextClip
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

import config

logger = logging.getLogger(__name__)

_BOLD_FONTS = ["arialbd.ttf", "segoeuib.ttf", "calibrib.ttf"]
_FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_IMAGE_EXTS = ("*.jpg", "*.jpeg", "*.png", "*.webp")


def _bold_font_path() -> str:
    for name in _BOLD_FONTS:
        path = os.path.join(_FONTS_DIR, name)
        if os.path.exists(path):
            return path
    raise RuntimeError("No bold TrueType font found for captions")


def _slugify(text: str, max_len: int = 60) -> str:
    text = (text or "clip").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"[^A-Za-z0-9_-]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text[:max_len].rstrip("_")) or "clip"


def _find_asset_image() -> str | None:
    if not os.path.isdir(config.ASSETS_DIR):
        return None
    for pattern in _IMAGE_EXTS:
        matches = sorted(glob.glob(os.path.join(config.ASSETS_DIR, pattern)))
        if matches:
            return matches[0]
    return None


def _make_background(window: float) -> ImageClip:
    """Dark background, or a blurred + darkened asset image if one exists."""
    w, h = config.SLIDE_WIDTH, config.SLIDE_HEIGHT
    asset = _find_asset_image()
    if asset:
        logger.info("Using background image: %s", os.path.basename(asset))
        im = Image.open(asset).convert("RGB")
        scale = max(w / im.width, h / im.height)
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
        left, top = (im.width - w) // 2, (im.height - h) // 2
        im = im.crop((left, top, left + w, top + h))
        im = im.filter(ImageFilter.GaussianBlur(config.BG_BLUR_RADIUS))
        im = ImageEnhance.Brightness(im).enhance(config.BG_DARKEN)
    else:
        im = Image.new("RGB", (w, h), config.VIDEO_BG_COLOR)
    return ImageClip(np.array(im)).with_duration(window)


def _clip_window(start: float, end: float, audio_duration: float) -> tuple[float, float]:
    """Safety clamp so the window always lands inside the audio."""
    window = min(max(end - start, 1.0), audio_duration)
    start = max(0.0, min(start, audio_duration - window))
    return start, start + window


def group_words(words: list, max_n: int = None, gap: float = None) -> list:
    """Chunk words into caption groups of up to ``max_n`` words.

    A new group also starts when the silent gap before a word exceeds ``gap``,
    so groups break on natural pauses.
    """
    max_n = max_n or config.CAPTION_WORDS_PER_GROUP
    gap = config.CAPTION_GROUP_GAP if gap is None else gap

    groups: list[list] = []
    current: list = []
    for w in words:
        if current and (len(current) >= max_n or (w["start"] - current[-1]["end"]) > gap):
            groups.append(current)
            current = []
        current.append(w)
    if current:
        groups.append(current)
    return groups


def _render_block(words_text: list[str], font: ImageFont.FreeTypeFont, highlight_idx: int, max_width: int) -> np.ndarray:
    """Render a word group to a transparent RGBA array, one word highlighted.

    Word-wraps on whole words only (never mid-word). ``highlight_idx`` < 0
    means no highlight (used for the hook).
    """
    sw = config.CAPTION_STROKE_WIDTH
    measure = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    space_w = measure.textlength(" ", font=font)
    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * config.CAPTION_LINE_SPACING)
    widths = [measure.textlength(w, font=font) for w in words_text]

    # Greedy word-wrap into lines of (index, word, width).
    lines: list[list[tuple[int, str, float]]] = []
    current: list[tuple[int, str, float]] = []
    current_w = 0.0
    for i, (word, ww) in enumerate(zip(words_text, widths)):
        add = ww + (space_w if current else 0)
        if current and current_w + add > max_width:
            lines.append(current)
            current, current_w = [], 0.0
            add = ww
        current.append((i, word, ww))
        current_w += add
    if current:
        lines.append(current)

    line_widths = [sum(ww for _, _, ww in ln) + space_w * (len(ln) - 1) for ln in lines]
    pad = sw + 4
    block_w = int(max(line_widths)) + 2 * pad
    block_h = line_h * len(lines) + 2 * pad

    img = Image.new("RGBA", (block_w, block_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = pad
    for ln, lw in zip(lines, line_widths):
        x = (block_w - lw) / 2
        for idx, word, ww in ln:
            color = config.CAPTION_HIGHLIGHT_COLOR if idx == highlight_idx else config.CAPTION_COLOR
            draw.text((x, y), word, font=font, fill=color, stroke_width=sw, stroke_fill=config.CAPTION_STROKE_COLOR)
            x += ww + space_w
        y += line_h
    return np.array(img)


def build_video(audio_path: str, words: list, highlights: dict, podcast_name: str = "") -> str:
    """Render a word-level karaoke clip; return the output MP4 path."""
    if not os.path.exists(audio_path):
        raise FileNotFoundError(audio_path)

    os.makedirs(config.VIDEO_DIR, exist_ok=True)
    font_path = _bold_font_path()
    cap_font = ImageFont.truetype(font_path, config.CAPTION_FONT_SIZE)
    hook_font = ImageFont.truetype(font_path, config.HOOK_FONT_SIZE)
    w, h = config.SLIDE_WIDTH, config.SLIDE_HEIGHT
    margin = config.SLIDE_MARGIN
    max_width = w - 2 * margin

    audio = AudioFileClip(audio_path)
    layers: list = []
    try:
        req_start = float(highlights.get("clip_start", 0))
        req_end = float(highlights.get("clip_end", req_start + config.CLIP_WINDOW_MIN_SECONDS))
        start, end = _clip_window(req_start, req_end, audio.duration)
        if (start, end) != (req_start, req_end):
            logger.warning(
                "Clip %.1f-%.1fs out of range (audio %.1fs); clamped to %.1f-%.1fs",
                req_start, req_end, audio.duration, start, end,
            )
        window = end - start
        clip_audio = audio.subclipped(start, end)
        layers.append(_make_background(window))

        # Word-level karaoke captions.
        in_window = [
            wd for wd in words
            if wd.get("start") is not None and wd.get("end") is not None
            and wd["start"] >= start - 0.05 and wd["end"] <= end + 0.05
        ]
        groups = group_words(in_window)
        cap_y = int(h * config.CAPTION_CENTER_Y)
        n_clips = 0
        for group in groups:
            texts = [g["word"] for g in group]
            group_end_rel = group[-1]["end"] - start
            for i, gw in enumerate(group):
                seg_start = max(0.0, gw["start"] - start)
                seg_end = (group[i + 1]["start"] - start) if i < len(group) - 1 else group_end_rel
                seg_end = min(window, seg_end)
                if seg_end - seg_start <= 0:
                    continue
                arr = _render_block(texts, cap_font, i, max_width)
                clip = (
                    ImageClip(arr, transparent=True)
                    .with_start(seg_start)
                    .with_duration(seg_end - seg_start)
                    .with_position(("center", cap_y - arr.shape[0] // 2))
                )
                layers.append(clip)
                n_clips += 1
        logger.info("Built %d word-caption clips from %d words in %.1fs window", n_clips, len(in_window), window)

        # Hook across the top for the first few seconds.
        hook = (highlights.get("hook") or "").strip()
        if hook:
            arr = _render_block(hook.split(), hook_font, -1, max_width)
            hook_clip = (
                ImageClip(arr, transparent=True)
                .with_start(0)
                .with_duration(min(config.HOOK_DURATION, window))
                .with_position(("center", int(h * config.HOOK_TOP)))
            )
            layers.append(hook_clip)

        # Muted watermark bottom-right.
        if podcast_name:
            wm = TextClip(
                font=font_path,
                text=podcast_name,
                font_size=config.WATERMARK_FONT_SIZE,
                color=config.WATERMARK_COLOR,
            ).with_duration(window)
            ww, wh = wm.size
            wm = wm.with_position((w - margin - ww, h - margin - wh))
            layers.append(wm)

        video = CompositeVideoClip(layers, size=(w, h)).with_audio(clip_audio).with_duration(window)

        out_path = os.path.join(config.VIDEO_DIR, f"{_slugify(highlights.get('title', 'clip'))}.mp4")
        video.write_videofile(
            out_path,
            fps=config.VIDEO_FPS,
            codec=config.VIDEO_CODEC,
            audio_codec=config.AUDIO_CODEC,
            logger=None,
        )
        video.close()
        for layer in layers:
            layer.close()
    finally:
        audio.close()

    logger.info("Wrote video: %s", out_path)
    return out_path


if __name__ == "__main__":
    import subprocess

    import imageio_ffmpeg

    from modules import ai_extract, transcribe

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    mp3s = sorted(glob.glob(os.path.join(config.TMP_DIR, "*.mp3")), key=os.path.getmtime, reverse=True)
    if not mp3s:
        raise SystemExit(f"No MP3 found in {config.TMP_DIR} - run rss_ingest first.")
    audio_path = mp3s[0]

    print(f"Transcribing: {os.path.basename(audio_path)}", flush=True)
    transcript = transcribe.transcribe(audio_path)
    highlights = ai_extract.extract_highlights(transcript)
    start, end = highlights["clip_start"], highlights["clip_end"]
    print(f"Grounded clip window: {start:.2f}-{end:.2f}s ({end - start:.1f}s)", flush=True)

    out = build_video(audio_path, transcript["words"], highlights, podcast_name="The Mindset Mentor")

    # Caption schedule for the first 10 seconds (relative to clip start).
    in_window = [w for w in transcript["words"] if w["start"] >= start - 0.05 and w["end"] <= end + 0.05]
    groups = group_words(in_window)
    print("\n=== Word-caption schedule, first 10s (rel times; * = group boundary) ===")
    for gi, group in enumerate(groups):
        if (group[0]["start"] - start) > 10:
            break
        grp_txt = " ".join(g["word"] for g in group)[:48].encode("ascii", "replace").decode("ascii")
        print(f"  group {gi}: \"{grp_txt}\"")
        for gw in group:
            r0, r1 = gw["start"] - start, gw["end"] - start
            if r0 > 10:
                break
            tok = gw["word"].encode("ascii", "replace").decode("ascii")
            print(f"      {r0:5.2f}-{r1:5.2f}s  {tok}")

    # Pick a frame time ~4s in and report which word should be highlighted.
    t = 4.0
    active = next((wd for wd in in_window if (wd["start"] - start) <= t <= (wd["end"] - start)), None)
    if active is None:
        active = min(in_window, key=lambda wd: abs((wd["start"] - start) - t))
        t = (active["start"] + active["end"]) / 2 - start
    hl = active["word"].encode("ascii", "replace").decode("ascii")
    print(f"\nSample frame at t={t:.2f}s -> highlighted word should be: '{hl}'", flush=True)

    frame_path = os.path.join(config.OUTPUT_DIR, "frame_karaoke.png")
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ff, "-y", "-ss", str(t), "-i", out, "-frames:v", "1", frame_path],
                   check=True, capture_output=True)
    print(f"Saved frame: {frame_path}", flush=True)

    print(f"\nVideo: {out}  ({os.path.getsize(out) / (1024 * 1024):.2f} MB)", flush=True)
