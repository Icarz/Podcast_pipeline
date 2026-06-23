"""Podcast automation pipeline orchestrator.

End-to-end flow for the latest episode of a feed:
    RSS ingest -> transcribe -> AI extract -> background select
    -> video render (karaoke MP4) -> slide deck (PNGs)

After rendering, the publish stage is resilient and best-effort:
    R2 upload (video + slides) -> YouTube Short (default private)
    -> Instagram + TikTok logged as MANUAL POST reminders.
A failure in R2 or YouTube is logged but never crashes the run or discards
the already-rendered video/slides. Instagram is NOT auto-published (the Meta
flow is a NotImplementedError scaffold) — we only log the file paths/URLs.

Run:
    .\\venv\\Scripts\\python.exe main.py mindset_mentor   # feed key
    .\\venv\\Scripts\\python.exe main.py https://...rss    # or a raw RSS URL
    .\\venv\\Scripts\\python.exe main.py --url https://...mp3 --title "..."  # direct audio, no RSS

Re-runs reuse the tmp/<basename>.plan.json cache that this module (and the
video_gen harness) writes, so Groq/Claude are only hit once per episode.
"""

import json
import logging
import os
import sys
from datetime import date

from dotenv import load_dotenv

import config
from modules import (
    ai_extract,
    background,
    posted_history,
    rss_ingest,
    slide_gen,
    storage,
    transcribe,
    video_gen,
    youtube_publish,
)

# Mon=0 .. Sun=6 (matches date.weekday() and config.ROTATION keys).
_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# NOTE: instagram_publish is deliberately NOT imported — its Meta Graph flow is
# still a NotImplementedError scaffold, so Instagram/TikTok are handled as
# MANUAL POST log reminders rather than real API calls (see _publish_stage).

load_dotenv()

# Episode titles often contain non-ASCII (curly quotes, em dashes); the Windows
# console defaults to cp1252 and would crash on them. Force UTF-8 on the console
# streams so logging/printing a title never takes down the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

os.makedirs(config.LOGS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("pipeline")


def _display_name(feed_arg: str) -> str:
    """The brand name for the watermark.

    ALWAYS the single-source brand (``config.BRAND_NAME``) — every video the
    pipeline publishes must carry the Icarus Wings watermark, regardless of
    source. This holds for configured feed keys, raw RSS URLs, and direct
    ``--url`` ("manual") runs alike.
    """
    return config.BRAND_NAME


def _plan_cache_path(audio_path: str) -> str:
    """The plan cache path video_gen uses: tmp/<basename>.plan.json."""
    return os.path.splitext(audio_path)[0] + ".plan.json"


def _write_plan(cache_path: str, transcript: dict, highlights: dict) -> None:
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"transcript": transcript, "highlights": highlights}, f)


def _load_or_build_plan(audio_path: str) -> tuple[dict, dict]:
    """Return (transcript, highlights), reusing the cache when present.

    Only transcribes (Groq) + extracts (Claude) on a cache miss, so repeat
    runs of the same episode never burn API credits.
    """
    cache_path = _plan_cache_path(audio_path)

    if os.path.exists(cache_path):
        logger.info("[2-3/6] Plan cache HIT: %s (skipping Groq + Claude)", os.path.basename(cache_path))
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        transcript, highlights = cached["transcript"], cached["highlights"]
        # Regenerate JUST the extraction (no Groq) if the cached plan predates a
        # schema field we now require.
        if "search_queries" not in highlights:
            logger.info("Cached plan missing search_queries; re-running extraction (no Groq)")
            highlights = ai_extract.extract_highlights_with_retry(transcript)
            _write_plan(cache_path, transcript, highlights)
        return transcript, highlights

    logger.info("[2/6] Transcribe (Groq Whisper): %s", os.path.basename(audio_path))
    transcript = transcribe.transcribe(audio_path)
    logger.info("[3/6] Extract clip plan (Claude %s)", config.EXTRACT_MODEL)
    highlights = ai_extract.extract_highlights_with_retry(transcript)
    _write_plan(cache_path, transcript, highlights)
    logger.info("Cached transcript + plan: %s", os.path.basename(cache_path))
    return transcript, highlights


