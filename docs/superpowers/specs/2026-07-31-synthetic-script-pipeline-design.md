# Synthetic script + ElevenLabs voiceover pipeline — design

**Date:** 2026-07-31
**Status:** Approved
**Area:** replaces `modules/rss_ingest.py`, `modules/ai_extract.py`,
`modules/candidate_bank.py`, `modules/posted_history.py`, and `main.py`'s
orchestration; leaves `modules/transcribe.py`, `modules/image_gen.py`,
`modules/background.py`, `modules/video_gen.py`, `modules/slide_gen.py`
unchanged.

## Problem

The pipeline currently sources its spoken content by picking a random podcast
episode, transcribing it, and extracting a clip. A manual proof-of-concept
(2026-07-31) validated an alternative: write a script from scratch for a topic
already known to perform (fear/anxiety mechanisms, individuality vs
conformity, money-as-freedom), generate the voiceover with ElevenLabs, and
render it through the existing image/video/slide stack unchanged. The test
render (`brain_replays_the_argument.mp4`) passed the pillarbox/duration
verification and the user judged the output good — voice, images, and pacing
all worked using the pipeline's existing downstream modules with zero changes.

The user has decided to switch to this as the primary content source and
drop podcast sourcing entirely, rather than run both.

## Goal

Replace the podcast-ingestion half of the pipeline (RSS → download → transcribe
→ two-stage clip extraction) with a topic-driven script generator, while
reusing every downstream module (images, video, slides) exactly as they exist
today. ElevenLabs generation stays a manual step (paste script → download
audio → hand back to the pipeline) — no ElevenLabs API integration in this
pass.

## Constraints

- No pytest suite — per-module `__main__` harnesses remain the only tests.
- Brand/content rules are unchanged: hook formula, content-direction filter
  (self-awareness / new perspective / hope+agency / self-knowledge), banned
  topics (relationships, personal-finance tips, banter/trivia), the 6-scene
  wolf-mascot visual arc and its safety gate all carry over verbatim from
  `ai_extract.py` — only *how* the content arrives changes (written from a
  topic, not extracted from a transcript).
- `video_gen.build_video` and `slide_gen.build_slides` need no changes —
  proven today by rendering a fully synthetic script through them unmodified.
- Secrets stay at 3 keys (`ANTHROPIC_API_KEY`, `GROQ_API_KEY`,
  `OPENAI_API_KEY`). `GROQ_API_KEY` is still required: `transcribe.py` is
  still used, now to get word-level timestamps from the ElevenLabs-returned
  audio (ElevenLabs' own timestamp API is not used since generation is
  manual).

## Design

### Module map

**Deleted:** `modules/rss_ingest.py`, `modules/ai_extract.py`,
`modules/candidate_bank.py`, `modules/posted_history.py`, and the
podcast-specific config constants (`PODCAST_FEEDS`, `PODCAST_HOSTS`,
`DEFAULT_FEED`, `CANDIDATE_COUNT`, `BANK_REVIEW_COUNT`,
`EPISODE_CLIP_SPACING_DAYS`, `CANDIDATE_BANK_PATH`, `POSTED_HISTORY_PATH`).

**New:**
- `modules/script_gen.py` — one Claude call per run. Picks a topic (informed
  by the dedup ledger, favoring the 3 proven clusters) and writes the full
  content package in one shot: hook, spoken script, insights, key_line,
  title, hashtags, wolf_outfit, 6 image_scenes. No transcript to search, so
  there is no two-stage candidate-then-copy split like `ai_extract.py` had —
  topic selection and copywriting happen together.
- `modules/script_history.py` — the dedup ledger, replacing the job
  `posted_history.py` did for episodes. Much simpler: no GUIDs, no
  used/rejected states, no episode-retirement timing.

**Unchanged, reused as-is:** `modules/transcribe.py`, `modules/image_gen.py`,
`modules/background.py`, `modules/video_gen.py`, `modules/slide_gen.py`. None
of these care whether the audio/script came from a podcast transcript or a
generated script — proven by today's manual test.

### CLI flow

`main.py` becomes two commands, mirroring today's manual test exactly
(Approach B — no front-loading of image/slide generation before audio
exists):

