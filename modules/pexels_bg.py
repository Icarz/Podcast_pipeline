"""Pexels stock video backgrounds (primary background source).

For each stock-footage search query from :mod:`ai_extract`, queries the Pexels
video search API, picks the best vertical (9:16-ish) file, and downloads it to
``tmp/bg_<n>.mp4`` for :mod:`video_gen`.

Resilience:
  * per-query orientation fallback (portrait -> square -> landscape),
  * exponential backoff on 429 (Pexels free tier = 200 requests/hour),
  * per-file caching (an existing ``bg_<n>.mp4`` is reused),
  * returns ``None`` if Pexels yields nothing at all, so the caller can fall
    back to the Gemini -> gradient chain.
"""

import glob
import hashlib
import logging
import os
import time

import requests
from dotenv import load_dotenv

import config

load_dotenv()

logger = logging.getLogger(__name__)

_TARGET_RATIO = config.SLIDE_HEIGHT / config.SLIDE_WIDTH  # 1920/1080 ~= 1.778


def _api_key() -> str | None:
    return os.environ.get("PEXELS_API_KEY")


def query_slug(query: str) -> str:
    """Stable short hash of a search query, used as the cache-file key.

    Caching by the *query* (not the slot index) means a new episode whose
    ``video_queries`` differ always fetches fresh footage, while re-rendering the
    same episode (identical queries) still reuses the cached download. Normalized
    (lower/stripped) so trivial casing/whitespace differences don't miss cache.
    """
    norm = " ".join((query or "").lower().split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def _vid_from_cache_name(path: str) -> int | None:
    """Parse the Pexels video id off a ``bg_<slug>_<id>.mp4`` cache filename."""
    stem = os.path.splitext(os.path.basename(path))[0]
    tail = stem.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _cached_for_slug(out_dir: str, slug: str) -> tuple[str, int | None] | None:
    """Return (path, video_id) for an existing non-empty cache file for ``slug``.

    Cache files embed the Pexels video id (``bg_<slug>_<id>.mp4``) so that a
    cache HIT can still register its id into the run's used-id set — that's what
    makes cross-slot dedup bulletproof even on mixed cached/fresh runs.
    """
    pattern = os.path.join(out_dir, f"{config.BG_IMAGE_PREFIX}{slug}_*.mp4")
    for match in sorted(glob.glob(pattern)):
        if os.path.getsize(match) > 0:
            return match, _vid_from_cache_name(match)
    return None


def _request(query: str, orientation: str, per_page: int = None) -> dict | None:
    """One Pexels search call with backoff on 429; returns parsed JSON or None."""
    headers = {"Authorization": _api_key()}
    params = {
        "query": query,
        "orientation": orientation,
        "size": config.PEXELS_SIZE,
        "per_page": per_page or config.PEXELS_PER_PAGE,
    }
    for attempt in range(len(config.PEXELS_BACKOFFS) + 1):
        try:
            resp = requests.get(
                config.PEXELS_SEARCH_URL,
                headers=headers,
                params=params,
                timeout=config.PEXELS_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning("Pexels request error for %r (%s); aborting query", query, exc)
            return None

        if resp.status_code == 429:
            if attempt < len(config.PEXELS_BACKOFFS):
                wait = config.PEXELS_BACKOFFS[attempt]
                logger.warning("Pexels rate-limited (429) for %r, retry in %ds", query, wait)
                time.sleep(wait)
                continue
            logger.warning("Pexels still 429 for %r after retries; giving up", query)
            return None

        if resp.status_code != 200:
            logger.warning("Pexels HTTP %d for %r (%s)", resp.status_code, query, orientation)
            return None

        return resp.json()
    return None


def _best_vertical_file(video: dict) -> str | None:
    """Pick the download URL of the file whose aspect ratio is closest to 9:16.

    Prefers files at least 1080px tall; falls back to the tallest available.
    """
    files = [f for f in video.get("video_files", []) if f.get("link") and f.get("height")]
    if not files:
        return None

    def score(f):
        w, h = f.get("width") or 1, f.get("height") or 1
        ratio_err = abs((h / w) - _TARGET_RATIO)
        too_short = 0 if h >= config.SLIDE_HEIGHT else 1  # prefer >= 1080 tall
        return (too_short, ratio_err, -h)

    return min(files, key=score)["link"]


def _find_video(query: str, used_ids: set | None = None) -> tuple[str | None, int | None]:
    """Search ``query`` across orientations for a usable, not-yet-used video.

    Pulls several candidates per query and skips any whose Pexels video id is in
    ``used_ids`` so the same clip never fills two background slots. Returns
    ``(download_url, video_id)``, or ``(None, None)`` if nothing usable is left.
    """
    used_ids = used_ids or set()
    for orientation in config.PEXELS_ORIENTATIONS:
        data = _request(query, orientation, per_page=config.PEXELS_VIDEO_PER_PAGE)
        if not data:
            continue
        for video in data.get("videos", []):
            vid = video.get("id")
            if vid in used_ids:
                logger.info("Pexels video %s for %r already used; skipping to next", vid, query)
                continue
            url = _best_vertical_file(video)
            if url:
                logger.info("Pexels match for %r (%s): video %s", query, orientation, vid)
                return url, vid
    logger.warning("No unused Pexels video found for %r in any orientation", query)
    return None, None


def _download(url: str, path: str) -> bool:
    """Stream ``url`` to ``path``; return True on success."""
    try:
        with requests.get(url, stream=True, timeout=config.PEXELS_TIMEOUT) as resp:
            resp.raise_for_status()
            with open(path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
        return os.path.getsize(path) > 0
    except (requests.RequestException, OSError) as exc:
        logger.warning("Failed to download %s (%s)", os.path.basename(path), exc)
        if os.path.exists(path):
            os.remove(path)
        return False


def _photo_request(query: str, orientation: str) -> dict | None:
    """One Pexels *photo* search call with backoff on 429; parsed JSON or None."""
    headers = {"Authorization": _api_key()}
    params = {
        "query": query,
        "orientation": orientation,
        "size": config.PEXELS_SIZE,
        "per_page": config.PEXELS_PER_PAGE,
    }
    for attempt in range(len(config.PEXELS_BACKOFFS) + 1):
        try:
            resp = requests.get(
                config.PEXELS_PHOTO_SEARCH_URL,
                headers=headers,
                params=params,
                timeout=config.PEXELS_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning("Pexels photo request error for %r (%s); aborting", query, exc)
            return None

        if resp.status_code == 429:
            if attempt < len(config.PEXELS_BACKOFFS):
                wait = config.PEXELS_BACKOFFS[attempt]
                logger.warning("Pexels photo rate-limited (429) for %r, retry in %ds", query, wait)
                time.sleep(wait)
                continue
            logger.warning("Pexels photo still 429 for %r after retries; giving up", query)
            return None

        if resp.status_code != 200:
            logger.warning("Pexels photo HTTP %d for %r (%s)", resp.status_code, query, orientation)
            return None

        return resp.json()
    return None


def _best_photo_url(photo: dict) -> str | None:
    """Highest-res download URL from a Pexels photo's ``src`` dict.

    Prefers the original/large renditions so the center-crop to 1080x1350 stays
    sharp; ``portrait`` (800x1200) is only a last resort.
    """
    src = photo.get("src") or {}
    for key in ("original", "large2x", "large", "portrait"):
        url = src.get(key)
        if url:
            return url
    return None


def fetch_photo(query: str, path: str, force: bool = False) -> str | None:
    """Download ONE portrait stock photo for ``query`` to ``path``.

    Returns ``path`` on success (or when a cached file already exists), else
    ``None`` so the caller can fall back. Never raises.
    """
    if not force and os.path.exists(path) and os.path.getsize(path) > 0:
        logger.info("Pexels slide photo cached, skipping: %s", os.path.basename(path))
        return path
    if not _api_key():
        logger.info("PEXELS_API_KEY not set; skipping Pexels slide photo")
        return None
    if not query:
        return None

    data = _photo_request(query, "portrait")
    if data:
        for photo in data.get("photos", []):
            url = _best_photo_url(photo)
            if url and _download(url, path):
                logger.info("Downloaded Pexels slide photo for %r: %s", query, os.path.basename(path))
                return path
    logger.warning("No Pexels photo for %r; slide will fall back to solid background", query)
    return None


def fetch_backgrounds(queries: list[str], out_dir: str = None, force: bool = False) -> list[str] | None:
    """Download one stock video per query; return paths, or None if all fail.

    Files go to ``out_dir/bg_<query-hash>.mp4`` — keyed by the query string, not
    the slot index, so a new episode with different ``video_queries`` never
    reuses the previous episode's footage, while re-rendering the same episode
    (identical queries) still hits cache. Existing files are reused unless
    ``force`` is True. Queries that yield nothing are skipped; if *every* query
    fails (and nothing was cached), returns None so the caller can fall back to
    image/gradient backgrounds.
    """
    if not _api_key():
        logger.info("PEXELS_API_KEY not set; skipping Pexels backgrounds")
        return None

    out_dir = out_dir or config.TMP_DIR
    os.makedirs(out_dir, exist_ok=True)

    paths: list[str] = []
    chosen_ids: list[int | None] = []  # final id per slot, for the distinctness log
    used_ids: set = set()  # Pexels video ids already taken this run -> no repeats.
    for query in queries:
        slug = query_slug(query)

        # Cache HIT: reuse only if its id hasn't already been taken by an earlier
        # slot. A collision (same id cached for two queries) falls through to a
        # fresh fetch that _find_video forces to a different, unused id.
        cached = None if force else _cached_for_slug(out_dir, slug)
        if cached and cached[1] not in used_ids:
            cpath, cvid = cached
            logger.info("Pexels background cached, skipping: %s (video %s)", os.path.basename(cpath), cvid)
            if cvid is not None:
                used_ids.add(cvid)
            paths.append(cpath)
            chosen_ids.append(cvid)
            continue

        url, vid = _find_video(query, used_ids)
        if url:
            path = os.path.join(out_dir, f"{config.BG_IMAGE_PREFIX}{slug}_{vid}.mp4")
            if _download(url, path):
                used_ids.add(vid)
                logger.info("Downloaded Pexels background: %s (video %s)", os.path.basename(path), vid)
                paths.append(path)
                chosen_ids.append(vid)

    if not paths:
        logger.warning("Pexels returned no usable backgrounds")
        return None

    # Confirm every slot got a distinct Pexels video id (no clip repeats).
    distinct = len(set(chosen_ids)) == len(chosen_ids)
    logger.info(
        "Final background video ids: %s (%d slots, distinct=%s)",
        chosen_ids, len(chosen_ids), distinct,
    )
    if not distinct:
        logger.warning("Duplicate background video id across slots: %s", chosen_ids)
    return paths


if __name__ == "__main__":
    import glob
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    plans = sorted(glob.glob(os.path.join(config.TMP_DIR, "*.plan.json")), key=os.path.getmtime, reverse=True)
    if not plans:
        raise SystemExit(f"No *.plan.json found in {config.TMP_DIR} - run the pipeline once first.")
    with open(plans[0], encoding="utf-8") as f:
        highlights = json.load(f)["highlights"]
    vq = highlights.get("video_queries") or []
    queries = [v["query"] if isinstance(v, dict) else v for v in vq] or highlights.get("search_queries", [])
    print(f"Queries: {queries}\n", flush=True)

    out = fetch_backgrounds(queries, force=True)
    if out is None:
        print("Pexels failed entirely -> caller should fall back.")
    else:
        print(f"\nDownloaded {len(out)} stock video(s):")
        for p in out:
            print(f"  {p}  ({os.path.getsize(p) / (1024 * 1024):.2f} MB)")
