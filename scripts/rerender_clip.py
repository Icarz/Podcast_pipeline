"""One-off: re-render the Jordan Peterson clip with a corrected, complete window.

The auto run cut the clip mid-sentence ("...a hole that you fell in") and dropped
the payoff. This re-renders with a self-contained window that ENDS on the payoff
("...you don't know how you got there."), reusing the SAME four already-approved
Pexels clips (so footage doesn't change) and the cached transcript/plan.

Run from repo root:
    .\\venv\\Scripts\\python.exe scripts\\rerender_clip.py
"""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
from modules import video_gen  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

PLAN = os.path.join(config.TMP_DIR, "The_Jordan_B_Peterson_Podcast_Structuring_Your_World_View.plan.json")
AUDIO = os.path.join(config.TMP_DIR, "The_Jordan_B_Peterson_Podcast_Structuring_Your_World_View.mp3")

# Corrected window: start on "you never imagine..." (skips the stutter), end on
# the full payoff sentence "...you don't know how you got there."
NEW_START = 333.0
NEW_END = 383.3

# The four approved background clips, in original slot order.
BG = [
    os.path.join(config.TMP_DIR, "bg_e4fda8e3f2d6_33795615.mp4"),
    os.path.join(config.TMP_DIR, "bg_c441f085b6da_33766128.mp4"),
    os.path.join(config.TMP_DIR, "bg_c13beef288ee_27223237.mp4"),
    os.path.join(config.TMP_DIR, "bg_9c6641afd4e6_8679942.mp4"),
]


def main() -> None:
    with open(PLAN, encoding="utf-8") as f:
        plan = json.load(f)
    transcript, highlights = plan["transcript"], plan["highlights"]

    old = (highlights["clip_start"], highlights["clip_end"])
    highlights["clip_start"] = NEW_START
    highlights["clip_end"] = NEW_END
    # Persist so the plan stays consistent with what we render.
    with open(PLAN, "w", encoding="utf-8") as f:
        json.dump({"transcript": transcript, "highlights": highlights}, f)
    print(f"Clip window: {old[0]:.2f}-{old[1]:.2f}s -> {NEW_START:.2f}-{NEW_END:.2f}s "
          f"({NEW_END - NEW_START:.1f}s)", flush=True)

    missing = [b for b in BG if not os.path.exists(b)]
    if missing:
        raise SystemExit(f"Missing approved bg clip(s): {missing}")

    out = video_gen.build_video(
        AUDIO, transcript["words"], highlights,
        podcast_name=config.BRAND_NAME, background_images=BG,
    )
    print(f"\nRe-rendered: {out}", flush=True)


if __name__ == "__main__":
    main()
