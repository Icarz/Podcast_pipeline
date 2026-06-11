"""Post-fetch quality gate for Pexels background candidates.

The SYSTEM_PROMPT can *ask* for on-brand footage, but Pexels routinely returns a
clip that contradicts the query (a "sunlit hallway" query yields a dark
corridor; a "teacher" query yields a classroom full of children; an "open book"
query yields legible page text). The prompt can't see what actually came back —
this module can.

For a candidate Pexels ``video`` object we inspect its preview poster frames
(the ``video_pictures`` thumbnails — NO full clip download) and reject footage
that fails any of three checks tuned to the brand's NEVER-DEPICT rules:

  * BRIGHTNESS — median frame luma below ``config.BG_BRIGHTNESS_MIN`` (too
    dark / dim / off the warm palette). numpy-only, always runs.
  * FACES      — any frame with a face bigger than ``config.BG_FACE_AREA_MAX``
    of the frame (close-up / identifiable subject) OR more than
    ``config.BG_FACE_COUNT_MAX`` faces (group / crowd / classroom). Needs cv2.
  * TEXT       — text-like regions covering more than
    ``config.BG_TEXT_COVER_MAX`` of the frame (signage / flipchart / book
    pages / captioned screens). Heuristic (gradient + morphology), needs cv2.

opencv (``opencv-python-headless``) is OPTIONAL: if it isn't importable the gate
degrades to the brightness check alone and logs once, so the pipeline never
hard-breaks. Network/parse hiccups fetching a poster are treated as a PASS
(degrade-never — a transient error must not starve a slot).
"""

import io
import logging

import numpy as np
import requests
from PIL import Image

import config

logger = logging.getLogger(__name__)

try:
    import cv2

    _FACE_CASCADE = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    _HAS_CV2 = not _FACE_CASCADE.empty()
except Exception:  # noqa: BLE001 - any import/load failure -> brightness-only
    cv2 = None
    _FACE_CASCADE = None
    _HAS_CV2 = False

if not _HAS_CV2:
    logger.info(
        "bg_quality: opencv unavailable; quality gate runs BRIGHTNESS-only "
        "(no face/text checks). Install opencv-python-headless to enable them."
    )


def _poster_urls(video: dict, max_frames: int) -> list[str]:
    """Evenly-sampled preview-frame URLs for a Pexels ``video`` (<= max_frames)."""
    pics = [
        p.get("picture")
        for p in (video.get("video_pictures") or [])
        if isinstance(p, dict) and p.get("picture")
    ]
    if not pics:
        img = video.get("image")
        pics = [img] if img else []
    if len(pics) > max_frames > 0:
        step = len(pics) / max_frames
        pics = [pics[int(i * step)] for i in range(max_frames)]
    return pics


def _fetch_rgb(url: str) -> np.ndarray | None:
    try:
        resp = requests.get(url, timeout=config.PEXELS_TIMEOUT)
        resp.raise_for_status()
        return np.asarray(Image.open(io.BytesIO(resp.content)).convert("RGB"))
    except (requests.RequestException, OSError, ValueError) as exc:
        logger.debug("bg_quality: poster fetch failed (%s); treating as pass", exc)
        return None


def _luma(rgb: np.ndarray) -> float:
    """Mean perceived brightness 0-255 (Rec.601 luma)."""
    weights = np.array([0.299, 0.587, 0.114], dtype=np.float32)
    return float((rgb[..., :3].astype(np.float32) * weights).sum(axis=-1).mean())


