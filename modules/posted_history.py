"""Posted-history log: which episodes have already been uploaded.

A tiny JSON store at ``config.POSTED_HISTORY_PATH`` (``tmp/posted_history.json``)
keyed by the episode's RSS GUID, so the weekly rotation (``main.py --auto``) never
re-uploads an episode it has already published.

Contract:
  * shape: ``{"<guid>": {"feed", "title", "posted_at" (ISO 8601 UTC), "youtube_url"}}``
  * ``record()`` is called ONLY after a successful YouTube upload — a failed
    upload leaves no entry, so the next run retries the same episode.
  * read/write are both crash-safe: a missing or corrupt file reads as empty so
    a bad log never breaks a scheduled run.
"""

import json
import logging
import os
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

PATH = config.POSTED_HISTORY_PATH


def load() -> dict:
    """Return the history dict, or ``{}`` if the file is missing/unreadable."""
    if not os.path.exists(PATH):
        return {}
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read posted history (%s); treating as empty", exc)
        return {}


def is_posted(guid: str) -> bool:
    """True if ``guid`` is already recorded as posted. Empty guid -> False."""
    if not guid:
        return False
    return guid in load()


def record(guid: str, feed: str, title: str, youtube_url: str) -> None:
    """Append/overwrite the entry for ``guid`` after a successful upload.

    Idempotent by design: re-recording an existing GUID (e.g. a manual re-upload
    of an episode already in the log) simply OVERWRITES the entry — latest upload
    wins (fresh ``posted_at`` + ``youtube_url``), never an error.

    No-op (with a warning) when ``guid`` is falsy — we never want to key the log
    on an empty string and silently collapse distinct episodes together.
    """
    if not guid:
        logger.warning("No GUID for episode %r; NOT recording to posted history", title)
        return
    data = load()
    data[guid] = {
        "feed": feed,
        "title": title,
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "youtube_url": youtube_url,
    }
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Recorded posted episode: %r (guid=%s)", title, guid)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    hist = load()
    print(f"Posted history: {PATH}")
    print(f"Entries: {len(hist)}")
    for guid, meta in hist.items():
        print(f"  - {meta.get('feed')}: {meta.get('title')!r}")
        print(f"    guid={guid}  at={meta.get('posted_at')}  url={meta.get('youtube_url')}")
