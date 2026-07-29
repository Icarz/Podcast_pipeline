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

import anthropic
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
    """The final combined plan cache: tmp/<basename>.plan.json (video_gen reads
    this too). Written only once, after Stage 2 succeeds for an approved
    candidate."""
    return os.path.splitext(audio_path)[0] + ".plan.json"


def _transcript_cache_path(audio_path: str) -> str:
    """Transcribe-only cache: tmp/<basename>.transcript.json. Separate from the
    plan cache so re-running Stage 1 (e.g. across a reject-all candidate loop,
    or a second manual run before the episode is approved) never re-hits Groq,
    even though nothing has been approved yet."""
    return os.path.splitext(audio_path)[0] + ".transcript.json"


def _write_plan(cache_path: str, transcript: dict, highlights: dict) -> None:
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"transcript": transcript, "highlights": highlights}, f)


def _load_or_build_transcript(audio_path: str) -> dict:
    """Return the transcript dict, reusing a cache when present.

    Transcribe-only (Groq). The candidate/copy extraction (Claude) happens
    later, after a human picks a candidate, and must not force a
    re-transcription just because the pick changes or is rejected.
    """
    cache_path = _transcript_cache_path(audio_path)
    if os.path.exists(cache_path):
        logger.info("Transcript cache HIT: %s (skipping Groq)", os.path.basename(cache_path))
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    logger.info("[2/6] Transcribe (Groq Whisper): %s", os.path.basename(audio_path))
    transcript = transcribe.transcribe(audio_path)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(transcript, f)
    logger.info("Cached transcript: %s", os.path.basename(cache_path))
    return transcript


def _print_candidates(candidates: list[dict]) -> None:
    print("\nCandidate clips:")
    for i, c in enumerate(candidates, 1):
        flag = "  [!] METAPHOR HOOK — verify the payoff lands in the first sentence" \
            if ai_extract.is_metaphor_hook(c["hook"]) else ""
        print(f"  {i}. [{c['clip_start']:.1f}-{c['clip_end']:.1f}s] {c['hook']!r}{flag}")
        print(f"     exposes: {c.get('exposes', '')}")
        print(f"     reframe: {c.get('reframe', '')}")
        print(f"     payoff : {c.get('payoff', '')}")


def _find_and_pick_candidate(transcript: dict, interactive: bool = True) -> dict | None:
    """Run Stage 1 (find + filter) and return the chosen candidate, or ``None``
    if there are no survivors or the human rejects every candidate.

    ``interactive=False`` (used by ``--auto``) skips the prompt and takes the
    top-ranked survivor automatically — no human is present for scheduled runs.
    """
    candidates = ai_extract.find_candidates(transcript)
    survivors = ai_extract.filter_candidates(candidates, transcript)
    if not survivors:
        logger.info("No viable candidates after filtering (%d raw)", len(candidates))
        return None

    if not interactive:
        logger.info("Auto mode: taking top candidate (%d survivor(s))", len(survivors))
        return survivors[0]

    _print_candidates(survivors)
    choice = input("Pick a number, or 0 to reject all: ").strip()
    if choice == "0":
        return None
    try:
        idx = int(choice) - 1
    except ValueError:
        idx = -1
    if 0 <= idx < len(survivors):
        return survivors[idx]
    print("Invalid choice; treating as reject-all.")
    return None


def _pick_episode_and_candidate(feed_arg: str, interactive: bool = True) -> tuple[dict, dict, dict]:
    """Loop episodes until a candidate is approved, or the RSS window is exhausted.

    Returns ``(episode, transcript, chosen_candidate)``. Raises ``RuntimeError``
    when every episode in the RSS window has been used/rejected. A rejected
    episode (empty shortlist, or the human rejects every candidate) is retired
    via ``posted_history.mark_used`` — same permanent-skip treatment as a
    published episode, per the design spec.
    """
    feed_url = config.PODCAST_FEEDS.get(feed_arg, feed_arg)
    host_name = config.PODCAST_HOSTS.get(feed_arg)
    used_guids = set(posted_history.load().keys())

    while True:
        picked = rss_ingest.pick_random_entry(feed_url, exclude_guids=used_guids, host_name=host_name)
        if picked is None:
            raise RuntimeError(
                f"All episodes in the RSS window for '{feed_arg}' have already been used. "
                "Delete tmp/posted_history.json to reset."
            )
        feed_obj, entry, meta = picked
        guid = meta.get("guid", "")
        logger.info("[1/6] Ingest: candidate episode %r (guid=%s)", meta.get("title"), guid)
        episode = rss_ingest.download_latest(feed_obj, entry)
        transcript = _load_or_build_transcript(episode["audio_path"])

        candidate = _find_and_pick_candidate(transcript, interactive=interactive)
        if candidate is None:
            if guid:
                posted_history.mark_used(guid=guid, feed=feed_arg, title=episode.get("title", ""))
                used_guids.add(guid)
            print(f"No candidate approved for {episode.get('title')!r} — trying next episode.")
            continue

        return episode, transcript, candidate


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


