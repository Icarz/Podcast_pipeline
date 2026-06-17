"""Smoke test for the payoff-anchored _trim_to_cap (no API calls).

Builds synthetic word lists with known sentence boundaries and asserts:
  (a) an over-cap thought ending on a payoff sentence keeps clip_end fixed and
      moves clip_start forward to a sentence boundary, window within [floor, cap];
  (b) a single payoff sentence longer than the cap falls back to trimming
      clip_end (payoff sacrificed);
  (c) an already-fitting clip is left untouched.

Run from repo root:
    .\\venv\\Scripts\\python.exe scripts\\test_trim_to_cap.py
Exit 0 = all pass, 1 = a failure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
from modules.ai_extract import _trim_to_cap  # noqa: E402

CAP = config.CLIP_WINDOW_MAX_HARD_SECONDS
FLOOR = config.CLIP_WINDOW_MIN_SECONDS


def _words(n: int, enders: set[int]) -> list[dict]:
    """n one-second words; words whose index is in ``enders`` end a sentence."""
    out = []
    for i in range(n):
        w = f"w{i}" + ("." if i in enders else "")
        out.append({"word": w, "start": float(i), "end": float(i + 1)})
    return out


def _check(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def main() -> int:
    results = []

    # (a) over-cap, payoff sentence fits -> move clip_start forward, keep clip_end.
    # Sentences every 10s (enders at 9,19,...,69); clip 0..70 (70s > cap).
    words = _words(70, {i for i in range(9, 70, 10)})
    data = {"clip_start": 0.0, "clip_end": 70.0}
    _trim_to_cap(data, words)
    win = data["clip_end"] - data["clip_start"]
    results.append(_check(
        "a: payoff preserved",
        data["clip_end"] == 70.0 and data["clip_start"] > 0.0 and FLOOR <= win <= CAP,
        f"start {data['clip_start']} end {data['clip_end']} window {win}s",
    ))

    # (b) single payoff sentence > cap (enders only at 5 and 69) -> fallback trims end.
    words = _words(70, {5, 69})
    data = {"clip_start": 0.0, "clip_end": 70.0}
    _trim_to_cap(data, words)
    results.append(_check(
        "b: fallback trims clip_end",
        data["clip_start"] == 0.0 and data["clip_end"] < 70.0,
        f"start {data['clip_start']} end {data['clip_end']}",
    ))

    # (c) already fits -> no-op.
    words = _words(40, {i for i in range(9, 40, 10)})
    data = {"clip_start": 0.0, "clip_end": 40.0}
    _trim_to_cap(data, words)
    results.append(_check(
        "c: in-cap no-op",
        data == {"clip_start": 0.0, "clip_end": 40.0},
        f"start {data['clip_start']} end {data['clip_end']}",
    ))

    ok = all(results)
    print(f"\n{'ALL PASS' if ok else 'FAILURES PRESENT'} ({sum(results)}/{len(results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
