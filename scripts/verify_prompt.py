"""THROWAWAY verification driver for the SYSTEM_PROMPT quality changes.

Render-only, NO publish. For each of the 3 brand sources it:
  1. loads the cached transcript from the existing plan.json (no Groq re-spend),
  2. re-runs ai_extract with the NEW prompt to get fresh highlights,
  3. fetches the real Pexels background clips (background.select_backgrounds)
     and extracts one frame per clip,
  4. renders the 5 carousel slides,
  5. copies all artifacts into output/verify/<source>/ and writes report.json
     (insight char counts, the queries, artifact paths).

It does NOT touch YouTube/R2/posted_history. footage_history.json is backed up
and reset before the run, then restored after, so each query returns its top
Pexels result (clean read of what the new queries pull).

Run:  .\\venv\\Scripts\\python.exe scripts\\verify_prompt.py
"""

import json
import logging
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from modules import ai_extract, background, slide_gen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("verify")

EPISODES = {
    "mindset_mentor": "The_Mindset_Mentor_How_To_Rewire_Your_Brain_To_Enjoy_Discipline.plan.json",
    "jordan_peterson": "The_Jordan_B_Peterson_Podcast_572_Navigating_Education_Ideology_and_Children_Answer_the_Ca.plan.json",
    "huberman_lab": "Huberman_Lab_Eating_for_Better_Sleep_Foods_that_Improve_Metabolic_Health.plan.json",
}

VERIFY_DIR = os.path.join(config.OUTPUT_DIR, "verify")
FFMPEG = "ffmpeg"


def _extract_frame(video_path: str, out_png: str, at_seconds: float = 1.0) -> bool:
    try:
        subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error", "-ss", str(at_seconds),
             "-i", video_path, "-frames:v", "1", out_png],
            check=True,
        )
        return os.path.exists(out_png)
    except Exception as exc:  # noqa: BLE001
        log.warning("frame extract failed for %s: %s", video_path, exc)
        return False


def run_source(source: str, plan_name: str) -> dict:
    plan_path = os.path.join(config.TMP_DIR, plan_name)
    with open(plan_path, encoding="utf-8") as f:
        cached = json.load(f)
    transcript = cached["transcript"]

    log.info("[%s] re-extracting with NEW prompt ...", source)
    highlights = ai_extract.extract_highlights_with_retry(transcript)

    # Persist fresh highlights back to the plan cache (keep the transcript).
    cached["highlights"] = highlights
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(cached, f)

    dst = os.path.join(VERIFY_DIR, source)
    os.makedirs(dst, exist_ok=True)

    log.info("[%s] selecting backgrounds (Pexels) ...", source)
    bgs = background.select_backgrounds(highlights)
    frames = []
    for i, p in enumerate(bgs, 1):
        ext = os.path.splitext(p)[1].lower()
        out = os.path.join(dst, f"bg_slot{i}.png")
        if ext == ".mp4":
            if _extract_frame(p, out):
                frames.append(out)
        elif ext == ".png":
            shutil.copy(p, out)
            frames.append(out)

    log.info("[%s] rendering slides ...", source)
    slides = slide_gen.build_slides(highlights)
    slide_copies = []
    for i, p in enumerate(slides, 1):
        out = os.path.join(dst, f"slide_{i}.png")
        shutil.copy(p, out)
        slide_copies.append(out)

    insights = highlights.get("insights", [])
    report = {
        "source": source,
        "title": highlights.get("title"),
        "clip_window": [highlights.get("clip_start"), highlights.get("clip_end")],
        "insights": [{"text": t, "chars": len(t), "over_110": len(t) > 110} for t in insights],
        "search_queries": highlights.get("search_queries"),
        "video_queries": highlights.get("video_queries"),
        "bg_kind": "video" if bgs and bgs[0].lower().endswith(".mp4") else "image",
        "bg_paths": bgs,
        "frame_pngs": frames,
        "slide_pngs": slide_copies,
    }
    with open(os.path.join(dst, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return report


def main() -> None:
    os.makedirs(VERIFY_DIR, exist_ok=True)

    # Back up + reset footage history so each query returns its top result.
    fh = config.FOOTAGE_HISTORY_PATH
    fh_backup = fh + ".verify_backup"
    if os.path.exists(fh):
        shutil.copy(fh, fh_backup)
        os.remove(fh)
        log.info("footage_history backed up -> %s and reset", fh_backup)

    summary = {}
    try:
        for source, plan_name in EPISODES.items():
            summary[source] = run_source(source, plan_name)
    finally:
        # Restore original footage history (discard verify-run picks).
        if os.path.exists(fh_backup):
            shutil.copy(fh_backup, fh)
            os.remove(fh_backup)
            log.info("footage_history restored from backup")

    with open(os.path.join(VERIFY_DIR, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("\n==== INSIGHT CHAR COUNTS ====")
    for source, rep in summary.items():
        print(f"\n{source}: {rep['title']}")
        for n, ins in enumerate(rep["insights"], 1):
            flag = "  <-- OVER 110" if ins["over_110"] else ""
            print(f"  insight {n}: {ins['chars']} chars{flag}")
        print(f"  video_queries: {[v['query'] for v in rep['video_queries']]}")
        print(f"  search_queries: {rep['search_queries']}")
    print("\nArtifacts under:", VERIFY_DIR)


if __name__ == "__main__":
    main()
