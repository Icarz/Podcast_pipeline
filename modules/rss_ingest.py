"""RSS ingestion: fetch the latest podcast episode and download its audio."""

import logging
import os

import feedparser

logger = logging.getLogger(__name__)

TMP_DIR = "tmp"


def fetch_latest(feed_url: str) -> dict:
    """Return metadata for the most recent episode in ``feed_url``."""
    logger.info("Parsing feed: %s", feed_url)
    feed = feedparser.parse(feed_url)
    if not feed.entries:
        raise ValueError(f"No entries found in feed: {feed_url}")

    entry = feed.entries[0]
    audio_url = None
    for enclosure in entry.get("enclosures", []):
        if enclosure.get("type", "").startswith("audio"):
            audio_url = enclosure.get("href")
            break

    return {
        "title": entry.get("title"),
        "summary": entry.get("summary"),
        "published": entry.get("published"),
        "guid": entry.get("id"),
        "audio_url": audio_url,
    }


def download_audio(episode: dict) -> str:
    """Download the episode audio to ``tmp/`` and return the local path."""
    audio_url = episode.get("audio_url")
    if not audio_url:
        raise ValueError("Episode has no audio URL")

    os.makedirs(TMP_DIR, exist_ok=True)
    # TODO: download via yt-dlp / requests and return the saved path.
    raise NotImplementedError("download_audio not yet implemented")
