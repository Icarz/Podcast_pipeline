# Situation-first IMAGE_SCENES prompt — design

**Date:** 2026-08-06
**Status:** Approved
**Area:** `modules/script_gen.py` (`SYSTEM_PROMPT`'s IMAGE_SCENES section only).
No other module changes; no data-contract changes.

## Problem

The first `--topic "money as freedom"` render (`more_money_same_broke_feeling_pick_your_enough_number`)
surfaced two real defects in the generated `image_scenes`, both traced back to
the same root cause — the prompt's `"action"` field rule requires every scene
to center on a named literal prop, described face-on to the camera:

1. **Scene 2** — the action field said only "checking a banking app on his
   phone," with no screen content specified. gpt-image-2 filled in its own UI
   ("ACCOUNT BALANCE $0.00"), which contradicts the actual concept (hedonic
   adaptation — feeling no richer after a raise, not literally being at zero).
   Nothing in that quarter's script ever mentions a bank balance; the prop was
   invented to satisfy the literal-prop requirement, not because the script
   called for it.
2. **Scenes 4 and 5** — the "always face the camera, fully legible" instruction
   produced a wolf writing "ENOUGH" on a notepad and then carrying that same
   notepad down a street, angled toward the camera to display it — a
   sign-holding "product shot," not a believable moment. It also reused the
   same prop across two consecutive scenes.

(A fast-follow fix already landed for the physical-consistency half of this —
a FACING RULE, a SCREENS ban on inventing on-screen content, and a 1-of-6 cap
on legible prop text, all in `script_gen.py` + a matching `image_gen.py`
`NEGATIVE_BLOCK` update.) This spec covers the deeper creative fix: scenes are
built prop-first ("what object represents this idea") instead of
situation-first ("what moment would a real person recognize"), and the STEP 1
content analysis summarizes the script loosely enough that a scene can drift
from anything the script actually says.

## Goal

Rewrite the IMAGE_SCENES section of `SYSTEM_PROMPT` so the six generated
scenes read as specific, recognizable situations tightly grounded in the
script's actual words — not generic prop demonstrations — while keeping every
existing safety/brand gate intact.

## Constraints

- **No data-contract changes.** `image_scenes` stays a list of 6 objects with
  exactly `{"beat", "concept", "action", "setting", "camera"}`. No changes to
  `_validate()`, `_normalize_image_scenes()`, or `_scene_safety_gate()` in
  `script_gen.py`, nor to `image_gen.compose_prompts()`, `video_gen.py`, or
  `slide_gen.py` — all of these key off the existing field names only.
- **Style stays out of scope.** `STYLE_BLOCK`, `BEAT_MOODS`, `DEFAULT_OUTFIT`,
  and `NEGATIVE_BLOCK` in `image_gen.py` are untouched (already-locked visual
  identity, per CLAUDE.md's content/style split) beyond today's already-landed
  facing/screens fix.
- **No few-shot examples** — user decision: rules-only, to avoid the model
  overfitting to one example topic's style and to keep the prompt shorter.
- No pytest suite exists; verification is the existing manual pattern — run
  `python -m modules.script_gen` (a real Claude call) and read the output.

## Design

### STEP 1 — content analysis gets a script anchor

Current STEP 1 asks for one plain-sentence summary per script quarter. Add a
requirement that each quarter's summary include a short quoted fragment
(3-8 words, near-verbatim) actually taken from that quarter's script text,
alongside the existing one-sentence paraphrase. This folds into the existing
`concept` field — no new schema key.

This directly targets defect #1: a scene whose prop can't be traced to a real
quoted fragment from the script is now visibly ungrounded before it's written,
instead of surfacing only after the image comes back looking wrong.

### STEP 2 — situation before prop

Reframe the `"action"` field's instructions around a specific, recognizable
**situation** — a moment a real person would recognize (mid-commute, caught
mid-habit, a specific small interaction) — rather than "the wolf holds/uses
object X." A prop may still appear, but only if the situation naturally
contains one, and the situation must read clearly with the prop removed. This
absorbs the existing "AVOID LAZY VISUAL SHORTHAND" rule as the named
anti-pattern (a lazy prop-demonstration is exactly what situation-first
writing rules out), so that rule can be deleted as a separate paragraph and
folded in.

The three rules added in the prior fast-follow — FACING RULE, SCREENS, WRITTEN
PROP TEXT (capped at 1-of-6, no reuse across scenes) — carry over unchanged;
they now describe how to handle a prop *if* the situation includes one, rather
than governing a prop that's mandatory in every scene.

### What doesn't change

- The 6-beat story arc (`problem, problem, stakes, reframe, payoff, payoff`)
  and its per-beat guidance.
- `WOLF_OUTFIT` rules, the NEVER-DEPICT blacklist, the single-figure rule.
- `_scene_safety_gate()`'s regex scan of `action`/`setting` (never `concept`)
  — situations still get written into `action`/`setting`, so the existing gate
  keeps working unmodified.

## Testing / verification

No automated test exists for prompt quality (it's not that kind of bug). Plan:

1. Run `python -m modules.script_gen` two or three times (real Claude calls,
   non-deterministic — same as every other `script_gen` smoke-test run).
2. Read the resulting `image_scenes` for each run: does every `concept`
   include a real quoted fragment from that run's script? Does every `action`
   read as a specific situation rather than a prop demonstration? Is prop text
   (if any) used at most once and on something plausible, not held up to
   camera?
3. If a run still produces a prop-first or ungrounded scene, tighten wording
   rather than adding a schema field or a few-shot example (per the
   rules-only constraint above).

This is a prompt-text-only change with no code paths to regress, so no
render/pillarbox re-verification is required — that's only needed after
`video_gen` output changes.
