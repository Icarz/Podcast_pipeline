"""RSS ingestion: fetch the latest podcast episode and download its audio.

Parses a podcast RSS feed with browser-like headers, pulls the latest
episode's audio URL from its enclosures, and downloads the MP3 with yt-dlp.
"""

import hashlib
import logging
import os
import random
import re
import unicodedata
from urllib.parse import unquote, urlparse

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


def _entry_guid(entry) -> str | None:
    """The stable per-episode id from a feed entry.

    feedparser normalizes the RSS ``<guid>`` onto ``entry.id``; we fall back to
    the raw ``guid``, then the episode ``link``, then the ``title`` so the
    posted-history key is never empty for a well-formed feed.
    """
    return entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")


def _parse_feed(feed_url: str):
    """Parse ``feed_url`` (browser headers) and return the feedparser result.

    Raises ValueError if the feed can't be parsed or has no entries, so callers
    can treat an unreachable/empty feed as a clean no-op.
    """
    logger.info("Parsing feed: %s", feed_url)
    feed = feedparser.parse(feed_url, request_headers=BROWSER_HEADERS)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Failed to parse feed {feed_url}: {feed.bozo_exception}")
    if not feed.entries:
        raise ValueError(f"No entries found in feed: {feed_url}")
    return feed


def _entry_metadata(entry) -> dict:
    """The non-audio metadata shared by ``peek_latest`` and ``fetch_latest``."""
    return {
        "title": entry.get("title"),
        "guid": _entry_guid(entry),
        "description": entry.get("summary") or entry.get("description"),
        "link": entry.get("link"),
    }


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


def parse_latest(feed_url: str):
    """Parse ``feed_url`` ONCE and return ``(feed, latest_entry, metadata)``.

    Lets a caller read the latest episode's metadata (e.g. the GUID for the
    posted-history pre-check) and then download that SAME parsed entry via
    :func:`download_latest` — so a full ``--auto`` run hits the feed only once.
    Raises ValueError on an unreachable/empty feed.
    """
    feed = _parse_feed(feed_url)
    entry = feed.entries[0]
    logger.info("Latest episode: %s", entry.get("title"))
    return feed, entry, _entry_metadata(entry)


def download_latest(feed, entry) -> dict:
    """Download ``entry``'s audio (``feed`` supplies the show name) and return its
    metadata dict plus ``audio_path``.

    Pairs with :func:`parse_latest` so the feed is parsed only once per run.
    """
    audio_url = _extract_audio_url(entry)
    if not audio_url:
        raise ValueError("Latest episode has no audio enclosure")

    feed_name = _sanitize(feed.feed.get("title", "feed"), max_len=config.FEED_NAME_MAX_LEN)
    title_slug = _sanitize(entry.get("title", ""), max_len=config.EPISODE_TITLE_MAX_LEN)
    basename = f"{feed_name}_{title_slug}"

    logger.info("Downloading audio: %s -> tmp/%s.mp3", audio_url, basename)
    audio_path = _download_audio(audio_url, basename)

    meta = _entry_metadata(entry)
    meta["audio_path"] = audio_path
    return meta


def fetch_from_url(url: str, title: str | None = None) -> dict:
    """Download a DIRECT episode-audio URL, bypassing RSS entirely.

    Mirrors the yt-dlp download path used for feed episodes (:func:`_download_audio`)
    but takes a raw audio URL instead of a feed. Returns the same minimal episode
    dict shape the pipeline consumes downstream (``transcribe`` onward):

      * ``title``      — ``title`` when given, else derived from the URL filename.
      * ``guid``       — sha1 of the URL, so posted-history dedup still works on a
                         repeat run of the same direct URL.
      * ``audio_path`` — the downloaded local file.
      * ``feed``       — the literal ``"manual"`` (not a configured feed).
      * ``description`` / ``link`` — ``None`` / the source URL.
    """
    # Derive a title from the URL filename when none was supplied.
    if not title:
        filename = unquote(os.path.basename(urlparse(url).path))
        title = os.path.splitext(filename)[0] or "manual_episode"

    guid = hashlib.sha1(url.encode("utf-8")).hexdigest()
    basename = f"manual_{_sanitize(title, max_len=config.EPISODE_TITLE_MAX_LEN)}"

    logger.info("Downloading direct audio (no RSS): %s -> tmp/%s", url, basename)
    audio_path = _download_audio(url, basename)

    return {
        "title": title,
        "guid": guid,
        "audio_path": audio_path,
        "description": None,
        "link": url,
        "feed": "manual",
    }


def pick_random_entry(feed_url: str, exclude_guids: set) -> tuple | None:
    """Parse ``feed_url`` and return ``(feed, entry, metadata)`` for a random unused episode.

    Filters out every entry whose GUID is in ``exclude_guids``, then picks one
    at random from what remains. Returns ``None`` when every entry in the feed's
    current RSS window has already been used — the caller should log and skip.
    """
    feed = _parse_feed(feed_url)
    available = [e for e in feed.entries if _entry_guid(e) not in exclude_guids]
    if not available:
        logger.info(
            "All %d entries in the RSS window are already used for this feed",
            len(feed.entries),
        )
        return None
    entry = random.choice(available)
    logger.info(
        "Random episode selected: %r (%d unused / %d total in RSS window)",
        entry.get("title"), len(available), len(feed.entries),
    )
    return feed, entry, _entry_metadata(entry)


def peek_latest(feed_url: str) -> dict:
    """Latest episode metadata WITHOUT downloading audio (rotation pre-check).

    Returns ``title``, ``guid``, ``description``, ``link`` (no ``audio_path``).
    """
    logger.info("Peeking latest episode (no download): %s", feed_url)
    _, _, meta = parse_latest(feed_url)
    return meta


def fetch_latest(feed_url: str) -> dict:
    """Parse + download the latest episode in one call (manual runs / harness).

    Returns ``title``, ``guid``, ``audio_path``, ``description``, ``link``. Parses
    the feed exactly once (delegates to :func:`parse_latest` + :func:`download_latest`).
    """
    feed, entry, _ = parse_latest(feed_url)
    return download_latest(feed, entry)


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
