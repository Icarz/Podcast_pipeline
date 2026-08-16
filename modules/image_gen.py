"""AI background generation via OpenAI's gpt-image-2 (primary background source).

Takes the structured ``image_scenes`` produced by :mod:`ai_extract` (composed
into final prompts here via the locked ``STYLE_BLOCK`` template — see
:func:`compose_prompts`; old cached plans' raw ``image_prompts`` still work),
generates one image per prompt via the OpenAI Images API, and writes them to
``tmp/bg_<n>.png`` for :mod:`video_gen` to use as themed video backgrounds. Motion is added at render
time by ``video_gen._image_background_layers`` (Ken Burns pan/zoom) — this
module only produces the still frames.

Resilience (so the pipeline never crashes mid-run):
  * exponential-backoff retry on 429 / 5xx,
  * a local gradient fallback when the API key is missing or every retry fails.

Simple per-file caching, namespaced per episode: a prompt whose
``tmp/<episode-basename>_bg_<n>.png`` already exists is reused (pass
``force=True`` to regenerate). Omitting ``basename`` falls back to the bare
``tmp/bg_<n>.png`` name used by this module's own smoke-test harness below.
"""

import base64
import io
import logging
import os
import time

import numpy as np
import requests
from dotenv import load_dotenv
from PIL import Image

import config

load_dotenv()

logger = logging.getLogger(__name__)

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"

# --- Locked visual identity (single source of truth for the brand style) ----
# ai_extract emits per-scene CONTENT only (image_scenes: beat/concept/action/
# setting/camera, plus one per-clip wolf_outfit); the STYLE below is appended
# verbatim by compose_prompts(), so it can never drift with model paraphrasing.
# Edit the look HERE — no extraction prompt change, no re-extraction needed.
STYLE_BLOCK = (
    "Vintage halftone comic-book illustration: flat colors, visible halftone "
    "dot shading, bold black ink linework, subtle paper-grain texture. Not "
    "photorealistic, not 3D-rendered, not painterly. Warm vibrant palette — "
    "saturated mustard gold, terracotta rust orange, vivid kelly green, cream "
    "and tan, warm brown wood — like sunlit poster art. Bright, warm, sunlit "
    "or golden-hour daylight; cheerful and alive, never dark, gloomy, or "
    "night-dominant."
)

# Story-arc mood per beat (see config.IMAGE_SCENE_BEATS). Tension is allowed in
# the early beats but ALWAYS upright/determined and brightly lit — the arc
# lives in posture and scene, never in gloom (the dark 07-29 redesign was
# rejected; don't reintroduce it here).
BEAT_MOODS = {
    "problem": (
        "focused and confronting the problem head-on, upright and determined, "
        "never defeated"
    ),
    "stakes": (
        "alert, feeling the weight of the moment, jaw set, still upright and "
        "in full warm light"
    ),
    "reframe": (
        "struck by a moment of realization, posture opening up, light "
        "breaking wider across the scene"
    ),
    "payoff": "calm, resolved and quietly triumphant, at ease in warm light",
}

# Used when a beat is unrecognized (old/hand-edited plans).
_DEFAULT_MOOD = BEAT_MOODS["payoff"]

DEFAULT_OUTFIT = "a mustard-yellow hoodie, dark jeans and clean white sneakers"

# Text ON a contextual prop (a list, note, sign named by the scene's action)
# is allowed since 2026-07-31 — user decision; the scene spec supplies the
# exact words. Everything else stays banned.
NEGATIVE_BLOCK = (
    "Any readable text appears ONLY on the prop described in the scene, with "
    "exactly the wording given there, spelled correctly, small and naturally "
    "part of the object — never floating typography, captions, titles, "
    "watermarks, or decorative lettering, and no other text anywhere else in "
    "the image — including no invented numbers, balances, notifications, or "
    "app UI on any screen (phone, laptop, tablet, smartwatch), even if the "
    "scene has the wolf checking one; screens stay angled away, dim, or off. "
    "When that prop text is present, it must render correctly spelled and "
    "unambiguous, not garbled, reversed, or gibberish — but its orientation "
    "follows the scene's own physical logic: if the wolf is depicted reading "
    "or writing it, the readable side faces the wolf, NOT the camera (a "
    "screen or page cannot face both the reader and the viewer at once); "
    "only a prop nobody in the scene is reading may face the camera. No "
    "skulls or death imagery, no cigarettes, alcohol, drugs, or vices, no "
    "slumped or defeated posture, no violence or gore. No humans, no other "
    "animals, no second wolf — the wolf is the only figure (a busy street or "
    "market as a soft anonymous backdrop is fine, with no other clearly "
    "rendered figure). The wolf's design has NO visible tail in any scene — "
    "fully human proportions and silhouette below the neck, wolf head and "
    "hands only."
)


