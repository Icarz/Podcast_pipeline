"""Background source selection — one ordered fallback chain.

Priority (first source that yields backgrounds wins):
    1. Pexels stock video   (if PEXELS_API_KEY is set and search succeeds)
    2. Gemini AI image       (gemini-2.5-flash-image / fallback model chain)
    3. gradient / dark        (always succeeds; final safety net)

Steps 2 and 3 are handled inside :func:`image_gen.generate_backgrounds`, which
itself degrades from Gemini to local gradients and never fails. So this chain
only needs to try Pexels first, then hand off to image_gen.
"""

import logging

from modules import image_gen, pexels_bg

logger = logging.getLogger(__name__)


def _video_queries(highlights: dict) -> list[str]:
    """The stock-VIDEO search strings for the clip background.

    ai_extract emits ``video_queries`` as ``{keyword, query}`` objects — take the
    ``query`` field of each. Tolerates the legacy plain-string form and falls
    back to the slide ``search_queries`` for plans that predate ``video_queries``.
    """
    queries: list[str] = []
    for item in highlights.get("video_queries") or []:
        if isinstance(item, dict):
            q = (item.get("query") or "").strip()
        elif isinstance(item, str):
            q = item.strip()
        else:
            q = ""
        if q:
            queries.append(q)
    return queries or highlights.get("search_queries", [])


def select_backgrounds(highlights: dict, force: bool = False) -> list[str]:
    """Return a list of background file paths (all .mp4 or all .png).

    Tries Pexels stock video first, then falls back to the Gemini -> gradient
    image chain. Always returns a non-empty list.
    """
    # 1. Pexels stock video (primary). Use the video-optimized queries tuned for
    #    portrait footage that looks good in motion; fall back to the slide photo
    #    queries only for older cached plans that predate video_queries.
    videos = pexels_bg.fetch_backgrounds(_video_queries(highlights), force=force)
    if videos:
        logger.info("Background source: Pexels stock video (%d clips)", len(videos))
        return videos

    # 2 + 3. Gemini image, then gradient fallback (image_gen handles both).
    images = image_gen.generate_backgrounds(highlights.get("image_prompts", []), force=force)
    logger.info("Background source: Gemini/gradient images (%d files)", len(images))
    return images
