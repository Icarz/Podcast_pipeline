"""Post-render verification for the finished karaoke MP4.

Enforces the render checks CLAUDE.md mandates AFTER every render, so a bad clip
never gets a thumbs-up on a visual tail-grab alone:

  1. DURATION  — must stay UNDER 60.0s (>=60s lets YouTube strip the Pixabay
     music bed on Shorts). Warns if under CLIP_WINDOW_MIN_SECONDS.
  2. DIMENSIONS — must be exactly SLIDE_WIDTH x SLIDE_HEIGHT (1080x1920, the
     video canvas reuses the slide dims).
  3. PILLARBOX  — the edge-brightness scan: sample one frame every ~2s across
     the WHOLE video, measure mean brightness of the leftmost/rightmost
     EDGE_STRIP_PX strips over the middle 60% of height, and FAIL if one edge is
     dark (< EDGE_DARK) while the opposite is lit (> EDGE_LIT) — the signature of
     a black bar added by a mis-covered background slot.

To avoid false-positiving on footage that is *genuinely* dark on one side, a
suspected bar is confirmed only when the dark edge is a CONTIGUOUS near-zero
column run ending in a SHARP cliff (a real added bar) rather than a soft
gradient (real content). The script reports the bar width so a human can judge,
and points at the raw source clips (tmp/bg_*.mp4) to probe further.

Usage (from repo root, venv interpreter):
    .\\venv\\Scripts\\python.exe scripts\\verify_render.py            # newest output/videos/*.mp4
    .\\venv\\Scripts\\python.exe scripts\\verify_render.py path\\to\\clip.mp4

Exit code 0 = PASS, 1 = FAIL, 2 = could not run (missing file / ffmpeg).
ffmpeg/ffprobe must be on PATH (winget Gyan.FFmpeg) — separate from MoviePy's
bundled imageio binary.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

# --- scan tuning ---
SAMPLE_EVERY_S = 2.0      # one frame every ~2s across the whole clip
EDGE_STRIP_PX = 15        # width of the left/right strip we average
MID_BAND = 0.60           # use the middle 60% of frame height (skip top/bottom)
EDGE_DARK = 8.0           # an edge strip mean below this is "dark"
EDGE_LIT = 15.0           # ...is a bar only if the OPPOSITE edge is above this
BAR_COL_DARK = 8.0        # a column counts as "bar" if its mean is below this
CLIFF_JUMP = 12.0         # brightness rise from bar into content = a sharp cliff
HARD_DURATION_CAP = 60.0  # >= this loses the music bed on YouTube Shorts


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _newest_render() -> str | None:
    vids = glob.glob(os.path.join(config.VIDEO_DIR, "*.mp4"))
    return max(vids, key=os.path.getmtime) if vids else None


def _probe(path: str) -> tuple[float, int, int]:
    """(duration_seconds, width, height) via ffprobe."""
    cp = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "format=duration:stream=width,height",
        "-of", "json", path,
    ])
    if cp.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {cp.stderr.strip()}")
    data = json.loads(cp.stdout)
    dur = float(data.get("format", {}).get("duration", 0.0))
    st = (data.get("streams") or [{}])[0]
    return dur, int(st.get("width", 0)), int(st.get("height", 0))


def _extract_frames(path: str, out_dir: str) -> list[str]:
    """Dump one frame every SAMPLE_EVERY_S to out_dir; return sorted paths."""
    fps = 1.0 / SAMPLE_EVERY_S
    cp = _run([
        "ffmpeg", "-v", "error", "-i", path,
        "-vf", f"fps={fps}", os.path.join(out_dir, "f_%05d.png"),
    ])
    if cp.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extract failed: {cp.stderr.strip()}")
    return sorted(glob.glob(os.path.join(out_dir, "f_*.png")))


def _bar_width(col_means: np.ndarray, from_left: bool) -> int:
    """Contiguous run (from the chosen edge) of columns below BAR_COL_DARK that
    ends in a sharp brightness cliff. Returns 0 if it's a soft gradient, not a bar.
    """
    seq = col_means if from_left else col_means[::-1]
    run = 0
    for v in seq:
        if v < BAR_COL_DARK:
            run += 1
        else:
            break
    if run == 0 or run >= len(seq):
        return 0
    # Confirm a SHARP cliff: the first lit column after the run must jump clearly.
    if seq[run] - seq[run - 1] >= CLIFF_JUMP:
        return run
    return 0


def _scan_frame(path: str) -> dict:
    arr = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
    luma = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    h, w = luma.shape
    y0, y1 = int(h * (1 - MID_BAND) / 2), int(h * (1 + MID_BAND) / 2)
    band = luma[y0:y1, :]
    left = float(band[:, :EDGE_STRIP_PX].mean())
    right = float(band[:, -EDGE_STRIP_PX:].mean())
    col_means = band.mean(axis=0)  # per-column brightness across the band
    suspect = (left < EDGE_DARK and right > EDGE_LIT) or (right < EDGE_DARK and left > EDGE_LIT)
    bar = 0
    side = None
    if suspect:
        if left < right:
            bar, side = _bar_width(col_means, from_left=True), "left"
        else:
            bar, side = _bar_width(col_means, from_left=False), "right"
    return {"left": left, "right": right, "bar_px": bar, "side": side}


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else _newest_render()
    if not path or not os.path.exists(path):
        print(f"FAIL: no render found ({path or config.VIDEO_DIR})")
        return 2
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        print("FAIL: ffmpeg/ffprobe not on PATH")
        return 2

    print(f"Verifying: {path}\n")
    fails: list[str] = []

    # 1. duration
    dur, w, h = _probe(path)
    if dur >= HARD_DURATION_CAP:
        fails.append(f"duration {dur:.2f}s >= {HARD_DURATION_CAP}s (loses music bed on Shorts)")
    print(f"  duration : {dur:.2f}s  {'OK' if dur < HARD_DURATION_CAP else 'FAIL'}")
    if dur < config.CLIP_WINDOW_MIN_SECONDS:
        print(f"             WARNING: under CLIP_WINDOW_MIN_SECONDS ({config.CLIP_WINDOW_MIN_SECONDS}s)")

    # 2. dimensions (the video canvas reuses SLIDE_WIDTH/HEIGHT = 1080x1920)
    want = (config.SLIDE_WIDTH, config.SLIDE_HEIGHT)
    if (w, h) != want:
        fails.append(f"dimensions {w}x{h} != {want[0]}x{want[1]}")
    print(f"  dimensions: {w}x{h}  {'OK' if (w, h) == want else 'FAIL'}")

    # 3. pillarbox edge scan
    tmp = tempfile.mkdtemp(prefix="verify_render_")
    try:
        frames = _extract_frames(path, tmp)
        bars = []
        for i, fp in enumerate(frames):
            r = _scan_frame(fp)
            if r["bar_px"] > 0:
                bars.append((i * SAMPLE_EVERY_S, r))
        print(f"  edge scan : {len(frames)} frames sampled  "
              f"{'OK' if not bars else f'FAIL ({len(bars)} with bars)'}")
        if bars:
            fails.append(f"pillarbox bar on {len(bars)} frame(s)")
            for ts, r in bars[:20]:
                print(f"             t={ts:5.1f}s  {r['side']:>5} bar ~{r['bar_px']}px  "
                      f"(L={r['left']:.0f} R={r['right']:.0f})")
            print("             -> confirm vs genuinely-dark footage by probing the "
                  "raw source clips in tmp/bg_*.mp4")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print("RESULT: FAIL")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