def compose_prompts(scenes: list[dict], outfit: str = "") -> list[str]:
    """Assemble the final gpt-image-2 prompts from ai_extract's scene specs.

    One prompt per scene: locked ``STYLE_BLOCK`` + the wolf character wearing
    the clip's single locked ``outfit`` + the scene's content fields + the
    beat's arc mood + ``NEGATIVE_BLOCK``. The scenes follow the clip's story
    arc (config.IMAGE_SCENE_BEATS: problem x2 -> stakes -> reframe ->
    payoff x2), so the images read as one character's visual story rather
    than unrelated wallpapers.
    """
    outfit = (outfit or "").strip() or DEFAULT_OUTFIT
    prompts: list[str] = []
    for scene in scenes:
        beat = str(scene.get("beat") or "").strip().lower()
        mood = BEAT_MOODS.get(beat, _DEFAULT_MOOD)
        action = str(scene.get("action") or "").strip().rstrip(".")
        setting = str(scene.get("setting") or "").strip().rstrip(".")
        camera = str(scene.get("camera") or "").strip().rstrip(".") \
            or "dynamic three-quarter view with strong depth"
        concept = str(scene.get("concept") or "").strip().rstrip(".")
        prompts.append(
            f"{STYLE_BLOCK} "
            "Subject: exactly one anthropomorphic wolf character — upright "
            "human posture and proportions, wolf head and facial features, "
            "a serious, composed, dignified expression (warm and confident, "
            "but NEVER a wide cartoon grin or a soft doe-eyed 'cute' look), "
            f"wearing {outfit} (the same outfit in every image of this set). "
            f"The wolf is {action}, in {setting}. "
            f"Camera: {camera}. "
            f"Mood: {mood}. "
            f"The scene is a literal visual stand-in for this idea: {concept}. "
            f"{NEGATIVE_BLOCK}"
        )
    return prompts


# Waits (seconds) between retries after a 429/5xx: up to 4 retries.
RETRY_BACKOFFS = [2, 4, 8, 16]

# Diagonal-gradient palettes (top-left -> bottom-right) for the local fallback.
_FALLBACK_PALETTES = [
    ((180, 90, 40), (20, 20, 40)),
    ((30, 80, 120), (10, 10, 20)),
    ((120, 40, 60), (15, 15, 15)),
    ((40, 110, 90), (10, 15, 25)),
]


def _api_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY")


def _is_retryable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _generate_one(api_key: str, prompt: str, size: str = "") -> bytes:
    """Single OpenAI Images API call; raises on error, returns decoded PNG bytes."""
    response = requests.post(
        OPENAI_IMAGES_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": config.OPENAI_IMAGE_MODEL,
            "prompt": prompt,
            "size": size or config.OPENAI_IMAGE_SIZE,
            "quality": config.OPENAI_IMAGE_QUALITY,
            "n": 1,
        },
        timeout=config.OPENAI_IMAGE_TIMEOUT,
    )
    if not response.ok:
        exc = RuntimeError(f"OpenAI images API {response.status_code}: {response.text[:300]}")
        exc.status_code = response.status_code  # type: ignore[attr-defined]
        raise exc
    b64 = response.json()["data"][0]["b64_json"]
    return base64.b64decode(b64)


def _generate_with_retry(api_key: str, prompt: str, idx: int, size: str = "") -> bytes | None:
    """Retry with backoff on 429/5xx. Returns image bytes, or None if exhausted."""
    for attempt in range(len(RETRY_BACKOFFS) + 1):
        try:
            image_bytes = _generate_one(api_key, prompt, size=size)
            logger.info("Background %d generated with %s", idx, config.OPENAI_IMAGE_MODEL)
            return image_bytes
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            if status is not None and _is_retryable(status) and attempt < len(RETRY_BACKOFFS):
                wait = RETRY_BACKOFFS[attempt]
                logger.warning(
                    "Background %d: %s error %s, retry in %ds (%d/%d)",
                    idx, config.OPENAI_IMAGE_MODEL, status, wait, attempt + 1, len(RETRY_BACKOFFS),
                )
                time.sleep(wait)
                continue
            logger.warning("Background %d: %s failed (%s)", idx, config.OPENAI_IMAGE_MODEL, exc)
            return None
    return None


