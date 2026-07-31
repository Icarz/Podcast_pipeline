"""Dedup ledger for synthetic script topics.

A tiny JSON store at ``config.SCRIPT_HISTORY_PATH`` (``tmp/script_history.json``)
listing every topic ``modules/script_gen.py`` has generated, so each run's
topic pick can avoid repeating a recent topic_cluster or hook. Much simpler
than the old ``posted_history.py``: no GUIDs, no used/rejected states -- just
a flat, append-only log.

Contract:
  * shape: a JSON list of ``{"date", "topic_cluster", "hook", "title"}``,
    newest last.
  * ``record()`` is the only writer, called once by ``main.py`` right after
    ``script_gen``'s output is written to disk -- the topic is spent whether
    or not the render step ever runs, same timing philosophy as the old
    ``posted_history.mark_used()``.
  * read/write are both crash-safe: a missing or corrupt file reads as empty
    so a bad log never breaks a run.
"""

import json
import logging
import os
from datetime import date

import config

logger = logging.getLogger(__name__)

PATH = config.SCRIPT_HISTORY_PATH


def load() -> list[dict]:
    """Return the history list, or ``[]`` if the file is missing/unreadable."""
    if not os.path.exists(PATH):
        return []
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read script history (%s); treating as empty", exc)
        return []


def recent(limit: int = 8) -> list[dict]:
    """Return the ``limit`` most recently recorded entries, oldest first within
    the slice (so ``entries[-1]`` is always 'the most recently used topic')."""
    return load()[-limit:]


def record(topic_cluster: str, hook: str, title: str) -> None:
    """Append a new entry."""
    data = load()
    data.append({
        "date": date.today().isoformat(),
        "topic_cluster": topic_cluster,
        "hook": hook,
        "title": title,
    })
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Recorded script topic: [%s] %r", topic_cluster, title)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    hist = load()
    print(f"Script history: {PATH}")
    print(f"Entries: {len(hist)}")
    for e in hist:
        print(f"  - [{e.get('topic_cluster')}] {e.get('title')!r} (hook: {e.get('hook')!r}, {e.get('date')})")
