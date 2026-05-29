"""Slide generation: render highlight text into slide images via Pillow."""

import logging
import os

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join("output", "slides")


def build_slides(highlights: dict) -> list:
    """Render one slide image per highlight and return the list of paths."""
    logger.info("Building slides from highlights")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # TODO: use Pillow to draw title/quote text onto branded backgrounds,
    #       save each as a PNG in OUTPUT_DIR, and return the paths.
    raise NotImplementedError("build_slides not yet implemented")
