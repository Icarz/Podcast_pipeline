"""YouTube publishing via the YouTube Data API v3."""

import logging
import os

logger = logging.getLogger(__name__)


def publish(video_path: str, episode: dict, highlights: dict) -> str:
    """Upload ``video_path`` to YouTube and return the video ID/URL."""
    logger.info("Publishing to YouTube: %s", episode.get("title"))

    # TODO: build a Resumable upload using google-api-python-client +
    #       google-auth-oauthlib (OAuth flow with YOUTUBE_* env vars),
    #       set title/description from highlights, and return the video ID.
    raise NotImplementedError("youtube publish not yet implemented")
