"""Video generation: word-level karaoke captioned vertical clip.

Builds a 1080x1920 MP4 from a window of episode audio, in the style of viral
podcast clip channels:
  - dark (or blurred-image) background
  - word-level karaoke captions: 4-5 words at a time, the currently-spoken
    word highlighted, each group appearing/disappearing on its timestamps
  - a podcast-name watermark bottom-right (white on a dark pill)
"""

import glob
import logging
import os
import re

import numpy as np
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    afx,
    vfx,
)
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


def _cover_image(im: Image.Image, w: int, h: int) -> Image.Image:
    """Scale + center-crop ``im`` to exactly ``w``x``h`` (cover fit)."""
    scale = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    left, top = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((left, top, left + w, top + h))


def _ken_burns_clip(arr: np.ndarray, duration: float, pan_x: float, pan_y: float) -> ImageClip:
    """A slow zoom + pan ("Ken Burns") clip from a frame-sized RGB array.

    The scale stays > 1 throughout so the panned/zoomed image always overfills
    the frame and never reveals an edge.
    """
    w, h = config.SLIDE_WIDTH, config.SLIDE_HEIGHT
    z0, z1 = config.BG_KENBURNS_ZOOM_FROM, config.BG_KENBURNS_ZOOM_TO

    def scale(t: float) -> float:
        p = (t / duration) if duration else 0.0
        return z0 + (z1 - z0) * p

    def pos(t: float):
        p = (t / duration) if duration else 0.0
        s = scale(t)
        # Center the (scaled) image, then drift it across by +/- the pan amount.
        x = (w - w * s) / 2 + pan_x * (p - 0.5) * 2
        y = (h - h * s) / 2 + pan_y * (p - 0.5) * 2
        return (x, y)

    return ImageClip(arr).with_duration(duration).resized(scale).with_position(pos)


def _slot_layout(window: float, n: int) -> tuple[float, float]:
    """Return (seg, xfade): per-clip length and crossfade so ``n`` clips tile
    ``window`` exactly when overlapped by ``xfade``."""
    xfade = min(config.BG_CROSSFADE, window / max(n, 1))
    seg = (window + xfade * (n - 1)) / n
    return seg, xfade


def _overlay_layer(window: float) -> ColorClip:
    """50% black overlay so captions stay readable over any background."""
    return (
        ColorClip(size=(config.SLIDE_WIDTH, config.SLIDE_HEIGHT), color=(0, 0, 0))
        .with_opacity(config.BG_OVERLAY_OPACITY)
        .with_duration(window)
    )


def _image_background_layers(window: float, image_paths: list[str]) -> list:
    """4 Ken Burns (slow zoom + pan) images, crossfaded across the window."""
    w, h = config.SLIDE_WIDTH, config.SLIDE_HEIGHT
    n = len(image_paths)
    seg, xfade = _slot_layout(window, n)

    layers: list = []
    for i, path in enumerate(image_paths):
        arr = np.array(_cover_image(Image.open(path).convert("RGB"), w, h))
        sign = 1 if i % 2 == 0 else -1  # alternate pan direction for variety
        pan = config.BG_KENBURNS_PAN * sign
        clip = _ken_burns_clip(arr, seg, pan, pan).with_start(i * (seg - xfade))
        if i > 0:
            clip = clip.with_effects([vfx.CrossFadeIn(xfade)])
        layers.append(clip)
    logger.info("Built %d-image Ken Burns background (%.1fs each, %.1fs crossfade)", n, seg, xfade)
    return layers


def _video_background_layers(window: float, video_paths: list[str]) -> list:
    """Stock video clips looped/trimmed to their slot, cover-cropped, crossfaded."""
    w, h = config.SLIDE_WIDTH, config.SLIDE_HEIGHT
    n = len(video_paths)
    seg, xfade = _slot_layout(window, n)

    layers: list = []
    for i, path in enumerate(video_paths):
        clip = VideoFileClip(path).without_audio()
        scale = max(w / clip.w, h / clip.h)               # cover-fit
        clip = clip.resized(scale)
        clip = clip.cropped(width=w, height=h, x_center=clip.w / 2, y_center=clip.h / 2)
        clip = clip.with_effects([vfx.Loop(duration=seg)])  # loop short / trim long
        clip = clip.with_start(i * (seg - xfade))
        if i > 0:
            clip = clip.with_effects([vfx.CrossFadeIn(xfade)])
        layers.append(clip)
    logger.info("Built %d-video background (%.1fs each, %.1fs crossfade)", n, seg, xfade)
    return layers


