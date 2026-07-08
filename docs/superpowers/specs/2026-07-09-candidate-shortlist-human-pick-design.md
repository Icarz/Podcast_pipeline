# Candidate shortlist + human pick — design

**Date:** 2026-07-09
**Status:** Approved
**Area:** `modules/ai_extract.py` (extraction is split into two stages), `main.py`
(episode-selection loop becomes interactive), `modules/posted_history.py` (no
schema change — reuses existing "used but not published" state)

## Problem

The current pipeline reads the full episode transcript in a single Sonnet call
and commits to ONE candidate clip window plus its full copywriting package
(hook, insights, quote, title, hashtags, image/video queries) all at once. If
the automated content gate rejects it, `extract_highlights_with_retry` blind-
retries the *entire* call from scratch up to 3 times, with no memory of why the
previous attempt failed — and if all 3 fail, the whole episode is discarded and
another is picked.

This has two failure modes in practice:

1. **Slow, wasteful gambling on rejection.** Multiple observed runs burned 3
   full re-extractions × 65s rate-limit sleeps per episode, across up to 5
   episodes, before landing anything — or exhausting all 5 without success.
2. **False positives that still miss the mark.** Even when a clip survives
   every automated gate (brand gate, content gate), the user reports having to
   render 3 times before finding something that actually delivers the
   pipeline's core promise: exposing a behavior/problem/way of thinking,
   reframing or explaining it, and landing a payoff. The Haiku content gate is
   an approximation of "is this good" — it is not a reliable proxy for the
   user's actual judgment, and there is currently no way to compare candidates
   or exercise that judgment before a full render is spent.

The user's own framing: *"I am tired of gambling fetching the right podcast
speech... whether I find something already made or find a way to fetch the
right speech I am looking for."* Background footage quality is explicitly
out of scope — it's "just background" and already acceptable.

## Goal

Make clip *selection* (which transcript segment becomes the short) a decision
the user makes directly, backed by a shortlist of AI-surfaced candidates
instead of a single blind pick — while keeping the AI's job (copywriting,
brand/scene compliance) unchanged once a segment is chosen. Reject-all must
loop to the next episode automatically rather than stopping the run.

## Constraints

- No pytest suite — per-module `__main__` harnesses are the only tests.
- Extraction is non-deterministic; recover via structure (multiple candidates,
  narrow retries), never assume a re-ask converges on something better.
- `--auto` (rotation mode) has no human present and must stay fully automatic;
  it is currently disabled (user manually triggers renders) but the code path
  must keep working non-interactively.
- `posted_history.mark_used()` already supports a "used but never published"
  state (a YouTube publish failure leaves an episode in exactly this state
  today) — no schema change needed to represent a user-rejected episode the
  same way.
- Background-footage selection, video rendering, and publish stages are
  unaffected — this design only touches the extraction/selection layer.

## Design

### 1. Split `SYSTEM_PROMPT` into two stage-specific prompts

- **`CANDIDATE_SYSTEM_PROMPT`** keeps the BRAND MISSION, CLIP SELECTION RULES
  (topic priority/tiers, digestibility-first, single-speaker hard rule), and
  the clip_start/clip_end grounding-in-real-timestamps rules from the current
  prompt. Drops hashtags/image_prompts/search_queries/video_queries entirely.
  Instructs the model to surface **up to `CANDIDATE_COUNT` (5)** distinct
  candidates, ranked, instead of committing to one.
- **`COPY_SYSTEM_PROMPT`** keeps the HOOK RULES, insights/quote rules, and the
  full image_prompts/search_queries/video_queries art-direction + NEVER DEPICT
  blacklist. Takes a transcript excerpt for an **already-fixed** window instead
  of picking its own.

### 2. New/changed functions in `modules/ai_extract.py`

```python
def find_candidates(transcript: dict) -> list[dict]:
    """One Sonnet call (CANDIDATE_SYSTEM_PROMPT). Returns up to
    config.CANDIDATE_COUNT candidates:
      [{"clip_start": float, "clip_end": float, "hook": str,
        "exposes": str, "reframe": str, "payoff": str}, ...]
    Each already respects CLIP_WINDOW_MIN/MAX_HARD_SECONDS and the brand/
    single-speaker rules via the same prompt constraints as today, applied
    per-candidate. No creative copywriting yet."""

def filter_candidates(candidates: list[dict], transcript: dict) -> list[dict]:
    """For each candidate: run _snap_to_sentences + _extend_to_floor +
    _trim_to_cap (reused as-is), then _content_gate (reused as-is, catching
    ValueError to drop rather than raise). Returns survivors only, preserving
    the model's original rank order."""

def extract_copy_for_window(transcript: dict, clip_start: float,
                             clip_end: float, seed: dict) -> dict:
    """Second Sonnet call (COPY_SYSTEM_PROMPT). clip_start/clip_end are FIXED
    inputs, not chosen here. `seed` is the approved candidate dict, passed as
    context so copy stays anchored to what was approved. Returns the same
    schema as today's extract_highlights() (hook, insights, best_quote,
    title, hashtags, image_prompts, search_queries, video_queries) plus the
    fixed clip_start/clip_end passed through unchanged.
    Runs _validate (schema/count checks only — no window-bounds check, that's
    already satisfied), _brand_gate, _scene_safety_gate, _content_gate."""

def extract_copy_with_retry(transcript, clip_start, clip_end, seed,
                             attempts: int = 3) -> dict:
    """Same 3-attempt/65s-sleep retry shape as today's
    extract_highlights_with_retry, but retrying regenerates copy for the
    SAME fixed window — it cannot drift to a different segment."""
```

