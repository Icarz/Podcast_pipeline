"""AI background generation via Gemini image models ("Nano Banana").

Takes the ``image_prompts`` produced by :mod:`ai_extract`, generates one image
per prompt with the google-genai SDK, and writes them to ``tmp/bg_<n>.png`` for
:mod:`video_gen` to use as themed video backgrounds.

Resilience (so the pipeline never crashes mid-run):
  * exponential-backoff retry on 429 / RESOURCE_EXHAUSTED,
  * a model fallback chain (``IMAGE_MODELS``),
  * a local gradient fallback when every model/retry is exhausted.

Simple per-file caching: a prompt whose ``tmp/bg_<n>.png`` already exists is
reused (pass ``force=True`` to regenerate).
"""

import io
import logging
import os
import time

import numpy as np
from dotenv import load_dotenv
from PIL import Image

import config

load_dotenv()

logger = logging.getLogger(__name__)

# Image models tried in order; we move to the next on a 429 or any error.
# Edit this list to change the fallback chain.
IMAGE_MODELS = [
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image-preview",
]

# Waits (seconds) between retries after a 429 on a single model: up to 4 retries.
RETRY_BACKOFFS = [2, 4, 8, 16]

# Diagonal-gradient palettes (top-left -> bottom-right) for the local fallback.
_FALLBACK_PALETTES = [
    ((180, 90, 40), (20, 20, 40)),
    ((30, 80, 120), (10, 10, 20)),
    ((120, 40, 60), (15, 15, 15)),
    ((40, 110, 90), (10, 15, 25)),
]


def _client():
    """Build a google-genai client from GEMINI_API_KEY (lazy import)."""
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set (check your .env file)")
    return genai.Client(api_key=api_key)


def _generate_config():
    """Request a single 9:16 image; degrade gracefully on older SDK versions."""
    from google.genai import types

    try:
        return types.GenerateContentConfig(
            response_modalities=["Image"],
            image_config=types.ImageConfig(aspect_ratio=config.IMAGE_ASPECT_RATIO),
        )
    except (TypeError, AttributeError):
        # Older SDKs lack ImageConfig/aspect_ratio — fall back to defaults.
        return types.GenerateContentConfig(response_modalities=["Image"])


def _is_quota_error(exc: Exception) -> bool:
    """True if ``exc`` looks like a 429 / RESOURCE_EXHAUSTED quota error."""
    if getattr(exc, "code", None) == 429 or getattr(exc, "status_code", None) == 429:
        return True
    text = str(exc).upper()
    return "429" in text or "RESOURCE_EXHAUSTED" in text


def _extract_image_bytes(response) -> bytes | None:
    """Pull the first inline image payload out of a generate_content response."""
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return inline.data
    return None


def _generate_one(client, model: str, prompt: str) -> bytes:
    """Single generate_content call; raises on error, returns image bytes."""
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=_generate_config(),
    )
    image_bytes = _extract_image_bytes(response)
    if not image_bytes:
        raise RuntimeError("model returned no inline image data")
    return image_bytes


def _generate_with_fallback(client, prompt: str, idx: int) -> tuple[bytes | None, str | None]:
    """Try each model with backoff retries on 429.

    Returns ``(image_bytes, model_name)`` on success, or ``(None, None)`` if
    every model and retry is exhausted.
    """
    for model in IMAGE_MODELS:
        for attempt in range(len(RETRY_BACKOFFS) + 1):
            try:
                image_bytes = _generate_one(client, model, prompt)
                logger.info("Background %d generated with %s", idx, model)
                return image_bytes, model
            except Exception as exc:  # noqa: BLE001
                if _is_quota_error(exc) and attempt < len(RETRY_BACKOFFS):
                    wait = RETRY_BACKOFFS[attempt]
                    logger.warning(
                        "Background %d: %s rate-limited (429), retry in %ds (%d/%d)",
                        idx, model, wait, attempt + 1, len(RETRY_BACKOFFS),
                    )
                    time.sleep(wait)
                    continue
                # Non-quota error, or retries exhausted -> move to next model.
                logger.warning("Background %d: %s failed (%s); trying next model", idx, model, exc)
                break
    return None, None


def _write_fallback(path: str, idx: int) -> None:
    """Write a darkened diagonal-gradient PNG as a local background fallback."""
    w, h = config.SLIDE_WIDTH, config.SLIDE_HEIGHT
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


def generate_backgrounds(prompts: list[str], out_dir: str = None, force: bool = False) -> list[str]:
    """Generate one background PNG per prompt; return the list of file paths.

    Files are written to ``out_dir/bg_<n>.png`` (1-indexed). Existing files are
    reused unless ``force`` is True. If a prompt can't be generated (quota /
    errors), a local gradient fallback is written so the pipeline always has a
    full set of backgrounds and never crashes.
    """
    out_dir = out_dir or config.TMP_DIR
    os.makedirs(out_dir, exist_ok=True)

    paths: list[str] = []
    client = None
    used_fallback = False

    for i, prompt in enumerate(prompts, start=1):
        path = os.path.join(out_dir, f"{config.BG_IMAGE_PREFIX}{i}.png")

        if not force and os.path.exists(path) and os.path.getsize(path) > 0:
            logger.info("Background cached, skipping generation: %s", os.path.basename(path))
            paths.append(path)
            continue

        # Lazily build the client; if that fails, fall straight back to gradients.
        if client is None and not used_fallback:
            try:
                client = _client()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gemini client unavailable (%s)", exc)

        image_bytes, model = (None, None)
        if client is not None:
            image_bytes, model = _generate_with_fallback(client, prompt, i)

        if image_bytes:
            _save_png(image_bytes, path)
            logger.info("Wrote background: %s (%s)", os.path.basename(path), model)
        else:
            used_fallback = True
            _write_fallback(path, i)
            logger.info("Wrote fallback background: %s", os.path.basename(path))

        paths.append(path)

    if used_fallback:
        logger.warning("Gemini image quota unavailable - using fallback backgrounds")

    return paths


if __name__ == "__main__":
    import glob
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Load the 4 image_prompts from the most recent cached extraction JSON so we
    # don't spend Groq/Claude credits re-running the upstream steps.
    plans = sorted(glob.glob(os.path.join(config.TMP_DIR, "*.plan.json")), key=os.path.getmtime, reverse=True)
    if not plans:
        raise SystemExit(f"No *.plan.json found in {config.TMP_DIR} - run the pipeline once first.")
    with open(plans[0], encoding="utf-8") as f:
        prompts = json.load(f)["highlights"]["image_prompts"]
    print(f"Loaded {len(prompts)} image_prompts from {os.path.basename(plans[0])}\n", flush=True)

    # force=True to actually exercise the API / retry / fallback path this run.
    out = generate_backgrounds(prompts, force=True)
    print(f"\nGenerated/fell back to {len(out)} background(s):")
    for p in out:
        size = os.path.getsize(p) if os.path.exists(p) else 0
        print(f"  {p}  ({size / 1024:.0f} KB)")