def _publish_stage(
    episode: dict,
    highlights: dict,
    video_path: str,
    slides: list[str],
    privacy_status: str = "private",
) -> dict:
    """Best-effort publish: R2 upload -> YouTube -> manual-post reminders.

    The content already rendered successfully by the time we get here, so every
    external step is wrapped: a failure is logged clearly but NEVER raised, so a
    flaky upload can't throw away the generated video/slides. Returns a dict of
    everything we managed to do (URLs and/or per-step error strings) for the
    end-of-run summary.

    ``privacy_status`` is forwarded to YouTube (defaults to ``"private"`` so a
    test run never goes public — flip to ``"public"`` manually after review).
    """
    result: dict = {
        "video_url": None,
        "slide_urls": [],
        "youtube_url": None,
        "r2_error": None,
        "youtube_error": None,
        "privacy_status": privacy_status,
    }

    # --- 1) R2 upload: video first, then each slide. -----------------------
    logger.info("[publish] R2: upload video + %d slide(s)", len(slides))
    try:
        result["video_url"] = storage.upload(video_path)
        logger.info("[publish] R2 video URL: %s", result["video_url"])
        for i, slide_path in enumerate(slides, 1):
            slide_url = storage.upload(slide_path)
            result["slide_urls"].append(slide_url)
            logger.info("[publish] R2 slide %d URL: %s", i, slide_url)
    except Exception as exc:  # noqa: BLE001 - publish must never crash the run
        result["r2_error"] = str(exc)
        logger.exception("[publish] R2 upload FAILED (continuing): %s", exc)

    # --- 2) YouTube Short (private by default). ----------------------------
    logger.info("[publish] YouTube: upload Short (%s)", privacy_status)
    try:
        result["youtube_url"] = youtube_publish.publish(
            video_path, episode, highlights, privacy_status=privacy_status
        )
        logger.info("[publish] YouTube URL: %s", result["youtube_url"])
    except Exception as exc:  # noqa: BLE001 - publish must never crash the run
        result["youtube_error"] = str(exc)
        logger.exception("[publish] YouTube upload FAILED (continuing): %s", exc)

    # --- 3) Instagram: NO API call — log a MANUAL POST reminder. -----------
    ig_lines = [
        "[publish] MANUAL POST — Instagram (post by hand):",
        f"            Reel video (local) : {video_path}",
    ]
    if result["video_url"]:
        ig_lines.append(f"            Reel video (R2)    : {result['video_url']}")
    ig_lines.append(f"            Carousel slides ({len(slides)}):")
    for i, slide_path in enumerate(slides, 1):
        ig_lines.append(f"              {i}. local: {slide_path}")
        if i - 1 < len(result["slide_urls"]):
            ig_lines.append(f"                 R2   : {result['slide_urls'][i - 1]}")
    logger.warning("\n".join(ig_lines))

    # --- 4) TikTok: NO API call — log a MANUAL POST reminder. --------------
    tt_lines = [
        "[publish] MANUAL POST — TikTok (post by hand):",
        f"            Video (local) : {video_path}",
    ]
    if result["video_url"]:
        tt_lines.append(f"            Video (R2)    : {result['video_url']}")
    logger.warning("\n".join(tt_lines))

    return result


def run(feed_arg: str, episode: dict | None = None, privacy_status: str = "private") -> dict:
    """Run ingest -> render -> publish for the latest episode of ``feed_arg``.

    ``feed_arg`` is a key in ``config.PODCAST_FEEDS`` or a raw RSS URL.
    ``episode`` may be a pre-ingested episode dict (with ``audio_path``) — passed
    by :func:`run_auto` so the feed it already parsed for the GUID pre-check is
    NOT parsed/downloaded a second time. When ``None`` (manual runs) the feed is
    parsed + downloaded here. ``privacy_status`` is forwarded to YouTube
    (default ``"private"``). Returns a summary dict.
    """
    feed_url = config.PODCAST_FEEDS.get(feed_arg, feed_arg)
    podcast_name = _display_name(feed_arg)
    logger.info("Starting pipeline | feed=%s -> %s", feed_arg, feed_url)

    # 1) Ingest: pick a random unused episode from the feed (or use the
    #    pre-fetched episode handed in by run_auto so the feed is parsed once).
    if episode is None:
        used_guids = set(posted_history.load().keys())
        picked = rss_ingest.pick_random_entry(feed_url, exclude_guids=used_guids)
        if picked is None:
            raise RuntimeError(
                f"All episodes in the RSS window for '{feed_arg}' have already been used. "
                "Delete tmp/posted_history.json to reset."
            )
        feed_obj, entry, _ = picked
        logger.info("[1/6] Ingest: random episode selected, downloading")
        episode = rss_ingest.download_latest(feed_obj, entry)
    else:
        logger.info("[1/6] Ingest: using pre-fetched episode (feed parsed once)")
    audio_path = episode["audio_path"]
    logger.info("Episode: %s", episode.get("title"))
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Downloaded audio missing: {audio_path}")

    # 2-3) Transcribe + extract clip plan (cached together as one plan.json).
    transcript, highlights = _load_or_build_plan(audio_path)
    cs, ce = float(highlights["clip_start"]), float(highlights["clip_end"])
    logger.info("Clip window: %.2f-%.2fs (%.1fs)", cs, ce, ce - cs)

    # Retire the episode immediately after the plan is built so a YouTube
    # failure later doesn't leave it available for re-selection next run.
    guid = episode.get("guid")
    if guid and feed_arg != "manual":
        posted_history.mark_used(
            guid=guid,
            feed=feed_arg,
            title=episode.get("title", ""),
        )

    # 4) Background selection: Pexels video -> Gemini image -> gradient chain.
    logger.info("[4/6] Backgrounds: select (Pexels -> Gemini -> gradient)")
    backgrounds = background.select_backgrounds(highlights)
    bg_kind = "video" if backgrounds and backgrounds[0].lower().endswith(".mp4") else "image"
    logger.info("Backgrounds: %d %s file(s)", len(backgrounds), bg_kind)

    # 5) Render the karaoke video.
    logger.info("[5/6] Video: render karaoke MP4")
    video_path = video_gen.build_video(
        audio_path,
        transcript["words"],
        highlights,
        podcast_name=podcast_name,
        background_images=backgrounds,
    )

    # 6) Render the static slide deck (hook + 3 insights + quote = 5 PNGs).
    logger.info("[6/6] Slides: render deck")
    slides = slide_gen.build_slides(highlights)

    # 7) Publish: R2 -> YouTube -> manual reminders (best-effort, never fatal).
    logger.info("[7/7] Publish: R2 + YouTube + manual reminders")
    publish = _publish_stage(
        episode, highlights, video_path, slides, privacy_status=privacy_status
    )

    # 8) Stamp the YouTube URL on the already-retired episode entry (written at
    #    render time above). A YouTube failure is fine — the episode is already
    #    excluded from future random selection regardless.
    if publish.get("youtube_url") and not publish.get("youtube_error"):
        posted_history.record(
            guid=episode.get("guid"),
            feed=feed_arg,
            title=episode.get("title"),
            youtube_url=publish["youtube_url"],
        )

    logger.info("Pipeline complete for: %s", episode.get("title"))
    return {
        "episode": episode,
        "highlights": highlights,
        "clip_start": cs,
        "clip_end": ce,
        "video_path": video_path,
        "slides": slides,
        "publish": publish,
    }