1. **`main.py`** — `script_gen` picks a topic and writes the full package →
   `tmp/<slug>.script.txt` (plain narration text to paste into ElevenLabs) +
   `tmp/<slug>.plan.json` (the cached highlights dict, same caching pattern
   as today's `.plan.json`) → `script_history.record(...)` logs the ledger
   entry immediately (content already exists, so the topic is spent whether
   or not step 2 ever runs — same timing philosophy as
   `posted_history.mark_used()`) → prints instructions to paste the script
   into ElevenLabs and run step 2 with the resulting audio file.
2. **`main.py --render <slug> <audio_path>`** — loads `tmp/<slug>.plan.json`
   → `transcribe.transcribe(audio_path)` for word-level timestamps →
   `background.select_backgrounds(highlights, basename=slug)` for the 6
   images → `video_gen.build_video(audio_path, words, highlights,
   podcast_name=config.BRAND_NAME, background_images=...)` →
   `slide_gen.build_slides(highlights, photo_paths=...)` → prints a
   MANUAL-POST checklist of output paths (unchanged format from today).

`<slug>` is the slugified title (same slugify `video_gen` already uses for
output filenames), doubling as the cache key.

A missing/typo'd `<slug>` at render time fails loudly (`FileNotFoundError` on
the missing `.plan.json`) rather than guessing or falling back — consistent
with the rest of the codebase.

### `script_gen` output contract

```
{
  "topic_cluster": str,       # one of config.TOPIC_CLUSTERS
  "hook": str,
  "script": str,               # full spoken narration, ~40-55s at natural pace
  "insights": [str, str, str], # <=100 chars each, identity statements
  "key_line": str,             # punchy written line for the QUOTE slide —
                                # replaces "best_quote"; nothing is being
                                # quoted anymore, so the field is renamed for
                                # honesty (requires a matching key rename in
                                # slide_gen.py, which currently hard-requires
                                # `best_quote`)
  "title": str,
  "hashtags": [str, ...],      # 3-8
  "wolf_outfit": str,
  "image_scenes": [ {beat, concept, action, setting, camera} x6 ],
  "clip_start": 0.0,
  "clip_end": 9999.0,          # sentinel — video_gen already clamps to the
                                # real audio duration (proven today), so the
                                # actual TTS length always wins
}
```

Safety/brand gates ported from `ai_extract.py` unchanged: hook rules
(contrarian identity frame, banned instructional hooks, <15 words),
content-direction filter, `image_scenes` fixed at 6 following
`config.IMAGE_SCENE_BEATS`, `_scene_safety_gate`'s banned-word regex on
`action`/`setting`.

`generate_script()` is wrapped by `generate_script_with_retry(attempts=3)`,
catching `ValueError` (schema/safety-gate failure) and rate-limit errors,
same shape as today's `extract_copy_with_retry`.

### Dedup ledger

`tmp/script_history.json` — a flat list of
`{"date", "topic_cluster", "hook", "title"}`, newest last.

`script_history.py`:
- `record(topic_cluster, hook, title)` — appends and saves.
- `recent(limit=8)` — returns the newest entries.

`script_gen`'s prompt is fed `recent(8)` and instructed: never pick the same
`topic_cluster` as the immediately preceding entry, never reuse a hook/title
substantially similar to a recent one.

### Config changes

New constants in `config.py`:
- `BRAND_NAME = "Icarus Wings"` — passed as `podcast_name` into
  `video_gen.build_video` for the watermark pill; no `video_gen.py` change
  needed, it already just renders whatever string it receives (and skips the
  watermark cleanly when empty, unused now that there's a fixed brand name).
- `TOPIC_CLUSTERS` — fixed 5-bucket taxonomy so the ledger stays consistent:
  `fear_anxiety_rumination`, `individuality_vs_conformity`,
  `money_as_freedom` (the 3 proven clusters, favored in the prompt),
  `neurology_focus_motivation`, `identity_resilience_meaning` (the rest of
  the content universe). The model still free-picks a specific topic each
  run; this only tags which bucket it falls in.
- `SCRIPT_HISTORY_PATH = tmp/script_history.json`.

Repurposed: `CLIP_WINDOW_MIN_SECONDS` / `CLIP_WINDOW_MAX_HARD_SECONDS` become
soft length guidance fed into the script-writing prompt (~40-55s spoken)
rather than an enforced extraction window with hard validation — there is no
transcript to snap to, so the real, final duration is simply whatever the
TTS audio comes out to; `video_gen` already clamps to it.

### Slide carousel

`slide_gen.build_slides` runs unchanged in step 2, using the same 6 images as
the video (cover = scene 1, insights = scenes 2-4, quote = scene 6, follow =
scene 5) — identical to the current pipeline's mapping. Only the
`best_quote` → `key_line` key rename touches this file.

## Out of scope (this pass)

- ElevenLabs API integration — generation stays a manual copy-paste-download
  step. A `tts_gen.py` module calling ElevenLabs directly (with word-level
  timestamps from their API, dropping the `transcribe.py` step entirely) is a
  natural future upgrade once the manual flow is validated over more than one
  render.
- Performance tracking / retention-by-topic-cluster analysis — the ledger
  only tracks what's been used for dedup, not how it performed. Revisit once
  there's real posting data on this track.