`extract_highlights` and `extract_highlights_with_retry` are removed —
replaced entirely by the two-stage functions above.

### 3. Interactive episode/candidate loop in `main.py`

```python
def _pick_episode_and_candidate(feed_arg: str) -> tuple[dict, dict, dict]:
    """Loops episodes until the user approves a candidate, or the RSS window
    is exhausted. Returns (episode, transcript, chosen_candidate)."""
    used_guids = set(posted_history.load().keys())
    while True:
        picked = rss_ingest.pick_random_entry(feed_url, exclude_guids=used_guids, ...)
        if picked is None:
            raise RuntimeError("All episodes in the RSS window have been used.")
        episode = rss_ingest.download_latest(...)
        transcript = _load_or_build_transcript(episode["audio_path"])  # cached, unchanged

        candidates = ai_extract.find_candidates(transcript)
        survivors = ai_extract.filter_candidates(candidates, transcript)

        if not survivors:
            posted_history.mark_used(guid, feed_arg, episode["title"])
            used_guids.add(guid)
            print(f"No viable candidates in {episode['title']!r} — trying next episode.")
            continue

        _print_candidates(survivors)   # numbered: hook + exposes/reframe/payoff
        choice = input("Pick a number, or 0 to reject all: ").strip()
        if choice == "0":
            posted_history.mark_used(guid, feed_arg, episode["title"])
            used_guids.add(guid)
            continue

        return episode, transcript, survivors[int(choice) - 1]
```

`run()` calls this instead of `pick_random_entry` + the extraction half of
`_load_or_build_plan`, then calls `ai_extract.extract_copy_with_retry(...)`
for Stage 2. `_load_or_build_plan` is split accordingly:

- `_load_or_build_transcript(audio_path)` — transcribe-only, cached exactly
  like today's transcribe half (Groq hit once per episode, reused across
  candidate-reject loops within the same run).
- The **final** `*.plan.json` (`{transcript, highlights}`) is written only
  once, **after Stage 2 succeeds** for the approved candidate — same format
  and same cache-hit short-circuit as today, so re-running the same episode
  (e.g. via the `video_gen` harness, or a second manual run before the
  episode is published) skips straight past both Stage 1 and Stage 2 and
  reuses the approved highlights, with no re-prompt.
- What is NOT cached: Stage 1's raw candidate list, and anything for a
  rejected episode. If `*.plan.json` is deleted and the same episode is
  re-run from scratch, it gets a fresh candidate scan (which may differ, per
  the existing documented non-determinism of extraction) rather than
  replaying the old shortlist.

A rejected episode is retired via the existing `posted_history.mark_used()` —
no new state, no schema change. Per the user: *"if it's working for me, it's
not working for me, now or later"* — permanent skip, same as a published
episode.

### 4. Gate/retry behavior

- **Stage 1** (`find_candidates`): no retry wrapper. A weak batch simply
  yields fewer/zero survivors after filtering, which the episode loop already
  handles by moving to the next episode. No rate-limit sleep needed since
  there's no retry here.
- **Filtering**: `_content_gate` reused unchanged (still fails open on API
  errors). Called up to `CANDIDATE_COUNT` (5) times per episode — roughly the
  same total call volume as today's blind retries, just spent up front
  instead of across failed full-extraction attempts.
- **Stage 2** (`extract_copy_with_retry`): retries regenerate copy for the
  *same fixed window* — cannot drift to a different segment. `_content_gate`
  reruns here too as a final check (copy could theoretically stop matching the
  transcript), but should rarely fail since the segment was already vetted in
  Stage 1 filtering.
- If Stage 2 exhausts all 3 retries (expected to be rare), that's a genuine
  failure: fall back to `_pick_episode_and_candidate` again (loop to another
  candidate/episode) rather than silently degrading output quality.

### 5. `--auto` and `--url` handling

- **`--auto`** (rotation mode; currently disabled per user's Jul 4 change, but
  the code path must keep working): stays fully non-interactive.
  `find_candidates` → `filter_candidates` → take the top surviving candidate
  automatically, no `input()` call. No human is present for scheduled runs.
- **`--url`** (direct-audio runs): gets the same interactive picker as feed
  runs, but there's only one "episode" — if all candidates are rejected, the
  run ends with a clear message rather than looping (nothing to fall back to).
- **Empty RSS window**: same existing error as today.

### 6. Testing

`modules/ai_extract.py`'s `__main__` harness is updated to run
`find_candidates` → print the shortlist → `extract_copy_for_window` on the
first survivor, so the split can be sanity-checked standalone without
touching `main.py`'s interactive loop.

## Non-goals

- No change to background-footage selection, video rendering, slide
  generation, or publish stages.
- No change to the RSS prescreen (host-only speaker rule) or the brand/scene
  safety gates' actual rule content — only *when* they run and *what* they
  gate (a candidate vs. a final copy draft).
- No persistence of rejected candidates beyond the episode-level
  `posted_history` skip (a rejected episode is not eligible for re-scan later,
  per the user's explicit "not now or later" instruction).
