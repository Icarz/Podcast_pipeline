"""RSS ingestion: fetch the latest podcast episode and download its audio.

Parses a podcast RSS feed with browser-like headers, pulls the latest
episode's audio URL from its enclosures, and downloads the MP3 with yt-dlp.
"""

import logging
import os
import re
import unicodedata

import feedparser
import yt_dlp

import config

logger = logging.getLogger(__name__)

TMP_DIR = config.TMP_DIR
BROWSER_HEADERS = config.BROWSER_HEADERS


def _sanitize(text: str, max_len: int = 60) -> str:
    """Sanitize ``text`` to a filesystem-safe ASCII slug.

    Transliterates to ASCII, replaces whitespace with underscores, drops any
    other non-word characters, collapses repeats, and truncates to ``max_len``.
    """
    if not text:
        return "untitled"
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"[^A-Za-z0-9_-]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text[:max_len].rstrip("_")) or "untitled"


def _extract_audio_url(entry) -> str | None:
    """Return the first audio enclosure URL on a feed ``entry``, if any."""
    for enclosure in entry.get("enclosures", []):
        if enclosure.get("type", "").startswith("audio"):
            return enclosure.get("href") or enclosure.get("url")
    # Fallback: some feeds expose media via links rel="enclosure".
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and link.get("type", "").startswith("audio"):
            return link.get("href")
    return None


def _download_audio(audio_url: str, basename: str) -> str:
    """Download ``audio_url`` into TMP_DIR as ``basename.<ext>``; return the local path."""
    os.makedirs(TMP_DIR, exist_ok=True)
    outtmpl = os.path.join(TMP_DIR, f"{basename}.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "http_headers": BROWSER_HEADERS,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(audio_url, download=True)
        path = ydl.prepare_filename(info)

    return path


def fetch_latest(feed_url: str) -> dict:
    """Fetch ``feed_url``, download the latest episode's audio, and return its metadata.

    Returns a dict with ``title``, ``audio_path``, ``description``, and ``link``.
    """
    logger.info("Parsing feed: %s", feed_url)
    feed = feedparser.parse(feed_url, request_headers=BROWSER_HEADERS)

    if feed.bozo and not feed.entries:
        raise ValueError(f"Failed to parse feed {feed_url}: {feed.bozo_exception}")
    if not feed.entries:
        raise ValueError(f"No entries found in feed: {feed_url}")

    entry = feed.entries[0]
    audio_url = _extract_audio_url(entry)
    if not audio_url:
        raise ValueError("Latest episode has no audio enclosure")

    feed_name = _sanitize(feed.feed.get("title", "feed"), max_len=config.FEED_NAME_MAX_LEN)
    title_slug = _sanitize(entry.get("title", ""), max_len=config.EPISODE_TITLE_MAX_LEN)
    basename = f"{feed_name}_{title_slug}"

    logger.info("Latest episode: %s", entry.get("title"))
    logger.info("Downloading audio: %s -> tmp/%s.mp3", audio_url, basename)
    audio_path = _download_audio(audio_url, basename)

    return {
        "title": entry.get("title"),
        "audio_path": audio_path,
        "description": entry.get("summary") or entry.get("description"),
        "link": entry.get("link"),
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    result = fetch_latest(config.PODCAST_FEEDS[config.DEFAULT_FEED])

    print("\n=== Latest episode ===")
    print(f"Title      : {result['title']}")
    print(f"Audio path : {result['audio_path']}")
    print(f"Link       : {result['link']}")
    desc = (result["description"] or "")[:300]
    print(f"Description: {desc}{'...' if result['description'] and len(result['description']) > 300 else ''}")

    print(f"\nFile exists: {os.path.exists(result['audio_path'])}")
    if os.path.exists(result["audio_path"]):
        size_mb = os.path.getsize(result["audio_path"]) / (1024 * 1024)
        print(f"File size  : {size_mb:.2f} MB")