def _write_fallback(path: str, idx: int, dims: tuple[int, int] | None = None) -> None:
    """Write a darkened diagonal-gradient PNG as a local background fallback."""
    w, h = dims or (config.SLIDE_WIDTH, config.SLIDE_HEIGHT)
    c0, c1 = _FALLBACK_PALETTES[(idx - 1) % len(_FALLBACK_PALETTES)]
    yy, xx = np.mgrid[0:h, 0:w]
    d = (xx / w + yy / h) / 2.0
    arr = np.zeros((h, w, 3), np.uint8)
    for k in range(3):
        arr[..., k] = (c0[k] * (1 - d) + c1[k] * d).astype(np.uint8)
    Image.fromarray(arr).save(path, "PNG")


def _save_png(image_bytes: bytes, path: str) -> None:
    """Decode model bytes and write a normalized RGB PNG."""
    im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    im.save(path, "PNG")


def generate_backgrounds(
    prompts: list[str], out_dir: str = None, basename: str = None, force: bool = False,
    size: str = "", dims: tuple[int, int] | None = None, file_names: list[str] | None = None,
) -> list[str]:
    """Generate one background PNG per prompt; return the list of file paths.

    Files are written to ``out_dir/<basename>_bg_<n>.png`` (1-indexed), or bare
    ``out_dir/bg_<n>.png`` when ``basename`` is omitted -- unless
    ``file_names`` is given, in which case those exact filenames are used
    (1:1 with ``prompts``; for one-off images that don't fit the ``_bg_<n>``
    numbering). Existing files are reused unless ``force`` is True. **Always
    pass the episode's audio basename from pipeline callers** — without it
    every episode collides on the same filenames and silently reuses a
    previous episode's images regardless of this run's ``image_prompts``
    (the per-clip art direction would never actually take effect). If a
    prompt can't be generated (missing key, rate limit, error), a local
    gradient fallback is written so the pipeline always has a full set of
    backgrounds and never crashes.

    ``size``/``dims``: override the OpenAI request size and the local
    gradient-fallback pixel dimensions together -- default to
    ``config.OPENAI_IMAGE_SIZE`` / ``(SLIDE_WIDTH, SLIDE_HEIGHT)`` when
    omitted, so existing callers are unaffected.
    """
    out_dir = out_dir or config.TMP_DIR
    os.makedirs(out_dir, exist_ok=True)

    api_key = _api_key()
    if not api_key:
        logger.warning("OPENAI_API_KEY is not set - using gradient fallback for all backgrounds")

    prefix = f"{basename}_{config.BG_IMAGE_PREFIX}" if basename else config.BG_IMAGE_PREFIX

    paths: list[str] = []
    used_fallback = False

    for i, prompt in enumerate(prompts, start=1):
        fname = file_names[i - 1] if file_names else f"{prefix}{i}.png"
        path = os.path.join(out_dir, fname)

        if not force and os.path.exists(path) and os.path.getsize(path) > 0:
            logger.info("Background cached, skipping generation: %s", os.path.basename(path))
            paths.append(path)
            continue

        image_bytes = _generate_with_retry(api_key, prompt, i, size=size) if api_key else None

        if image_bytes:
            _save_png(image_bytes, path)
            logger.info("Wrote background: %s (gpt-image-2)", os.path.basename(path))
        else:
            used_fallback = True
            _write_fallback(path, i, dims=dims)
            logger.info("Wrote fallback background: %s", os.path.basename(path))

        paths.append(path)

    if used_fallback:
        logger.warning("%s unavailable for one or more prompts - gradient fallback used", config.OPENAI_IMAGE_MODEL)

    return paths


if __name__ == "__main__":
    import glob
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Load the image scenes/prompts from the most recent cached extraction JSON
    # so we don't spend Groq/Claude credits re-running the upstream steps.
    plans = sorted(glob.glob(os.path.join(config.TMP_DIR, "*.plan.json")), key=os.path.getmtime, reverse=True)
    if not plans:
        raise SystemExit(f"No *.plan.json found in {config.TMP_DIR} - run the pipeline once first.")
    with open(plans[0], encoding="utf-8") as f:
        highlights = json.load(f)["highlights"]
    scenes = highlights.get("image_scenes")
    if scenes:
        prompts = compose_prompts(scenes, highlights.get("wolf_outfit", ""))
        print(f"Composed {len(prompts)} prompts from image_scenes in {os.path.basename(plans[0])}\n", flush=True)
    else:
        prompts = highlights["image_prompts"]  # pre-2026-07-31 cached plan
        print(f"Loaded {len(prompts)} legacy image_prompts from {os.path.basename(plans[0])}\n", flush=True)

    # force=True to actually exercise the API / retry / fallback path this run.
    out = generate_backgrounds(prompts, force=True)
    print(f"\nGenerated/fell back to {len(out)} background(s):")
    for p in out:
        size = os.path.getsize(p) if os.path.exists(p) else 0
        print(f"  {p}  ({size / 1024:.0f} KB)")
