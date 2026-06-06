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

Re-runs reuse the tmp/<basename>.plan.json cache that this module (and the
video_gen harness) writes, so Groq/Claude are only hit once per episode.
"""

import json
import logging
import os
import sys

from dotenv import load_dotenv

import config
from modules import (
    ai_extract,
    background,
    rss_ingest,
    slide_gen,
    storage,
    transcribe,
    video_gen,
    youtube_publish,
)

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

    Known feed keys use the single-source brand (``config.BRAND_NAME``); a raw
    URL yields an empty name (build_video then skips the watermark) since those
    one-off runs aren't branded.
    """
    if feed_arg in config.PODCAST_FEEDS:
        return config.BRAND_NAME
    return ""


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
            highlights = ai_extract.extract_highlights(transcript)
            _write_plan(cache_path, transcript, highlights)
        return transcript, highlights

    logger.info("[2/6] Transcribe (Groq Whisper): %s", os.path.basename(audio_path))
    transcript = transcribe.transcribe(audio_path)
    logger.info("[3/6] Extract clip plan (Claude %s)", config.EXTRACT_MODEL)
    highlights = ai_extract.extract_highlights(transcript)
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


def run(feed_arg: str, privacy_status: str = "private") -> dict:
    """Run ingest -> render -> publish for the latest episode of ``feed_arg``.

    ``feed_arg`` is a key in ``config.PODCAST_FEEDS`` or a raw RSS URL.
    ``privacy_status`` is forwarded to the YouTube upload (default ``"private"``).
    Returns a summary dict (episode, highlights, video_path, slides, publish).
    """
    feed_url = config.PODCAST_FEEDS.get(feed_arg, feed_arg)
    podcast_name = _display_name(feed_arg)
    logger.info("Starting pipeline | feed=%s -> %s", feed_arg, feed_url)

    # 1) Ingest: parse the feed and download the latest episode's audio.
    logger.info("[1/6] Ingest: latest episode from feed")
    episode = rss_ingest.fetch_latest(feed_url)
    audio_path = episode["audio_path"]
    logger.info("Episode: %s", episode.get("title"))
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Downloaded audio missing: {audio_path}")

    # 2-3) Transcribe + extract clip plan (cached together as one plan.json).
    transcript, highlights = _load_or_build_plan(audio_path)
    cs, ce = float(highlights["clip_start"]), float(highlights["clip_end"])
    logger.info("Clip window: %.2f-%.2fs (%.1fs)", cs, ce, ce - cs)

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
            "or a raw RSS URL. Defaults to " + config.DEFAULT_FEED
        ),
    )
    parser.add_argument(
        "--privacy",
        choices=["private", "unlisted", "public"],
        default="private",
        help="YouTube visibility for the uploaded Short (default: private)",
    )
    args = parser.parse_args()

    try:
        result = run(args.feed, privacy_status=args.privacy)
    except Exception:
        # Log the full traceback (the preceding "[n/6]" line shows which step
        # was running) and exit non-zero so failures never pass silently.
        logger.exception("Pipeline FAILED")
        sys.exit(1)

    _print_summary(result)