def run(
    feed_arg: str,
    episode: dict | None = None,
    privacy_status: str = "private",
    render_slides: bool = True,
    interactive: bool = True,
) -> dict:
    """Run ingest -> render -> publish for an episode of ``feed_arg``.

    ``feed_arg`` is a key in ``config.PODCAST_FEEDS`` or a raw RSS URL.

    When ``episode`` is ``None`` (RSS mode — feed runs and ``--auto``), episode
    AND candidate-clip selection are both handled internally by
    :func:`_pick_episode_and_candidate`, looping to another episode whenever a
    candidate shortlist comes up empty or (interactively) every candidate is
    rejected. If Stage 2 copywriting (:func:`ai_extract.extract_copy_with_retry`)
    exhausts its retries for the chosen candidate, the episode is retired and
    the picker runs again for a fresh candidate/episode.

    When ``episode`` is given (``--url`` direct-audio mode: there is only one
    episode, nothing to fall back to), a plan-cache hit short-circuits both
    stages entirely (e.g. a second run of the same URL); on a cache miss, a
    single Stage-1 pick and Stage-2 copy pass are attempted and any failure
    (reject-all, or Stage 2 exhaustion) raises immediately rather than looping.

    ``interactive=False`` (used by ``run_auto``) disables the human prompt and
    auto-picks the top-ranked surviving candidate at each Stage 1 pass.
    ``privacy_status`` is forwarded to YouTube (default ``"private"``).
    ``render_slides=False`` skips the carousel deck entirely (video-only run) —
    ``_publish_stage`` and the summary printer already treat an empty
    ``slides`` list as a no-op. Returns a summary dict.
    """
    podcast_name = _display_name(feed_arg)
    logger.info("Starting pipeline | feed=%s", feed_arg)

    if episode is not None:
        # --url direct-audio mode: exactly one episode, no RSS loop.
        audio_path = episode["audio_path"]
        cache_path = _plan_cache_path(audio_path)
        if os.path.exists(cache_path):
            logger.info("[2-6/6] Plan cache HIT: %s (skipping Groq + Claude)", os.path.basename(cache_path))
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            transcript, highlights = cached["transcript"], cached["highlights"]
        else:
            transcript = _load_or_build_transcript(audio_path)
            logger.info("[3/6] Find + filter clip candidates (Claude %s)", config.EXTRACT_MODEL)
            candidate = _find_and_pick_candidate(transcript, interactive=interactive)
            if candidate is None:
                raise RuntimeError(
                    f"No candidate approved for {episode.get('title')!r}; "
                    "nothing to fall back to in --url mode."
                )
            logger.info("Extracting copy (Claude %s) for approved candidate", config.EXTRACT_MODEL)
            highlights = ai_extract.extract_copy_with_retry(
                transcript, candidate["clip_start"], candidate["clip_end"], candidate,
            )
            _write_plan(cache_path, transcript, highlights)
            logger.info("Cached transcript + plan: %s", os.path.basename(cache_path))
    else:
        # RSS mode (feed runs and --auto): pick episode + candidate, looping on
        # empty shortlists, reject-all, or Stage 2 retry exhaustion.
        while True:
            episode, transcript, candidate = _pick_episode_and_candidate(feed_arg, interactive=interactive)
            audio_path = episode["audio_path"]
            cache_path = _plan_cache_path(audio_path)
            try:
                logger.info("Extracting copy (Claude %s) for approved candidate", config.EXTRACT_MODEL)
                highlights = ai_extract.extract_copy_with_retry(
                    transcript, candidate["clip_start"], candidate["clip_end"], candidate,
                )
            except (ValueError, anthropic.RateLimitError) as exc:
                logger.warning(
                    "Stage 2 copy extraction failed for %r (%s); retiring episode "
                    "and trying another candidate/episode", episode.get("title"), exc,
                )
                guid = episode.get("guid")
                if guid:
                    posted_history.mark_used(guid=guid, feed=feed_arg, title=episode.get("title", ""))
                continue
            _write_plan(cache_path, transcript, highlights)
            logger.info("Cached transcript + plan: %s", os.path.basename(cache_path))
            break

    logger.info("Episode: %s", episode.get("title"))
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Downloaded audio missing: {audio_path}")

    cs, ce = float(highlights["clip_start"]), float(highlights["clip_end"])
    logger.info("Clip window: %.2f-%.2fs (%.1fs)", cs, ce, ce - cs)

    # Retire the episode immediately after the plan is built (RSS mode only —
    # 'manual' --url episodes are never excluded from re-selection since
    # there's no RSS pool to exclude them from) so a YouTube failure later
    # doesn't leave it available for re-selection next run.
    guid = episode.get("guid")
    if guid and feed_arg != "manual":
        posted_history.mark_used(guid=guid, feed=feed_arg, title=episode.get("title", ""))

    # 4) Background selection: gpt-image-1 image -> gradient chain.
    logger.info("[4/6] Backgrounds: select (gpt-image-1 -> gradient)")
    audio_basename = os.path.splitext(os.path.basename(audio_path))[0]
    backgrounds = background.select_backgrounds(highlights, basename=audio_basename)
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
    if render_slides:
        logger.info("[6/6] Slides: render deck")
        slides = slide_gen.build_slides(highlights)
    else:
        logger.info("[6/6] Slides: skipped (render_slides=False)")
        slides = []

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


