"""Synthetic-script video pipeline orchestrator.

Two-command flow, split around a manual ElevenLabs voiceover step:

    main.py
        Pick a topic, write the full script + copy + art-direction package
        (modules.script_gen), save the narration text for you to paste into
        ElevenLabs, cache the package, log it to the dedup ledger.

    main.py --render <slug> <audio_path>
        Take the ElevenLabs audio you downloaded, transcribe it (Groq
        Whisper -- for word-level caption timestamps only, not content),
        generate the 6 wolf background images, render the karaoke MP4, then
        the 6-slide carousel.

Publishing is fully MANUAL by design: the run ends with a manual-post
checklist of local file paths -- no upload APIs.
"""

import json
import logging
import os
import sys

from dotenv import load_dotenv

import config
from modules import background, script_gen, script_history, slide_gen, transcribe, video_gen

load_dotenv()

# Episode/script titles often contain non-ASCII (curly quotes, em dashes); the
# Windows console defaults to cp1252 and would crash on them. Force UTF-8 on
# the console streams so logging/printing a title never takes down the run.
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


def _slugify(text: str) -> str:
    """Filesystem-safe slug from a title, e.g. 'Your Brain Replays That!' ->
    'your_brain_replays_that'. Same scheme video_gen already uses for output
    filenames, reused here as the cache key so both stay in sync by eye."""
    slug = "".join(c if c.isalnum() else "_" for c in text.lower())
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")[:80] or "script"


def _script_txt_path(slug: str) -> str:
    return os.path.join(config.TMP_DIR, f"{slug}.script.txt")


def _plan_cache_path(slug: str) -> str:
    return os.path.join(config.TMP_DIR, f"{slug}.plan.json")


def generate() -> str:
    """Step 1: pick a topic, write the package, cache it, log it. Returns the
    slug so the caller can print the exact --render command."""
    logger.info("[1/2] Generating script (Claude %s)", config.EXTRACT_MODEL)
    highlights = script_gen.generate_script_with_retry()

    slug = _slugify(highlights["title"])
    os.makedirs(config.TMP_DIR, exist_ok=True)

    script_path = _script_txt_path(slug)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(highlights["script"] + "\n")
    logger.info("Wrote narration script: %s", script_path)

    plan_path = _plan_cache_path(slug)
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump({"highlights": highlights}, f)
    logger.info("Cached plan: %s", plan_path)

    script_history.record(
        topic_cluster=highlights["topic_cluster"],
        hook=highlights["hook"],
        title=highlights["title"],
    )

    print("\n" + "=" * 64)
    print("SCRIPT READY")
    print("=" * 64)
    print(f"Title      : {highlights['title']}")
    print(f"Topic      : {highlights['topic_cluster']}")
    print(f"Hook       : {highlights['hook']}")
    print(f"Script file: {script_path}")
    print("\nNext steps:")
    print(f"  1. Paste the contents of {script_path} into ElevenLabs.")
    print("  2. Download the generated audio.")
    print(f"  3. Run: python main.py --render {slug} <path-to-audio-file>")
    print("=" * 64, flush=True)
    return slug


def _log_manual_post(video_path: str, slides: list[str]) -> None:
    lines = [
        "MANUAL POST (all platforms, by hand):",
        f"  Short/Reel video : {video_path}",
        f"  Carousel slides ({len(slides)}):",
    ]
    lines += [f"    {i}. {p}" for i, p in enumerate(slides, 1)]
    logger.info("\n".join(lines))


def render(slug: str, audio_path: str, render_slides: bool = True) -> dict:
    """Step 2: transcribe the manually-supplied audio, generate images, render
    video + slides."""
    plan_path = _plan_cache_path(slug)
    if not os.path.exists(plan_path):
        raise FileNotFoundError(
            f"No cached plan for slug {slug!r} (expected {plan_path}). "
            "Run `python main.py` first to generate a script."
        )
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    with open(plan_path, encoding="utf-8") as f:
        highlights = json.load(f)["highlights"]

    logger.info("[2/2] Transcribing voiceover for word timestamps (Groq Whisper): %s", audio_path)
    transcript = transcribe.transcribe(audio_path)

    logger.info("Backgrounds: select (gpt-image-2 -> gradient)")
    backgrounds = background.select_backgrounds(highlights, basename=slug)
    logger.info("Backgrounds: %d file(s)", len(backgrounds))

    logger.info("Video: render karaoke MP4")
    video_path = video_gen.build_video(
        audio_path,
        transcript["words"],
        highlights,
        podcast_name=config.BRAND_NAME,
        background_images=backgrounds,
    )

    if render_slides:
        logger.info("Slides: render deck (branded on the clip's wolf images)")
        slides = slide_gen.build_slides(highlights, photo_paths=backgrounds)
    else:
        logger.info("Slides: skipped (--no-slides)")
        slides = []

    _log_manual_post(video_path, slides)
    logger.info("Pipeline complete for: %s", highlights.get("title"))
    return {"highlights": highlights, "video_path": video_path, "slides": slides}


def _print_summary(result: dict) -> None:
    h = result["highlights"]
    line = "=" * 64
    print("\n" + line)
    print("PIPELINE SUMMARY")
    print(line)
    print(f"Title       : {h.get('title')}")
    print(f"Topic       : {h.get('topic_cluster')}")
    print(f"Video MP4   : {result['video_path']}")
    print(f"Slides ({len(result['slides'])})  :")
    for p in result["slides"]:
        print(f"              - {p}")
    print(line)
    print("MANUAL POST (all platforms, by hand):")
    print(f"  Short/Reel video : {result['video_path']}")
    print("  Carousel slides  :")
    for i, p in enumerate(result["slides"], 1):
        print(f"    {i}. {p}")
    print(line, flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Synthetic-script video pipeline")
    parser.add_argument(
        "--render",
        nargs=2,
        metavar=("SLUG", "AUDIO_PATH"),
        help="Render step 2: SLUG from a prior `main.py` run, AUDIO_PATH to the "
        "ElevenLabs voiceover you downloaded for it.",
    )
    parser.add_argument(
        "--no-slides",
        action="store_true",
        help="With --render, skip the slide-carousel render; produce the karaoke video only.",
    )
    args = parser.parse_args()

    try:
        if args.render:
            slug, audio_path = args.render
            result = render(slug, audio_path, render_slides=not args.no_slides)
            _print_summary(result)
        else:
            generate()
    except Exception:
        logger.exception("Pipeline FAILED")
        sys.exit(1)
