"""Background source selection — one ordered fallback chain.

Priority (first source that yields backgrounds wins):
    1. OpenAI gpt-image-1 AI image   (primary — on-brief, art-directed per clip)
    2. gradient / dark                (always succeeds; final safety net)

Both steps are handled inside :func:`image_gen.generate_backgrounds`, which
itself degrades from gpt-image-1 to local gradients and never fails.
"""

import logging

from modules import image_gen

logger = logging.getLogger(__name__)


def select_backgrounds(highlights: dict, basename: str = None, force: bool = False) -> list[str]:
    """Return a list of background file paths (all .png).

    Generates one AI image per ``image_prompts`` entry via gpt-image-1, with a
    local gradient fallback if the API key is missing or generation fails.
    ``basename`` (the episode's audio basename) namespaces the cache so each
    episode gets its own fresh, on-brief images instead of colliding on a
    shared ``bg_<n>.png`` filename — always pass it from pipeline callers.
    """
    images = image_gen.generate_backgrounds(
        highlights.get("image_prompts", []), basename=basename, force=force,
    )
    logger.info("Background source: gpt-image-1/gradient images (%d files)", len(images))
    return images
