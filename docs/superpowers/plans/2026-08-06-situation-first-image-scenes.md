# Situation-First IMAGE_SCENES Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Rewrite `modules/script_gen.py`'s IMAGE_SCENES prompt section so the
6 generated wolf scenes are built from a script-grounded situation first, with
a prop only appearing when the situation naturally contains one — replacing
the current "every scene needs a literal prop, described face-on" rule that
produced an invented bank balance and a sign-holding "product shot."

**Architecture:** Pure prompt-text change inside the `SYSTEM_PROMPT` string
constant in `modules/script_gen.py`. No data-contract, schema, or
downstream-module changes — `image_scenes` stays `{"beat", "concept",
"action", "setting", "camera"}` exactly as `_validate()`,
`_normalize_image_scenes()`, `_scene_safety_gate()`, `image_gen.py`,
`video_gen.py`, and `slide_gen.py` already expect.

**Tech Stack:** Python 3.12, no new dependencies.

## Global Constraints

- No new schema keys on `image_scenes` objects — reuse `concept` and `action`
  to carry the new requirements (per spec's "No data-contract changes").
- Do not touch `image_gen.py`'s `STYLE_BLOCK`, `BEAT_MOODS`, `DEFAULT_OUTFIT`,
  or `NEGATIVE_BLOCK` — those are locked visual-identity constants, out of
  scope for this content-only change (per spec's "Style stays out of scope").
- No few-shot examples in the prompt — rules-only, per spec's explicit
  constraint.
- No pytest suite exists for this repo. Verification is running
  `python -m modules.script_gen` (a real Claude API call) and reading the
  output, per the project's existing `__main__`-harness testing convention.
- Keep the FACING RULE / SCREENS / WRITTEN PROP TEXT paragraphs already in
  the file (landed in a prior fast-follow fix) — they already use
  conditional "if a prop is used" phrasing and stay compatible with props
  becoming optional; do not rewrite them in this task.

---

### Task 1: Rewrite IMAGE_SCENES STEP 1 + STEP 2 for situation-first scenes

**Files:**
- Modify: `modules/script_gen.py:218-225` (STEP 1 content analysis)
- Modify: `modules/script_gen.py:242-243` (the `"concept"` field description)
- Modify: `modules/script_gen.py:244-250` (the `"action"` field's opening —
  everything before the already-existing FACING RULE paragraph)
- Modify: `modules/script_gen.py:287-293` (delete the standalone "AVOID LAZY
  VISUAL SHORTHAND" paragraph — folded into the new action-field text)

**Interfaces:**
- Consumes: nothing new — this is a string constant edit inside
  `SYSTEM_PROMPT`, which is read by `generate_script()` at
  `modules/script_gen.py:519` via the existing `client.messages.create(...)`
  call. No signature changes anywhere.
- Produces: an updated `SYSTEM_PROMPT` whose `image_scenes` output still
  satisfies `_validate()`'s existing checks (exactly 6 objects, exactly the 5
  keys, `beat` values matching `config.IMAGE_SCENE_BEATS`) — verified in Step
  5 below, not by any code change.

- [x] **Step 1: Edit STEP 1 to require a quoted script anchor per quarter**

In `modules/script_gen.py`, find:

```python
    "STEP 1 -- CONTENT ANALYSIS (MANDATORY, do this before writing any "
    "scene): divide YOUR SCRIPT into 4 roughly equal quarters. For EACH "
    "quarter, name in one plain sentence the SPECIFIC concept, claim, or "
    "action it expresses:\n"
    "  Q1: the problem, behavior, or situation being introduced (the hook).\n"
    "  Q2: the mechanism, tension, or why it matters.\n"
    "  Q3: the insight, reframe, or turning point.\n"
    "  Q4: the payoff, resolution, or call to action.\n\n"
```

Replace with:

```python
    "STEP 1 -- CONTENT ANALYSIS (MANDATORY, do this before writing any "
    "scene): divide YOUR SCRIPT into 4 roughly equal quarters. For EACH "
    "quarter, name in one plain sentence the SPECIFIC concept, claim, or "
    "action it expresses, AND quote a short fragment (3-8 words, "
    "near-verbatim) actually taken from that quarter's script text as "
    "proof the concept is really there -- the literal words, not a "
    "paraphrase of the general topic:\n"
    "  Q1: the problem, behavior, or situation being introduced (the hook).\n"
    "  Q2: the mechanism, tension, or why it matters.\n"
    "  Q3: the insight, reframe, or turning point.\n"
    "  Q4: the payoff, resolution, or call to action.\n\n"
```

- [x] **Step 2: Carry the quoted anchor into the `"concept"` field description**

Find:

```python
    '  "concept" : string -- one plain sentence: the specific idea from '
    "STEP 1 this scene illustrates.\n"
```

Replace with:

```python
    '  "concept" : string -- one plain sentence: the specific idea from '
    "STEP 1 this scene illustrates, INCLUDING that quarter's quoted "
    "fragment in quotation marks so the connection to the actual script "
    "is checkable at a glance (e.g. 'The script says \\'upgrade the "
    "apartment, the car\\' -- he can't feel richer no matter what he "
    "buys.').\n"
```

- [x] **Step 3: Rewrite the `"action"` field's opening to be situation-first**

Find:

```python
    '  "action"  : string -- what the wolf is physically DOING, including a '
    "LITERAL PROP tied to that quarter's actual words (a to-do list, a "
    "phone, a mirror, money, a clock -- whatever it actually is). Favor "
    "natural, in-motion actions (writing, walking, pausing, closing "
    "something) over static poses that just hold or display a prop toward "
    "the camera like a product shot. A viewer on mute should guess the "
    "topic from the image alone.\n"
```

Replace with:

```python
    '  "action"  : string -- a SPECIFIC, RECOGNIZABLE SITUATION the wolf is '
    "physically living through right now -- a moment a real person would "
    "instantly recognize (mid-commute, caught mid-habit, one small "
    "interaction), built from that quarter's quoted fragment above. Start "
    "from the situation, not from an object: ask what is actually "
    "happening to him when this idea is true BEFORE asking whether "
    "anything is in his hands. A prop may appear ONLY if the situation "
    "would naturally contain one -- never invent a situation just to give "
    "a prop something to do. The situation must still read clearly with "
    "the prop removed; if it wouldn't, the scene is a prop demonstration, "
    "not a situation, and needs to be rewritten. Never represent an "
    "abstract idea by literally duplicating one object (e.g. a row of "
    "identical shirts for 'automated choices') or with an "
    "infographic-style prop (a labeled gauge, a grid of crossed-out "
    "marks, a chart) -- those illustrate a concept, they aren't a moment "
    "a person lives through. A viewer on mute should recognize the "
    "MOMENT, not decode a symbol.\n"
```

- [x] **Step 4: Delete the now-redundant standalone "AVOID LAZY VISUAL SHORTHAND" paragraph**

Find (note the setting/camera field descriptions stay — only the paragraph
after them is removed):

```python
    '  "camera"  : string -- dynamic film-still framing: low/high angle, '
    "three-quarter view, through a window, tracking alongside a moving "
    "subject, strong foreground/background depth.\n\n"
    "AVOID LAZY VISUAL SHORTHAND: never represent an abstract idea by "
    "literally duplicating one object (e.g. a row of identical shirts for "
    "'automated choices') or with an infographic-style prop (a labeled "
    "gauge, a giant grid of crossed-out marks, a chart). Find a concrete "
    "action, gesture, or environmental detail that dramatizes the idea "
    "instead -- something happening TO or AROUND the wolf, not something "
    "displayed at the camera.\n\n"
    'WOLF_OUTFIT: ONE outfit of ordinary human clothes that plausibly works '
```

Replace with:

```python
    '  "camera"  : string -- dynamic film-still framing: low/high angle, '
    "three-quarter view, through a window, tracking alongside a moving "
    "subject, strong foreground/background depth.\n\n"
    'WOLF_OUTFIT: ONE outfit of ordinary human clothes that plausibly works '
```

- [x] **Step 5: Sanity-check the module still imports and the prompt is well-formed**

Run: `./venv/Scripts/python.exe -c "import modules.script_gen as sg; print(len(sg.SYSTEM_PROMPT)); print('concept' in sg.SYSTEM_PROMPT and 'SITUATION' in sg.SYSTEM_PROMPT)"`

Expected: prints a byte length (no syntax error) and `True`.

- [x] **Step 6: Run the real smoke harness and manually verify scene quality**

Run: `./venv/Scripts/python.exe -m modules.script_gen`

This makes a real Claude API call and prints the generated script + full
`image_scenes` package (per the existing `__main__` block at the bottom of
`modules/script_gen.py`). Read the printed `image_scenes` output and check,
for every one of the 6 scenes:

1. Does `concept` contain an actual quoted fragment, and does that fragment
   really appear in the printed `script` text (not a paraphrase)?
2. Does `action` describe a specific situation a real person would
   recognize (not "wolf holds/checks/uses object X")?
3. If a prop appears, does the situation still make sense without it, and is
   it used in at most 1 of the 6 scenes with legible text (per the existing
   WRITTEN PROP TEXT rule)?
4. No scene shows the wolf looking at a prop that also faces the camera
   (existing FACING RULE) and no phone/screen shows invented text (existing
   SCREENS rule).

If any scene fails these checks, tighten the wording from Steps 1-4 above and
re-run this step — do not add a schema field or a few-shot example (per the
Global Constraints).

- [x] **Step 7: Repeat Step 6 once more on a second topic**

Run: `./venv/Scripts/python.exe -m modules.script_gen` again (non-deterministic
— it free-picks a new topic each run). Confirm the same 4 checks from Step 6
hold on this second, independent topic before considering the prompt change
validated.

- [x] **Step 8: Commit**

```bash
git add modules/script_gen.py
git commit -m "script_gen: rewrite IMAGE_SCENES prompt for situation-first scenes

Scenes were being built prop-first (every scene required a literal
prop, described face-on to camera), which produced an invented bank
balance contradicting the script and a sign-holding 'product shot'
notepad reused across two scenes. STEP 1 now requires a quoted
script anchor per quarter; STEP 2 now starts from a recognizable
situation and only reaches for a prop if the situation naturally
has one.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Addendum: blockers hit during execution (not in the original plan)

Two issues surfaced only once real API calls ran, both fixed in the same
branch before commit:

1. **JSON-breaking quote punctuation (self-inflicted, fixed twice).** The
   first draft of Steps 1-2 asked the model to wrap the quoted script anchor
   in quotation-mark punctuation inside the `concept` field. A JSON string
   value containing literal `"` characters requires the model to escape them
   as `\"`; the first attempt at this instruction leaked a literal backslash
   into the prompt text itself (`\\'` inside a Python double-quoted string
   doesn't need escaping — Python read it as backslash-then-quote), which the
   model imitated as an invalid `\'` JSON escape and broke parsing on all 3
   retry attempts. Removing the stray backslash still left the underlying
   design flaw (asking for embedded `"` inside a JSON string is fragile
   regardless), so the actual fix was to drop quotation-mark punctuation
   from the instruction entirely — the anchor fragment is stated as plain
   text, verifiable by eye without needing quote-escaping to render correctly.
2. **`config.EXTRACT_MAX_TOKENS=4000` too low once the prompt got more
   analytically demanding (real blocker, not a wording bug).** Diagnosed with
   a standalone script that called the same `client.messages.create(...)`
   directly and inspected `response.content`/`response.stop_reason` instead
   of guessing further: the call returns an internal `thinking` content block
   that shares the same `max_tokens` budget as the JSON text answer, and the
   more demanding STEP 1 anchor-identification requirement pushed thinking-
   token usage past the old 4000-token cap consistently (`stop_reason:
   max_tokens` with zero text output). Fixed by raising
   `EXTRACT_MAX_TOKENS` to 8000 in `config.py` (out of this plan's original
   file list, but required for Steps 6-7 to run at all) — confirmed via the
   same diagnostic script (`stop_reason: end_turn`, full valid JSON) before
   re-running the real harness.

## Verification results (actual)

- **Run 1** (`individuality_vs_conformity`-adjacent topic, via the standalone
  diagnostic call after the max-tokens fix): all 6 scenes were genuine
  situations (nodding along in a meeting, leaning against a hallway wall
  staring back through the glass, standing in the emptied conference room,
  rehearsing a line at a rooftop railing, speaking up, walking out changed) —
  zero props used at all, and every `concept` traced to real, near-verbatim
  script wording ("Everyone's nodding, so you nod too," "each one assuming
  they're alone," "you don't need courage to think differently").
- **Run 2** (`individuality_vs_conformity`, via the real `python -m
  modules.script_gen` harness — passed the brand gate on attempt 2/3, an
  ordinary/expected retry unrelated to this change): scenes correctly used
  the FACING RULE when a phone did appear — one scene left it "face-down and
  untouched," another described the screen "angled toward him and away from
  the camera" — with no invented on-screen text either time, plus verbatim
  anchors throughout ("shrank back from ? the promotion, the body, the
  idea," "build the room you're actually growing into").

Both runs satisfy all 4 checks from Step 6 with no further wording changes
needed.

---

## Self-Review Notes

- **Spec coverage:** STEP 1 anchor requirement (spec's STEP 1 section) →
  Task 1 Steps 1-2. STEP 2 situation-first rewrite + folding in the lazy-
  shorthand rule (spec's STEP 2 section) → Task 1 Steps 3-4. "No data-contract
  changes" and "style stays out of scope" constraints → enforced by only
  touching the two named field descriptions, verified in Step 5. Verification
  approach (spec's "Testing / verification" section) → Task 1 Steps 6-7,
  using the exact 4 checks the spec lists.
- **Placeholder scan:** no TBD/TODO; every step shows the literal before/after
  text to edit or the literal command to run.
- **Type consistency:** no new types/fields introduced — `image_scenes`
  objects keep exactly `{"beat", "concept", "action", "setting", "camera"}`
  throughout.
