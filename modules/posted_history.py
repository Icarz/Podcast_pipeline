"""Posted-history log: which episodes have already been uploaded.

A tiny JSON store at ``config.POSTED_HISTORY_PATH`` (``tmp/posted_history.json``)
keyed by the episode's RSS GUID, so the weekly rotation (``main.py --auto``) never
re-uploads an episode it has already published.

Contract:
  * shape: ``{"<guid>": {"feed", "title", "used_at" (ISO 8601 UTC), ...}}`` —
    older entries may carry ``posted_at``/``youtube_url`` fields from the
    pre-2026-07-31 publish stage; they're preserved but no longer written.
  * ``mark_used()`` retires an episode at render time and is the only writer.
  * read/write are both crash-safe: a missing or corrupt file reads as empty so
    a bad log never breaks a run.
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


def mark_used(guid: str, feed: str, title: str) -> None:
    """Retire an episode at render time, before the YouTube upload.

    Called immediately after the clip plan is built so the episode is excluded
    from future random selection even when the YouTube upload later fails.
    Idempotent — a re-render of the same episode (cache hit) is a no-op.
    No-op (with a warning) when ``guid`` is falsy.
    """
    if not guid:
        logger.warning("No GUID for episode %r; NOT recording to used history", title)
        return
    data = load()
    if guid in data:
        return  # already retired; don't overwrite (preserves youtube_url if set)
    data[guid] = {
        "feed": feed,
        "title": title,
        "used_at": datetime.now(timezone.utc).isoformat(),
        "youtube_url": None,
    }
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Retired episode at render time: %r (guid=%s)", title, guid)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    hist = load()
    print(f"Posted history: {PATH}")
    print(f"Entries: {len(hist)}")
    for guid, meta in hist.items():
        print(f"  - {meta.get('feed')}: {meta.get('title')!r}")
        print(f"    guid={guid}  at={meta.get('posted_at')}  url={meta.get('youtube_url')}")
