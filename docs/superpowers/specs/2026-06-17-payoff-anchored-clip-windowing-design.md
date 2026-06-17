# Payoff-anchored clip windowing — design

**Date:** 2026-06-17
**Status:** Approved
**Area:** `modules/ai_extract.py` (clip-window selection), `modules/ai_extract.py` `SYSTEM_PROMPT`

## Problem

A rendered clip must be a complete, self-contained thought that **ends on its
payoff** (the concluding line that delivers the brand's HOPE + AGENCY beat). The
Jordan Peterson "Structuring Your World View" auto-run instead ended mid-sentence
on "…a hole that you fell in" and dropped the resolving line ("…and you might
fall in it again, because you don't know how you got there").

Root cause: every length-correction mechanism in extraction only ever shortens
from the **end**. When the model's complete thought runs ~58–62s,
`_trim_to_cap` pulls `clip_end` *backward* to fit under the 58s hard cap — which
either lands on an earlier sentence (payoff lost) or keeps a mid-sentence cut.
Nothing tries moving `clip_start` **forward** to preserve the conclusion, which
is exactly the manual fix that was applied by hand.

## Goal

When an over-cap clip must be shortened, preserve the payoff: keep `clip_end`
fixed and trim from the **front** by pushing `clip_start` forward to a sentence
boundary. Fall back to the existing backward `clip_end` trim only in the rare
case where the payoff sentence alone exceeds the cap.

## Constraints

- Window must stay within `[CLIP_WINDOW_MIN_SECONDS (25), CLIP_WINDOW_MAX_HARD_SECONDS (58)]`.
  The 58s cap keeps the finished Short under 60s (YouTube strips the music bed at ≥60s).
- Extraction is non-deterministic — recover in code, never re-ask for a "better" pick.
- No pytest suite exists; verification is a per-module/script smoke harness.

## Design

### 1. `_trim_to_cap` becomes payoff-anchored (core change)

When `clip_end - clip_start > cap`:

- Treat `clip_end` (the payoff) as **fixed**.
- Build the list of sentence-opening word start times (first word, or any word
  whose predecessor ends a sentence — the same definition `_snap_to_sentences`
  uses today).
- Valid new-start band: `new_start ∈ [clip_end - cap, clip_end - floor]`
  (window lands between the floor and the cap, still ending on the payoff).
- Choose the **earliest** sentence-opening word in that band → longest window
  that fits → maximum setup context retained with the payoff intact.
- **Fallback:** if no sentence-opening word exists at/after `clip_end - cap`
  (the payoff sentence alone exceeds the cap), revert to the current behavior —
  pull `clip_end` back to the latest sentence boundary under the cap. Logged
  distinctly.
- Guard: never produce `new_start >= clip_end`; if the band is empty for any
  other reason, fall back to the backward end-trim.

### 2. Shared sentence-boundary helper (DRY)

Factor the sentence-opening-start computation out of `_snap_to_sentences` into
`_sentence_open_starts(words)` so `_trim_to_cap` and `_snap_to_sentences` agree
on the definition. `_ends_sentence` already covers the closing side.

### 3. `SYSTEM_PROMPT` tweak (belt-and-suspenders)

In the `clip_end` rule (~`ai_extract.py:176-178`) and the clip-length rule
(~`:325-327`), add one directive: when a complete thought runs long, **anchor on
the concluding/payoff sentence and set `clip_start` as late as needed to fit
under the cap** — rather than starting early and risking the payoff. The code
change is the real guarantee; this nudges the model to land closer on its own.

### 4. Ordering / convergence (no code reorder)

Call order in `extract_highlights` is unchanged
(`_extend_to_floor` → `_trim_to_cap` → validate/gate → `_snap_to_sentences` →
`_extend_to_floor` → `_trim_to_cap`). After the new trim lands `clip_start` on a
sentence-opening word, `_snap_to_sentences` snaps start to the nearest opening
word (itself → stable) and the second `_extend_to_floor`/`_trim_to_cap` are
no-ops. No oscillation.

### 5. Logging

Two distinct messages:
- normal: `clip too long; moved clip_start %.2f -> %.2f to preserve payoff (now %.1fs)`
- fallback: `payoff sentence exceeds cap; trimmed clip_end %.2f -> %.2f (now %.1fs)`

## Out of scope

- `_extend_to_floor` (too-short rescue) — unchanged.
- `_snap_to_sentences` backward end-snap — unchanged.
- `video_gen.py` render-time guard — stays as the last-ditch safety net.

## Verification

No-API smoke script `scripts/test_trim_to_cap.py` with synthetic word lists:
- (a) a >58s thought ending on a payoff sentence → assert `clip_end` unchanged,
  `clip_start` moved forward to a sentence boundary, window in [25, 58].
- (b) a single payoff sentence longer than 58s → assert fallback trims `clip_end`.
- (c) an already-fitting clip → assert no-op.

Optional live check: delete the JP `*.plan.json` and re-extract (one Claude call)
to confirm the real pick now lands payoff-complete.
