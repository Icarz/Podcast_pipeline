"""Video generation: compose slides + audio into a finished video via MoviePy."""

import logging
import os

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join("output", "videos")


def build_video(audio_path: str, slides: list, highlights: dict) -> str:
    """Render a video from ``slides`` over ``audio_path`` and return its path."""
    logger.info("Building video from %d slides", len(slides))
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # TODO: use moviepy to layer slide images over the audio track,
    #       set durations from highlight timings, and export an MP4.
    raise NotImplementedError("build_video not yet implemented")
