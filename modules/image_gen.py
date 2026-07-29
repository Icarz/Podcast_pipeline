"""AI background generation via OpenAI's gpt-image-2 (primary background source).

Takes the ``image_prompts`` produced by :mod:`ai_extract`, generates one image
per prompt via the OpenAI Images API, and writes them to ``tmp/bg_<n>.png`` for
:mod:`video_gen` to use as themed video backgrounds. Motion is added at render
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


def _generate_one(api_key: str, prompt: str) -> bytes:
    """Single OpenAI Images API call; raises on error, returns decoded PNG bytes."""
    response = requests.post(
        OPENAI_IMAGES_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": config.OPENAI_IMAGE_MODEL,
            "prompt": prompt,
            "size": config.OPENAI_IMAGE_SIZE,
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


def _generate_with_retry(api_key: str, prompt: str, idx: int) -> bytes | None:
    """Retry with backoff on 429/5xx. Returns image bytes, or None if exhausted."""
    for attempt in range(len(RETRY_BACKOFFS) + 1):
        try:
            image_bytes = _generate_one(api_key, prompt)
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


def generate_backgrounds(
    prompts: list[str], out_dir: str = None, basename: str = None, force: bool = False,
) -> list[str]:
    """Generate one background PNG per prompt; return the list of file paths.

    Files are written to ``out_dir/<basename>_bg_<n>.png`` (1-indexed), or bare
    ``out_dir/bg_<n>.png`` when ``basename`` is omitted. Existing files are
    reused unless ``force`` is True. **Always pass the episode's audio
    basename from pipeline callers** — without it every episode collides on
    the same 4 filenames and silently reuses a previous episode's images
    regardless of this run's ``image_prompts`` (the per-clip art direction
    would never actually take effect). If a prompt can't be generated
    (missing key, rate limit, error), a local gradient fallback is written so
    the pipeline always has a full set of backgrounds and never crashes.
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
        path = os.path.join(out_dir, f"{prefix}{i}.png")

        if not force and os.path.exists(path) and os.path.getsize(path) > 0:
            logger.info("Background cached, skipping generation: %s", os.path.basename(path))
            paths.append(path)
            continue

        image_bytes = _generate_with_retry(api_key, prompt, i) if api_key else None

        if image_bytes:
            _save_png(image_bytes, path)
            logger.info("Wrote background: %s (gpt-image-2)", os.path.basename(path))
        else:
            used_fallback = True
            _write_fallback(path, i)
            logger.info("Wrote fallback background: %s", os.path.basename(path))

        paths.append(path)

    if used_fallback:
        logger.warning("%s unavailable for one or more prompts - gradient fallback used", config.OPENAI_IMAGE_MODEL)

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