def run_auto(privacy_status: str = "private") -> dict | None:
    """Rotation entry point: pick today's feed and process a random unused episode.

    Resolves today's weekday against ``config.ROTATION`` and:
      * not a posting day            -> log and return None,
      * feed unreachable             -> log and return None,
      * all RSS entries already used -> log and return None,
      * otherwise                    -> run the full pipeline via :func:`run`
                                        with ``interactive=False`` (no human is
                                        present for scheduled runs; the top
                                        surviving candidate is taken
                                        automatically at each Stage 1 pass).

    Episode/candidate selection, filtering, and Stage 2 retry-exhaustion
    fallback are all handled inside :func:`run` (via
    :func:`_pick_episode_and_candidate`) — this function only resolves which
    feed to use today and translates a fully-exhausted RSS window or a feed
    error into a clean ``None`` return instead of a crash.
    """
    weekday = date.today().weekday()
    feed_key = config.ROTATION.get(weekday)
    if feed_key is None:
        logger.info("Auto mode: no posting day today (%s); nothing to do", _WEEKDAY_NAMES[weekday])
        return None

    logger.info("Auto mode: %s -> feed '%s' (random episode)", _WEEKDAY_NAMES[weekday], feed_key)

    try:
        return run(feed_key, privacy_status=privacy_status, interactive=False)
    except RuntimeError as exc:
        logger.info("Auto mode: %s; nothing to do", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - feed/network problems must not break scheduling
        logger.warning("Auto mode: feed '%s' unavailable (%s); exiting cleanly", feed_key, exc)
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
    parser.add_argument(
        "--no-slides",
        action="store_true",
        help="Skip the slide-carousel render; produce the karaoke video only. "
        "Not supported with --auto.",
    )
    args = parser.parse_args()

    if args.no_slides and args.auto:
        parser.error("--no-slides cannot be combined with --auto")

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
            result = run(
                "manual", episode=episode, privacy_status=args.privacy,
                render_slides=not args.no_slides, interactive=True,
            )
        else:
            result = run(
                args.feed, privacy_status=args.privacy,
                render_slides=not args.no_slides, interactive=True,
            )
    except Exception:
        # Log the full traceback (the preceding "[n/7]" line shows which step
        # was running) and exit non-zero so genuine pipeline failures never pass
        # silently. (Feed/no-episode no-ops are handled inside run_auto and exit 0.)
        logger.exception("Pipeline FAILED")
        sys.exit(1)

    _print_summary(result)