_AUTO_MAX_EPISODE_ATTEMPTS = 3


def run_auto(privacy_status: str = "private") -> dict | None:
    """Rotation entry point: pick today's feed and process a random unused episode.

    Resolves today's weekday against ``config.ROTATION`` and:
      * not a posting day           -> log and return None,
      * feed unreachable            -> log and return None,
      * all RSS entries already used -> log and return None,
      * otherwise                   -> pick a random unused episode and run the
                                       full pipeline via :func:`run`.

    If the content gate rejects every extraction attempt for an episode (all 3
    retries fail), the episode is skipped and another is tried — up to
    ``_AUTO_MAX_EPISODE_ATTEMPTS`` episodes before giving up.

    Episodes are retired at render time (not post time), so a YouTube failure
    never leaves an episode re-eligible for selection next run.
    """
    weekday = date.today().weekday()
    feed_key = config.ROTATION.get(weekday)
    if feed_key is None:
        logger.info("Auto mode: no posting day today (%s); nothing to do", _WEEKDAY_NAMES[weekday])
        return None

    feed_url = config.PODCAST_FEEDS[feed_key]
    logger.info("Auto mode: %s -> feed '%s' (random episode)", _WEEKDAY_NAMES[weekday], feed_key)

    used_guids = set(posted_history.load().keys())
    skipped_guids: set[str] = set()

    for ep_attempt in range(1, _AUTO_MAX_EPISODE_ATTEMPTS + 1):
        try:
            picked = rss_ingest.pick_random_entry(
                feed_url, exclude_guids=used_guids | skipped_guids,
            )
        except Exception as exc:  # noqa: BLE001 - feed problems must not break scheduling
            logger.warning("Auto mode: feed '%s' unavailable (%s); exiting cleanly", feed_key, exc)
            return None

        if picked is None:
            logger.info(
                "Auto mode: all episodes in '%s' RSS window already used; nothing to do",
                feed_key,
            )
            return None

        feed, entry, meta = picked
        guid = meta.get("guid", "")
        logger.info(
            "Auto mode: episode attempt %d/%d: %r (guid=%s)",
            ep_attempt, _AUTO_MAX_EPISODE_ATTEMPTS, meta.get("title"), guid,
        )
        episode = rss_ingest.download_latest(feed, entry)

        try:
            return run(feed_key, episode=episode, privacy_status=privacy_status)
        except ValueError as exc:
            if "CONTENT GATE" in str(exc) or "BRAND GATE" in str(exc):
                logger.warning(
                    "Auto mode: episode %r failed quality gates after all retries, "
                    "skipping to next episode: %s", meta.get("title"), exc,
                )
                if guid:
                    skipped_guids.add(guid)
                # Clean up stale plan cache so it doesn't block future runs
                cache_path = _plan_cache_path(episode["audio_path"])
                if os.path.exists(cache_path):
                    os.remove(cache_path)
                continue
            raise

    logger.warning(
        "Auto mode: exhausted %d episode attempts for '%s'; no episode passed quality gates",
        _AUTO_MAX_EPISODE_ATTEMPTS, feed_key,
    )
    return None