def _background_layers(window: float, paths: list[str]) -> list:
    """Dispatch to video or image backgrounds, then add the darkening overlay.

    All paths are assumed homogeneous (the selection chain returns either all
    .mp4 or all image files).
    """
    is_video = bool(paths) and str(paths[0]).lower().endswith(".mp4")
    layers = (
        _video_background_layers(window, paths)
        if is_video
        else _image_background_layers(window, paths)
    )
    layers.append(_overlay_layer(window))
    return layers


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


def _music_track(window: float):
    """The fixed background-music bed for ``window`` seconds, or None.

    Loops/trims the single track at ``config.MUSIC_PATH`` to the clip length,
    drops it to ``MUSIC_GAIN_DB`` below the voice, and adds fade in/out. Returns
    None (voice-only) when the file is absent so the pipeline never crashes.
    """
    path = config.MUSIC_PATH
    if not os.path.exists(path):
        logger.warning("No background music at %s; using voice only", path)
        return None
    factor = 10 ** (config.MUSIC_GAIN_DB / 20.0)  # dB -> linear gain
    # AudioLoop fills the window: it repeats a short track, then with_duration's
    # to the window (so a longer track is trimmed) -> handles both loop & trim.
    music = AudioFileClip(path).with_effects([
        afx.AudioLoop(duration=window),
        afx.MultiplyVolume(factor),               # quiet bed under the speech
        afx.AudioFadeIn(config.MUSIC_FADE_IN),
        afx.AudioFadeOut(config.MUSIC_FADE_OUT),
    ])
    logger.info("Background music: %s at %+ddB (x%.4f), %.0fs", os.path.basename(path),
                config.MUSIC_GAIN_DB, factor, window)
    return music


def _render_watermark(text: str, font: ImageFont.FreeTypeFont) -> np.ndarray:
    """Render the podcast-name watermark as solid white text on a semi-transparent
    dark rounded pill, to a transparent RGBA array. The pill guarantees contrast
    on any background while keeping the mark small and subtle."""
    measure = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    l, t, r, b = measure.textbbox((0, 0), text, font=font)
    text_w, text_h = r - l, b - t
    pad_x, pad_y = config.WATERMARK_PILL_PAD_X, config.WATERMARK_PILL_PAD_Y

    pill_w = text_w + 2 * pad_x
    pill_h = text_h + 2 * pad_y
    img = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    alpha = int(round(config.WATERMARK_PILL_OPACITY * 255))
    draw.rounded_rectangle(
        (0, 0, pill_w - 1, pill_h - 1),
        radius=config.WATERMARK_PILL_RADIUS,
        fill=(*config.WATERMARK_PILL_COLOR, alpha),
    )
    # textbbox offset (l, t) accounts for the font's internal padding so the
    # glyphs sit centered in the pill.
    draw.text((pad_x - l, pad_y - t), text, font=font, fill=config.WATERMARK_COLOR)
    return np.array(img)


