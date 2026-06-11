"""Tune the bg_quality thresholds against labeled good/bad Pexels clips."""
import os
import sys

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

import config  # noqa: E402
from modules import bg_quality  # noqa: E402

# (id, label, expected) — BAD should be rejected, GOOD should pass.
CLIPS = [
    (8499763,  "jordan slot2 classroom kids/group/signage", "BAD"),
    (6549527,  "jordan slot3 open book legible text",        "BAD"),
    (30635115, "jordan slot4 dark corridor",                 "BAD"),
    (9615485,  "huberman slot1 lying in bed dim",            "BAD"),
    (26755590, "mindset slot1 runner forest golden",         "GOOD"),
    (30074961, "mindset slot3 athlete training sunrise",     "GOOD"),
    (15787200, "mindset slot4 hilltop sunset silhouette",    "GOOD"),
    (37848044, "huberman slot3 kitchen meal prep",           "GOOD"),
    (35128964, "mindset slot2 notebook flatlay",             "GOOD"),
    (34762874, "huberman slot4 giant orange sun",            "GOOD?"),
    (6550432,  "huberman slot2 hand writing blue window",    "GOOD?"),
]


def fetch_video(vid: int) -> dict | None:
    r = requests.get(
        f"https://api.pexels.com/videos/videos/{vid}",
        headers={"Authorization": os.environ.get("PEXELS_API_KEY")},
        timeout=config.PEXELS_TIMEOUT,
    )
    if r.status_code != 200:
        print(f"  (HTTP {r.status_code} for {vid})")
        return None
    return r.json()


def main() -> None:
    print(f"cv2={bg_quality._HAS_CV2}  thresholds: luma>={config.BG_BRIGHTNESS_MIN} "
          f"faces<={config.BG_FACE_COUNT_MAX} face_area<={config.BG_FACE_AREA_MAX} "
          f"text<={config.BG_TEXT_COVER_MAX}\n")
    hdr = f"{'exp':5} {'verdict':7} {'reason':22} {'luma':>5} {'faces':>5} {'fArea':>6} {'text':>6}  label"
    print(hdr)
    print("-" * len(hdr))
    for vid, label, exp in CLIPS:
        v = fetch_video(vid)
        if not v:
            continue
        ok, reason, m = bg_quality.assess(v)
        verdict = "PASS" if ok else "REJECT"
        print(f"{exp:5} {verdict:7} {str(reason or ''):22} "
              f"{str(m['luma_median']):>5} {m['faces_max']:>5} {m['face_area_max']:>6} "
              f"{m['text_cover_max']:>6}  {label}")


if __name__ == "__main__":
    main()
