# Niche narrowing + audience-matching upgrade

Date: 2026-07-05

## Problem

The channel is dropping weekly multi-podcast rotation and going forward with
only two sources: **Mindset Mentor** (Rob Dial) and **The Jordan B. Peterson
Podcast**. Two things need to change to match:

1. **Config surface area** — `config.py` still defines/rotates through five
   other feeds (`mel_robbins`, `daily_stoic`, `brendon_burchard`, `jay_shetty`,
   `modern_wisdom`, `jocko_podcast`, `mark_manson`) that are no longer part of
   the plan. Leaving them in invites accidental use and rotation drift.
2. **Audience matching** — the user wants every clip to earn its view by
   solving a real problem for this specific audience: either it teaches
   something genuinely new, or it takes something the audience half-knows
   already and reframes it so it finally lands. This is a refinement of the
   existing brand-mission/topic-priority logic in `ai_extract.py`, not a
   replacement of it — the proven TIER 1 fear/anxiety cluster and existing
   hook rules stay as-is.

## Changes

### 1. `config.py` — narrow the feed surface

- `PODCAST_FEEDS`: delete entries for `mel_robbins`, `daily_stoic`,
  `brendon_burchard`, `jay_shetty`, `modern_wisdom`, `jocko_podcast`,
  `mark_manson`. Only `mindset_mentor` and `jordan_peterson` remain.
- `PODCAST_HOSTS`: delete the matching entries for the same removed feeds.
- `ROTATION`: currently maps weekdays to feed keys, several of which are being
  deleted (`brendon_burchard` Mon, `mel_robbins` Tue, `daily_stoic` Fri). If
  left as-is, `--auto` would `KeyError` on those weekdays. Collapse `ROTATION`
  to only the two surviving feeds, e.g.:
  ```python
  ROTATION = {
      2: "jordan_peterson",  # Wednesday
      5: "mindset_mentor",   # Saturday
  }
  ```
  (Exact weekday assignment isn't load-bearing since scheduled automation is
  already disabled per prior session — this just keeps `--auto` from crashing
  if ever invoked manually.)
- `DEFAULT_FEED` stays `"mindset_mentor"` (unchanged).
- `EPISODE_BRAND_KEYWORDS`: add `"responsibility"`, `"ownership"`,
  `"standards"`, `"accountability"`, `"direction"` so episode-level
  pre-screening also favors the newly-prioritized topics (see below). No
  removals — the existing list stays broad since both remaining shows still
  span the full content universe.

### 2. `modules/ai_extract.py` — SYSTEM_PROMPT topic-selection upgrade

Two additions to the existing prompt, both edits to established sections
(not new sections that could conflict):

**a. NEW-OR-REFRAMED requirement** — added to the BRAND MISSION block
(immediately after the four lettered outcomes, before "The content universe
is..."). Plain-language rule: every clip must either (a) hand the viewer a
mechanism/fact they likely have not heard named before, or (b) take something
the viewer already senses or half-knows and reframe it in language or an
angle that makes it click for the first time. A clip that just restates
common self-help wisdom in the same familiar phrasing — true, but not newly
landing — fails this check and a different segment must be picked.

**b. Elevated priority sub-topics** — inside the existing TOPIC PRIORITY
section (rule 4), alongside the already-proven FEAR/ANXIETY/RUMINATION
callout in TIER 1, add three more named, actively-favored sub-topics:

- **DISCIPLINE / SELF-SABOTAGE** — the knowing-doing gap: why the viewer
  knows what to do and still doesn't do it (procrastination, avoidance,
  willpower failure, excuses).
- **PURPOSE / MEANING / DIRECTION** — feeling lost, chasing the wrong things,
  identity confusion, lack of direction.
- **RESPONSIBILITY / STANDARDS / OWNERSHIP** — taking ownership, raising
  one's own bar, rejecting excuse-making or victimhood.

These are additive priority signals within the existing TIER 1/TIER 2
structure — they don't replace or demote the proven fear/anxiety cluster,
which stays the single strongest recurring sub-topic callout since it has
the most performance data behind it. No literal "masculinity" keyword is
added anywhere (prompt or config) — it's a loaded single word with high
false-positive/false-reject risk; "responsibility/standards/ownership"
covers the same territory more safely.

### 3. `modules/ai_extract.py` — `_content_gate()` 8th criterion

Add **NEW-OR-REFRAMED** as criterion 8 in the existing Haiku gate prompt
(`_content_gate`, currently 7 criteria: PAYOFF, DENSITY, HOOK-MATCH,
UNIVERSALITY, STRUCTURE, DIGESTIBILITY, SPECIFICITY). Same format as the
existing criteria — one sentence describing the test, with a BAD/GOOD
contrast example, ending with the same "If ANY criterion clearly fails ...
respond 'NO: <reason>'" instruction already in place. This is a second,
independent check on the actual transcript text (not just the model's
written hook/insights), catching a clip that slipped through extraction
selection but is genuinely just recycled generic advice.

## Out of scope (explicitly deferred)

- Retention/watch-time mechanics (hook-payoff pacing, caption timing, etc.)
  — user confirmed topic selection is the priority; retention is a possible
  follow-up, not part of this change.
- Any change to the existing hook-formula rules, palette/scene-priority
  system, or performance-data tables in CLAUDE.md — those are validated and
  untouched.
- Re-adding removed feeds to config "for later" — user chose full removal,
  not keep-but-unused.

## Testing / verification

No pytest suite exists for this project (per CLAUDE.md). Verification is:
- `config.py` imports cleanly and `python -c "import config"` succeeds with
  only two feeds present.
- `.\venv\Scripts\python.exe -m modules.ai_extract` (or a full `main.py`
  run) still produces a valid, schema-passing plan — confirms the expanded
  SYSTEM_PROMPT didn't break JSON output or trip `_validate`.
- Manual read-through of the new SYSTEM_PROMPT additions to confirm no
  contradiction with the existing TIER 1 fear/anxiety priority or the
  DIGESTIBILITY-FIRST rule.
