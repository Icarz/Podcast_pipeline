"""Instagram publishing via the Meta Graph API (Reels/video)."""

import logging
import os

import requests

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v21.0"


def publish(media_url: str, episode: dict, highlights: dict) -> str:
    """Publish ``media_url`` as an Instagram Reel and return the media ID."""
    logger.info("Publishing to Instagram: %s", episode.get("title"))

    ig_user_id = os.environ["META_IG_USER_ID"]
    access_token = os.environ["META_ACCESS_TOKEN"]

    # TODO: create a media container (POST .../media) then publish it
    #       (POST .../media_publish) using the Graph API two-step flow.
    raise NotImplementedError("instagram publish not yet implemented")
