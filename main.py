"""Podcast automation pipeline orchestrator.

End-to-end flow for the latest episode of a feed:
    RSS ingest -> transcribe -> AI extract -> background select
    -> video render (karaoke MP4) -> slide deck (PNGs)

Storage and publish (R2 / YouTube / Instagram) are intentionally SKIPPED for
now — youtube_publish/instagram_publish still raise NotImplementedError.

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
    transcribe,
    video_gen,
)

# NOTE: storage / youtube_publish / instagram_publish are deliberately NOT
# imported — those stages are skipped until the publish modules are ready.

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


def run(feed_arg: str) -> dict:
    """Run ingest -> render for the latest episode of ``feed_arg``.

    ``feed_arg`` is a key in ``config.PODCAST_FEEDS`` or a raw RSS URL.
    Returns a summary dict (episode, highlights, video_path, slides).
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

    # Publish stages are not ready — skip explicitly and loudly.
    logger.warning("Publish: SKIPPED (storage/youtube/instagram not implemented yet)")

    logger.info("Pipeline complete for: %s", episode.get("title"))
    return {
        "episode": episode,
        "highlights": highlights,
        "clip_start": cs,
        "clip_end": ce,
        "video_path": video_path,
        "slides": slides,
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
    print("Publish     : SKIPPED (storage / YouTube / Instagram not implemented)")
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
    args = parser.parse_args()

    try:
        result = run(args.feed)
    except Exception:
        # Log the full traceback (the preceding "[n/6]" line shows which step
        # was running) and exit non-zero so failures never pass silently.
        logger.exception("Pipeline FAILED")
        sys.exit(1)

    _print_summary(result)
