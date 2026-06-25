"""Pixabay stock video fallback for when Pexels is exhausted.

Mirrors pexels_bg._find_video: takes a query + dedup sets, returns
(download_url, video_id) or (None, None).  Called by pexels_bg._acquire_for_query
when Pexels yields nothing for a query.

API docs: https://pixabay.com/api/docs/#api_search_videos
Free tier: 100 requests/minute.  Key from env var PIXABAY_API_KEY.
"""

import logging
import os
import time

import requests
from dotenv import load_dotenv

import config
from modules import bg_quality

load_dotenv()

logger = logging.getLogger(__name__)

_TARGET_RATIO = config.SLIDE_HEIGHT / config.SLIDE_WIDTH  # 1920/1080 ≈ 1.778

PIXABAY_VIDEO_URL = "https://pixabay.com/api/videos/"
PIXABAY_PER_PAGE = 12
PIXABAY_BACKOFFS = [2, 4, 8]


def _api_key() -> str | None:
    return os.environ.get("PIXABAY_API_KEY")


def _request(query: str, per_page: int = PIXABAY_PER_PAGE) -> dict | None:
    """One Pixabay video search call with backoff on 429."""
    key = _api_key()
    if not key:
        return None
    params = {
        "key": key,
        "q": query,
        "video_type": "film",
        "per_page": per_page,
        "safesearch": "true",
        "order": "popular",
    }
    for attempt in range(len(PIXABAY_BACKOFFS) + 1):
        try:
            resp = requests.get(
                PIXABAY_VIDEO_URL, params=params, timeout=config.PEXELS_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning("Pixabay request error for %r (%s)", query, exc)
            return None

        if resp.status_code == 429:
            if attempt < len(PIXABAY_BACKOFFS):
                wait = PIXABAY_BACKOFFS[attempt]
                logger.warning("Pixabay rate-limited for %r, retry in %ds", query, wait)
                time.sleep(wait)
                continue
            logger.warning("Pixabay still 429 for %r after retries", query)
            return None

        if resp.status_code != 200:
            logger.warning("Pixabay HTTP %d for %r", resp.status_code, query)
            return None

        return resp.json()
    return None


def _best_vertical_file(hit: dict) -> str | None:
    """Pick the download URL closest to 9:16 from a Pixabay hit's video sizes.

    Prefers files at least 1080 px tall; falls back to the tallest available.
    """
    videos = hit.get("videos") or {}
    candidates = []
    for size_key in ("large", "medium", "small"):
        v = videos.get(size_key)
        if v and v.get("url") and v.get("height"):
            candidates.append(v)
    if not candidates:
        return None

    def score(v):
        w, h = v.get("width") or 1, v.get("height") or 1
        ratio_err = abs((h / w) - _TARGET_RATIO)
        too_short = 0 if h >= config.SLIDE_HEIGHT else 1
        return (too_short, ratio_err, -h)

    return min(candidates, key=score)["url"]


def _to_quality_dict(hit: dict) -> dict:
    """Adapt a Pixabay hit to the dict shape bg_quality.assess expects.

    Maps Pixabay's video thumbnail URLs into the Pexels ``video_pictures``
    format so the same quality gate runs on both providers.
    """
    pictures = []
    for size_key in ("large", "medium", "small", "tiny"):
        v = (hit.get("videos") or {}).get(size_key)
        if v and v.get("thumbnail"):
            pictures.append({"picture": v["thumbnail"]})
    return {
        "id": hit.get("id"),
        "video_pictures": pictures,
        "image": hit.get("userImageURL"),
    }


def _find_video(
    query: str, used_ids: set | None = None, history_ids: set | None = None,
) -> tuple[str | None, int | None]:
    """Search Pixabay for a usable vertical video.

    Same two-tier dedup contract as ``pexels_bg._find_video``:
    ``used_ids`` is a hard block (this run), ``history_ids`` is a soft avoid
    (prior episodes).  Returns ``(download_url, video_id)`` or ``(None, None)``.
    """
    if not _api_key():
        logger.info("PIXABAY_API_KEY not set; skipping Pixabay fallback")
        return None, None

    used_ids = used_ids or set()
    history_ids = history_ids or set()
    fallback: tuple[str, int] | None = None
    quality_fallback: tuple[str, int] | None = None

    data = _request(query)
    if not data:
        return None, None

    for hit in data.get("hits", []):
        vid = hit.get("id")
        if vid in used_ids:
            logger.info("Pixabay video %s for %r already used this run; skipping", vid, query)
            continue
        url = _best_vertical_file(hit)
        if not url:
            continue
        if vid in history_ids:
            if fallback is None:
                fallback = (url, vid)
            logger.info("Pixabay video %s for %r in footage history; holding as fallback", vid, query)
            continue
        ok, reason, metrics = bg_quality.assess(_to_quality_dict(hit))
        if not ok:
            if quality_fallback is None:
                quality_fallback = (url, vid)
            logger.info(
                "Pixabay video %s for %r failed quality gate (%s) %s; skipping",
                vid, query, reason, metrics,
            )
            continue
        logger.info("Pixabay match for %r: video %s [quality %s]", query, vid, metrics)
        return url, vid

    if fallback is not None:
        url, vid = fallback
        logger.warning("Pixabay fell back to previously-used footage id %s for %r", vid, query)
        return url, vid
    if quality_fallback is not None:
        url, vid = quality_fallback
        logger.warning("Pixabay quality fallback for %r: id %s", query, vid)
        return url, vid
    logger.warning("No usable Pixabay video for %r", query)
    return None, None