def build_video(
    audio_path: str,
    words: list,
    highlights: dict,
    podcast_name: str = "",
    background_images: list[str] | None = None,
) -> str:
    """Render a word-level karaoke clip; return the output MP4 path.

    If ``background_images`` is given, they are sequenced across the clip with a
    slow Ken Burns effect, crossfaded, and darkened 50%. Otherwise the dark /
    blurred-asset background is used.
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(audio_path)

    os.makedirs(config.VIDEO_DIR, exist_ok=True)
    font_path = _bold_font_path()
    cap_font = ImageFont.truetype(font_path, config.CAPTION_FONT_SIZE)
    wm_font = ImageFont.truetype(font_path, config.WATERMARK_FONT_SIZE)
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
        if background_images:
            layers.extend(_background_layers(window, background_images))
        else:
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

        # Watermark bottom-right: solid white on a dark pill so it reads on any bg.
        if podcast_name:
            arr = _render_watermark(podcast_name, wm_font)
            wh, ww = arr.shape[0], arr.shape[1]
            # Bottom edge of the pill at WATERMARK_BASELINE_Y (clear padding
            # below); clamp x so the full pill never runs off the left.
            wm_x = max(margin, w - margin - ww)
            wm_y = config.WATERMARK_BASELINE_Y - wh
            wm = (
                ImageClip(arr, transparent=True)
                .with_duration(window)
                .with_position((wm_x, wm_y))
            )
            layers.append(wm)

        # Mix the fixed background music under the (full-volume) voice. The
        # music is looped/trimmed to the window, quieted, and faded; absent
        # music falls through to voice-only.
        music = _music_track(window)
        if music is not None:
            final_audio = CompositeAudioClip([clip_audio, music]).with_duration(window)
        else:
            final_audio = clip_audio

        video = CompositeVideoClip(layers, size=(w, h)).with_audio(final_audio).with_duration(window)

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
        if music is not None:
            music.close()
    finally:
        audio.close()

    logger.info("Wrote video: %s", out_path)
    return out_path


if __name__ == "__main__":
    import json
    import subprocess

    import imageio_ffmpeg

    from modules import ai_extract, background, transcribe

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    mp3s = sorted(glob.glob(os.path.join(config.TMP_DIR, "*.mp3")), key=os.path.getmtime, reverse=True)
    if not mp3s:
        raise SystemExit(f"No MP3 found in {config.TMP_DIR} - run rss_ingest first.")
    audio_path = mp3s[0]

    # Cache the transcript + clip plan next to the MP3 so re-runs don't re-hit
    # the Groq/Claude APIs (the "cached episode" workflow).
    cache_path = os.path.splitext(audio_path)[0] + ".plan.json"
    if os.path.exists(cache_path):
        print(f"Loading cached transcript + plan: {os.path.basename(cache_path)}", flush=True)
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        transcript, highlights = cached["transcript"], cached["highlights"]
    else:
        print(f"Transcribing: {os.path.basename(audio_path)}", flush=True)
        transcript = transcribe.transcribe(audio_path)
        highlights = ai_extract.extract_highlights(transcript)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"transcript": transcript, "highlights": highlights}, f)

    # Re-run JUST the extraction (no Groq) if the cached plan predates a schema
    # field we now need (e.g. search_queries).
    if "search_queries" not in highlights:
        print("Cached plan missing search_queries; regenerating extraction...", flush=True)
        highlights = ai_extract.extract_highlights(transcript)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"transcript": transcript, "highlights": highlights}, f)

    start, end = highlights["clip_start"], highlights["clip_end"]
    print(f"Grounded clip window: {start:.2f}-{end:.2f}s ({end - start:.1f}s)", flush=True)

    print("\n=== search_queries ===")
    for i, q in enumerate(highlights.get("search_queries", []), 1):
        print(f"  {i}. {q.encode('ascii', 'replace').decode('ascii')}")

    # Ordered chain: Pexels video -> Gemini image -> gradient.
    backgrounds = background.select_backgrounds(highlights)
    src = "Pexels video" if backgrounds[0].lower().endswith(".mp4") else "Gemini/gradient image"
    print(f"\nBackground source: {src}")
    print(f"Backgrounds: {len(backgrounds)} -> {[os.path.basename(b) for b in backgrounds]}", flush=True)

    out = build_video(
        audio_path, transcript["words"], highlights,
        podcast_name="The Mindset Mentor", background_images=backgrounds,
    )

    # Save 3 sample frames spread across the clip so each background is visible.
    window = end - start
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    sample_times = [window * 0.15, window * 0.5, window * 0.85]
    print("\n=== Sample frames ===", flush=True)
    for i, t in enumerate(sample_times, 1):
        frame_path = os.path.join(config.OUTPUT_DIR, f"frame_bg_{i}.png")
        subprocess.run([ff, "-y", "-ss", f"{t:.2f}", "-i", out, "-frames:v", "1", frame_path],
                       check=True, capture_output=True)
        print(f"  t={t:5.2f}s -> {frame_path}", flush=True)

    print(f"\nVideo: {out}  ({os.path.getsize(out) / (1024 * 1024):.2f} MB)", flush=True)
