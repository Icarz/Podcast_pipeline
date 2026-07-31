"""Background source selection — one ordered fallback chain.

Priority (first source that yields backgrounds wins):
    1. OpenAI gpt-image-2 AI image   (primary — on-brief, art-directed per clip)
    2. gradient / dark                (always succeeds; final safety net)

Both steps are handled inside :func:`image_gen.generate_backgrounds`, which
itself degrades from gpt-image-2 to local gradients and never fails.
"""

import logging

from modules import image_gen

logger = logging.getLogger(__name__)


def select_backgrounds(highlights: dict, basename: str = None, force: bool = False) -> list[str]:
    """Return a list of background file paths (all .png).

    Composes one prompt per ``image_scenes`` entry (locked style template +
    per-clip ``wolf_outfit`` — see :func:`image_gen.compose_prompts`), falling
    back to the raw ``image_prompts`` strings of pre-2026-07-31 cached plans.
    Generation is gpt-image-2 with a local gradient fallback if the API key is
    missing or generation fails. ``basename`` (the episode's audio basename)
    namespaces the cache so each episode/clip gets its own fresh, on-brief
    images instead of colliding on a shared ``bg_<n>.png`` filename — always
    pass it from pipeline callers.
    """
    scenes = highlights.get("image_scenes")
    if scenes:
        prompts = image_gen.compose_prompts(scenes, highlights.get("wolf_outfit", ""))
    else:
        prompts = highlights.get("image_prompts", [])
    images = image_gen.generate_backgrounds(prompts, basename=basename, force=force)
    logger.info("Background source: gpt-image-2/gradient images (%d files)", len(images))
    return images