def _gray(rgb: np.ndarray) -> "np.ndarray":
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def _face_metrics(gray: np.ndarray) -> tuple[int, float]:
    """(face_count, largest_face_area_fraction) via Haar frontal-face cascade."""
    if not _HAS_CV2:
        return 0, 0.0
    h, w = gray.shape[:2]
    faces = _FACE_CASCADE.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=6, minSize=(max(20, w // 24), max(20, h // 24))
    )
    if len(faces) == 0:
        return 0, 0.0
    frame_area = float(w * h)
    largest = max((fw * fh) / frame_area for (_x, _y, fw, fh) in faces)
    return len(faces), largest


def _text_coverage(gray: np.ndarray) -> float:
    """Fraction of the frame occupied by text-like regions (heuristic).

    Horizontal-gradient response + morphological close into blocks, then keep
    only wide-but-short, mid-sized components (the signature of a line of text)
    and sum their area. Deliberately conservative — it is meant to catch dense,
    legible blocks (signage, flipcharts, book pages, captioned screens), not to
    OCR. High-frequency natural texture (foliage, gravel) is mostly rejected by
    the aspect/size filters.
    """
    if not _HAS_CV2:
        return 0.0
    h, w = gray.shape[:2]
    frame_area = float(w * h)
    # Emphasize horizontal stroke edges (text has strong vertical-edge density).
    grad = cv2.Sobel(gray, cv2.CV_8U, 1, 0, ksize=3)
    _t, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    # Close along the writing direction so glyphs merge into line-blobs.
    kern = cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, w // 40), max(3, h // 200)))
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kern)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # A real block of legible text is SEVERAL short, wide line-strips stacked up —
    # not one giant band. Keep only text-line-shaped components (wide vs tall, but
    # NOT spanning the frame like a horizon ridge, and short like a line of type),
    # then require at least a few of them before counting any coverage. This kills
    # the natural-scene false positives (horizons, foliage, gravel) that a raw
    # gradient count produced.
    lines = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if ch == 0:
            continue
        aspect = cw / ch
        frac = (cw * ch) / frame_area
        if (aspect >= 2.5
                and w * 0.06 <= cw <= w * 0.70      # wider than a word, not a full-width edge
                and ch <= h * 0.10                  # a text LINE is short
                and 0.0005 <= frac <= 0.06):        # not a speck, not a giant slab
            lines.append((x, y, cw, ch))
    if len(lines) < 3:
        return 0.0
    return sum(cw * ch for (_x, _y, cw, ch) in lines) / frame_area


def assess(video: dict) -> tuple[bool, str | None, dict]:
    """Inspect a Pexels ``video``'s poster frames; return (ok, reason, metrics).

    ``ok`` is False with a short ``reason`` when the clip should be skipped.
    Never raises — on any trouble it returns ok=True (degrade-never). ``metrics``
    is a small dict for logging/tuning.
    """
    metrics: dict = {"frames": 0, "luma_median": None, "faces_max": 0,
                     "face_area_max": 0.0, "text_cover_max": 0.0}
    if not config.BG_QUALITY_ENABLED:
        return True, None, metrics

    urls = _poster_urls(video, config.BG_QUALITY_FRAMES)
    lumas: list[float] = []
    faces_max = 0
    face_area_max = 0.0
    text_cover_max = 0.0
    for url in urls:
        rgb = _fetch_rgb(url)
        if rgb is None or rgb.ndim != 3:
            continue
        lumas.append(_luma(rgb))
        if _HAS_CV2:
            g = _gray(rgb)
            n_faces, area = _face_metrics(g)
            faces_max = max(faces_max, n_faces)
            face_area_max = max(face_area_max, area)
            text_cover_max = max(text_cover_max, _text_coverage(g))

    if not lumas:
        return True, None, metrics  # couldn't see anything -> don't block

    luma_median = float(np.median(lumas))
    metrics.update(frames=len(lumas), luma_median=round(luma_median, 1),
                   faces_max=faces_max, face_area_max=round(face_area_max, 4),
                   text_cover_max=round(text_cover_max, 4))

    if luma_median < config.BG_BRIGHTNESS_MIN:
        return False, f"dark(luma={luma_median:.0f}<{config.BG_BRIGHTNESS_MIN})", metrics
    if faces_max > config.BG_FACE_COUNT_MAX:
        return False, f"group({faces_max} faces)", metrics
    if face_area_max > config.BG_FACE_AREA_MAX:
        return False, f"face-closeup(area={face_area_max:.2f})", metrics
    if text_cover_max > config.BG_TEXT_COVER_MAX:
        return False, f"text(cover={text_cover_max:.3f})", metrics
    return True, None, metrics