def _print_summary(result: dict) -> None:
    ep = result["episode"]
    cs, ce = result["clip_start"], result["clip_end"]
    line = "=" * 64
    print("\n" + line)
    print("PIPELINE SUMMARY")
    print(line)
    print(f"Episode     : {ep.get('title')}")
    print(f"Clip window : {cs:.2f}-{ce:.2f}s  ({ce - cs:.1f}s)")
    print(f"Video MP4   : {result['video_path']}")
    print(f"Slides ({len(result['slides'])})  :")
    for p in result["slides"]:
        print(f"              - {p}")

    pub = result.get("publish", {})
    print(line)
    print("PUBLISH")
    print(line)

    # YouTube
    if pub.get("youtube_url"):
        print(f"YouTube ({pub.get('privacy_status', '?')}) : {pub['youtube_url']}")
    else:
        print(f"YouTube     : FAILED — {pub.get('youtube_error') or 'not attempted'}")

    # R2
    if pub.get("r2_error"):
        print(f"R2          : FAILED — {pub['r2_error']}")
    else:
        print(f"R2 video    : {pub.get('video_url') or '(none)'}")
        if pub.get("slide_urls"):
            print(f"R2 slides ({len(pub['slide_urls'])}):")
            for u in pub["slide_urls"]:
                print(f"              - {u}")

    # Manual post checklist (Instagram + TikTok)
    print("MANUAL POST (do by hand):")
    print(f"  Reel/Video (local) : {result['video_path']}")
    if pub.get("video_url"):
        print(f"  Reel/Video (R2)    : {pub['video_url']}")
    print("  Carousel slides    :")
    for i, p in enumerate(result["slides"], 1):
        print(f"    {i}. local: {p}")
        if pub.get("slide_urls") and i - 1 < len(pub["slide_urls"]):
            print(f"       R2   : {pub['slide_urls'][i - 1]}")
    print(line, flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Podcast automation pipeline (ingest -> render)")
    parser.add_argument(
        "feed",
        nargs="?",
        default=config.DEFAULT_FEED,
        help=(
            "Feed name (one of: " + ", ".join(config.PODCAST_FEEDS) + ") "
            "or a raw RSS URL. Defaults to " + config.DEFAULT_FEED + ". Ignored with --auto."
        ),
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help=(
            "Rotation mode: pick today's feed from config.ROTATION by weekday and "
            "process its latest episode. Exits 0 cleanly if today isn't a posting "
            "day, the feed is unreachable, or the latest episode was already posted."
        ),
    )
    parser.add_argument(
        "--url",
        help=(
            "Direct episode-audio URL. Bypasses RSS ingestion entirely: downloads "
            "the audio (yt-dlp) and runs the pipeline from transcribe onward, "
            "tagged as the 'manual' feed. Mutually exclusive with --auto and the "
            "positional feed."
        ),
    )
    parser.add_argument(
        "--title",
        help=(
            "Episode title to use with --url. When omitted, the title is derived "
            "from the URL's filename. Only meaningful alongside --url."
        ),
    )
    parser.add_argument(
        "--privacy",
        choices=["private", "unlisted", "public"],
        default="private",
        help="YouTube visibility for the uploaded Short (default: private)",
    )
    args = parser.parse_args()

    if args.url and args.auto:
        parser.error("--url cannot be combined with --auto")
    if args.title and not args.url:
        parser.error("--title is only valid together with --url")

    try:
        if args.auto:
            result = run_auto(privacy_status=args.privacy)
            if result is None:
                # Nothing to do (no posting day / already posted / feed down).
                sys.exit(0)
        elif args.url:
            # Direct-URL mode: skip rss_ingest, download the audio, then run the
            # normal pipeline from transcribe onward as the "manual" feed.
            logger.info("Direct URL mode: %s", args.url)
            episode = rss_ingest.fetch_from_url(args.url, title=args.title)
            result = run("manual", episode=episode, privacy_status=args.privacy)
        else:
            result = run(args.feed, privacy_status=args.privacy)
    except Exception:
        # Log the full traceback (the preceding "[n/7]" line shows which step
        # was running) and exit non-zero so genuine pipeline failures never pass
        # silently. (Feed/no-episode no-ops are handled inside run_auto and exit 0.)
        logger.exception("Pipeline FAILED")
        sys.exit(1)

    _print_summary(result)
