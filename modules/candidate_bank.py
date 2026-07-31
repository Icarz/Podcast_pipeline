"""Cross-episode candidate bank: the Stage-1 clip backlog.

``main.py --scan`` batch-runs Stage 1 (find + filter) over unscanned episodes
and stores every surviving candidate here; ``main.py --bank`` presents the
backlog across all feeds so a human picks the BEST available clip rather than
gambling on whatever one random episode contains. Each candidate is consumed
individually, so a single episode can yield several Shorts over time instead
of being retired after its first clip.

A tiny JSON store at ``config.CANDIDATE_BANK_PATH`` (``tmp/candidate_bank.json``):

    {"episodes": {"<guid>": {
        "feed", "title", "basename", "audio_url", "scanned_at",
        "candidates": [{clip_start, clip_end, hook, exposes, reframe, payoff,
                        rank, status: available|used|rejected, used_at}, ...]
    }}}

Contract:
  * An episode appears here once it has been SCANNED (even with zero surviving
    candidates) — scanning is what retires it from ``--scan`` re-selection, not
    rendering. ``scanned_guids()`` feeds every picker's exclude set.
  * ``basename`` is the episode's audio basename, so the audio
    (``tmp/<basename>.mp3``) and transcript (``tmp/<basename>.transcript.json``)
    caches can be found — or rebuilt from ``audio_url`` — at render time.
  * read/write are crash-safe like posted_history: a missing/corrupt file reads
    as empty and never breaks a run.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import config

logger = logging.getLogger(__name__)

PATH = config.CANDIDATE_BANK_PATH

_CANDIDATE_KEYS = ("clip_start", "clip_end", "hook", "exposes", "reframe", "payoff", "rank")


def load() -> dict:
    """Return the bank dict, or an empty shell if the file is missing/unreadable."""
    if not os.path.exists(PATH):
        return {"episodes": {}}
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("episodes"), dict):
            return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read candidate bank (%s); treating as empty", exc)
    return {"episodes": {}}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def scanned_guids() -> set:
    """GUIDs of every episode already in the bank (scanned, regardless of yield)."""
    return set(load()["episodes"].keys())


def add_episode(
    guid: str, feed: str, title: str, basename: str,
    audio_url: str | None, candidates: list[dict],
) -> None:
    """Record a scanned episode and its Stage-1 surviving candidates.

    Idempotent per GUID — a re-scan of a banked episode is a no-op (never
    overwrites, so used/rejected statuses survive). An empty ``candidates``
    list is still recorded: the episode is scanned-and-exhausted, which keeps
    it out of future ``--scan`` picks.
    """
    if not guid:
        logger.warning("No GUID for episode %r; NOT banking its candidates", title)
        return
    data = load()
    if guid in data["episodes"]:
        logger.warning("Episode already banked, skipping re-add: %r (guid=%s)", title, guid)
        return
    data["episodes"][guid] = {
        "feed": feed,
        "title": title,
        "basename": basename,
        "audio_url": audio_url,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "candidates": [
            {
                "clip_start": float(c["clip_start"]),
                "clip_end": float(c["clip_end"]),
                "hook": c.get("hook", ""),
                "exposes": c.get("exposes", ""),
                "reframe": c.get("reframe", ""),
                "payoff": c.get("payoff", ""),
                "rank": i,
                "status": "available",
                "used_at": None,
            }
            for i, c in enumerate(candidates)
        ],
    }
    _save(data)
    logger.info("Banked %d candidate(s) for %r (guid=%s)", len(candidates), title, guid)


def _overlaps(a: dict, b: dict) -> bool:
    return a["clip_start"] < b["clip_end"] and b["clip_start"] < a["clip_end"]


def available_candidates(spacing_days: int | None = None) -> list[dict]:
    """Flat, pickable candidate list across all banked episodes, best first.

    Excludes: candidates not in ``available`` status; candidates whose window
    overlaps an already-used window of the same episode (Stage 1 promises
    non-overlap, this is defence in depth); and ALL candidates of an episode
    whose most recent used clip is younger than ``spacing_days``
    (``config.EPISODE_CLIP_SPACING_DAYS``) — so two clips of the same episode
    never publish back-to-back. Pass ``spacing_days=0`` to disable the spacing
    filter (e.g. for counting).

    Sort: rank-within-episode ascending (each episode's #1 pick first), then
    most recently scanned episode first.
    """
    if spacing_days is None:
        spacing_days = config.EPISODE_CLIP_SPACING_DAYS
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    spaced_out = 0

    for guid, ep in load()["episodes"].items():
        used = [c for c in ep["candidates"] if c["status"] == "used"]
        last_used = max((c.get("used_at") or "" for c in used), default="")
        if last_used and spacing_days > 0:
            try:
                if now - datetime.fromisoformat(last_used) < timedelta(days=spacing_days):
                    spaced_out += sum(1 for c in ep["candidates"] if c["status"] == "available")
                    continue
            except ValueError:
                pass  # unparseable timestamp: don't hide the episode over it
        for i, c in enumerate(ep["candidates"]):
            if c["status"] != "available":
                continue
            if any(_overlaps(c, u) for u in used):
                continue
            entry = {k: c[k] for k in _CANDIDATE_KEYS}
            entry.update({
                "guid": guid,
                "index": i,
                "feed": ep.get("feed", ""),
                "episode_title": ep.get("title", ""),
                "basename": ep.get("basename", ""),
                "audio_url": ep.get("audio_url"),
                "scanned_at": ep.get("scanned_at", ""),
            })
            out.append(entry)

    # Stable two-key sort: newest scan first within each rank tier.
    out.sort(key=lambda c: c["scanned_at"], reverse=True)
    out.sort(key=lambda c: c["rank"])
    if spaced_out:
        logger.info(
            "%d available candidate(s) hidden by the %d-day same-episode spacing rule",
            spaced_out, spacing_days,
        )
    return out


def mark_candidate(guid: str, index: int, status: str) -> None:
    """Set one candidate's status (``used`` or ``rejected``), stamping ``used_at``."""
    data = load()
    ep = data["episodes"].get(guid)
    if not ep or not (0 <= index < len(ep.get("candidates", []))):
        logger.warning("Bank candidate not found: guid=%s index=%s", guid, index)
        return
    ep["candidates"][index]["status"] = status
    ep["candidates"][index]["used_at"] = datetime.now(timezone.utc).isoformat()
    _save(data)
    logger.info(
        "Bank candidate marked %s: %r [%d] %r",
        status, ep.get("title", ""), index, ep["candidates"][index].get("hook", ""),
    )


def stats() -> dict:
    """Counts for logging/CLI: episodes scanned, candidates by status."""
    data = load()
    counts = {"episodes": len(data["episodes"]), "available": 0, "used": 0, "rejected": 0}
    for ep in data["episodes"].values():
        for c in ep["candidates"]:
            counts[c["status"]] = counts.get(c["status"], 0) + 1
    return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    s = stats()
    print(f"Candidate bank: {PATH}")
    print(f"Episodes scanned: {s['episodes']} | available: {s['available']} "
          f"| used: {s['used']} | rejected: {s['rejected']}")
    for c in available_candidates(spacing_days=0):
        print(f"  - [{c['feed']}] {c['episode_title']!r}")
        print(f"    #{c['rank'] + 1} [{c['clip_start']:.1f}-{c['clip_end']:.1f}s] {c['hook']!r}")
