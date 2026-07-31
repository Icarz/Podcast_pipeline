# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A podcast-to-short-form-video pipeline. For a randomly selected episode from a podcast RSS feed it:
ingest → transcribe → AI clip plan → pick background → render a vertical karaoke-captioned MP4 (with a music bed) → render a 6-slide editorial carousel. Publish/upload stages are scaffolded but not wired in yet.

Windows-first (font paths, PowerShell setup, UTF-8 console shim). Python 3.12.

## Commands

Always use the venv interpreter explicitly — there is no activated shell assumed:

```powershell
# Full pipeline, end-to-end (this WORKS now — see main.py below)
.\venv\Scripts\python.exe main.py mindset_mentor      # feed key from config.PODCAST_FEEDS
.\venv\Scripts\python.exe main.py https://...rss      # or a raw RSS URL
.\venv\Scripts\python.exe main.py                      # no arg -> config.DEFAULT_FEED (mindset_mentor)

# Candidate-bank workflow (preferred — pick the BEST clip across episodes/feeds,
# not whatever one random episode happens to contain; see Architecture below)
.\venv\Scripts\python.exe main.py mindset_mentor --scan --limit 3  # batch Stage 1: transcribe + bank candidates, no render
.\venv\Scripts\python.exe main.py --bank                           # review the bank, pick one candidate, render it
.\venv\Scripts\python.exe -m modules.candidate_bank                # print bank contents/stats (no API calls)

# Install deps
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

`main.py` logs to both the console and `logs/pipeline.log`, and reconfigures the
console streams to UTF-8 so non-ASCII episode titles (curly quotes, em dashes)
never crash the run on Windows' cp1252 default.

### Running / "testing" individual stages

There is **no pytest suite**. Each module under `modules/` has a `__main__` smoke harness that *is* the test — run a stage in isolation from the repo root:

```powershell
.\venv\Scripts\python.exe -m modules.rss_ingest     # fetch + download latest episode -> tmp/
.\venv\Scripts\python.exe -m modules.transcribe      # transcribe newest tmp/*.mp3
.\venv\Scripts\python.exe -m modules.ai_extract      # transcribe + extract clip plan
.\venv\Scripts\python.exe -m modules.slide_gen       # render the deck from built-in SAMPLE_HIGHLIGHTS (no AI calls; solid backgrounds)
.\venv\Scripts\python.exe -m modules.image_gen       # generate gpt-image-2/gradient backgrounds from cached plan (image_scenes -> composed prompts; legacy image_prompts) — primary background source
.\venv\Scripts\python.exe -m modules.video_gen       # full render of newest episode + 3 sample frames
```

Most harnesses run downstream-to-upstream and assume upstream artifacts already
exist in `tmp/` (e.g. `video_gen` needs an MP3 from `rss_ingest`, and reuses the
`*.plan.json` cache to avoid re-hitting Groq/Claude). `slide_gen` is the only one
runnable with zero setup (it has a baked-in `SAMPLE_HIGHLIGHTS`).

## Architecture

### Linear pipeline, config-driven

`main.py` orchestrates a fixed 6-step sequence (`run(feed_arg)`); every module is a
thin function taking/returning plain dicts and file paths. **`config.py` is the
single source of truth** for all paths, dimensions, feed URLs, model IDs, and
tuning constants — modules import from it rather than hardcoding. Change behavior
there, not in module bodies.

The `main.py` flow:
1. `rss_ingest.pick_random_entry(feed_url, exclude_guids)` — parse feed, filter already-used GUIDs, pick a random unused episode; `download_latest` fetches its audio. (Manual runs use `fetch_latest` instead.)
2-3. Two-stage extraction, orchestrated by `main.py`'s `_pick_episode_and_candidate`
/ `_find_and_pick_candidate`: transcribe (cached separately as
`tmp/<basename>.transcript.json`) → `ai_extract.find_candidates` surfaces up to
`config.CANDIDATE_COUNT` (5) ranked clip candidates → `ai_extract.filter_candidates`
snaps each to sentence boundaries and drops content-gate failures → a human
picks one (or rejects all, looping to the next episode) → `ai_extract.extract_copy_with_retry`
writes the full copy/art-direction package for the approved window only, cached
as `tmp/<basename>.plan.json`. `posted_history.mark_used()` retires the episode
once a candidate is approved and Stage 2 succeeds (or immediately, for a
rejected/empty-shortlist episode), so it can never be re-selected.
4. `background.select_backgrounds(highlights)` — gpt-image-2 AI image → gradient.
5. `video_gen.build_video(...)` — the karaoke MP4.
6. `slide_gen.build_slides(highlights, photo_paths=backgrounds)` — the 6-PNG carousel on the same wolf images.
The run ends with a MANUAL-POST checklist of local file paths. **Publishing is
fully manual by user decision (2026-07-31)** — the YouTube/Meta/R2 upload
modules (`youtube_publish`, `instagram_publish`, `storage`) were deleted; see
git history if ever needed again.

### Candidate bank (`--scan` / `--bank`) — cross-episode clip backlog

Added 2026-07-31 to fix the "running out of good clips" wall, which was mostly
self-inflicted: the classic flow picks a *random* episode, extracts ONE clip,
and retires the whole episode — burning the other Stage-1 candidates it already
paid to find. The bank decouples scanning from rendering:

- **`main.py <feed> --scan [--limit N]`** (`main.scan_feed`) batch-runs Stage 1
  only: picks up to N unscanned episodes (prescreen/brand filters still apply),
  transcribes, runs `find_candidates` + `filter_candidates`, and stores every
  surviving candidate in `tmp/candidate_bank.json` (`modules/candidate_bank.py`).
  Zero-survivor episodes are banked too (scanned-and-exhausted) so they're never
  re-scanned. No human input, no Stage 2, no render.
- **`main.py --bank`** (`main.run_from_bank`) lists up to `BANK_REVIEW_COUNT`
  (10) available candidates ranked across ALL scanned feeds (rank-in-episode
  first, newest scan first). A number renders that candidate (Stage 2 →
  backgrounds → video → slides via the shared `_render_and_publish` tail);
  `x<N>` permanently rejects one; `0` exits. A Stage-2 failure auto-rejects the
  candidate and returns to the list.
- **Candidates are consumed individually** — one episode can yield several
  Shorts over time. `EPISODE_CLIP_SPACING_DAYS` (14) hides an episode's other
  candidates until that long after its last used clip, so same-episode clips
  never publish back-to-back. An overlap guard also hides any candidate whose
  window overlaps an already-used window of the same episode.
- **Cache collision guard:** bank renders namespace the AI-background cache per
  clip window (`tmp/<basename>_c<clip_start>_bg_<n>.png`) so a second clip from
  the same episode generates its own images. The plan cache
  (`<basename>.plan.json`) is still per-episode and is simply overwritten by
  each bank render (the render happens immediately after, so nothing reads a
  stale one). Output MP4s are named by clip *title*, so they don't collide.
- **Dedup interplay:** episodes enter the bank at scan time and are excluded
  from all future picks by `candidate_bank.scanned_guids()`, which every picker
  path (`--scan` and the classic `_pick_episode_and_candidate`) unions with the
  `posted_history` GUIDs. `mark_used`/`posted_history` semantics for the
  classic and `--url` flows are unchanged; bank episodes do NOT get
  `posted_history` entries (the bank itself is their ledger). The episode's
  enclosure URL is stored so `--bank` can re-download audio if `tmp/` was
  cleaned between scan and render (transcripts re-build the same way).

### Data contracts (what each module produces/consumes now)

- `rss_ingest.pick_random_entry(feed_url, exclude_guids, host_name=None)` → `(feed, entry, metadata)` — random unused episode from the RSS window (used by `--auto`). `rss_ingest.fetch_latest(feed_url)` → `{"title", "audio_path", "description", "link"}` — always-latest, used by manual runs only. `host_name` (looked up from `config.PODCAST_HOSTS` by every call site) is threaded into the prescreen so a guest/interview episode — where the named host isn't even the one speaking — is rejected at the RSS stage, before any download/transcription. **Hard rule: if the named host isn't speaking, don't take the episode.** There's no speaker diarization, so this is enforced at the episode level (title + description via a cheap Haiku call), not by trying to detect host-vs-guest within a transcript.
- `transcribe.transcribe(audio_path)` → `{"text", "segments":[{start,end,text}], "words":[{word,start,end}]}` (Groq `whisper-large-v3`, word + segment granularity).
- `ai_extract` now runs a two-stage extraction. Stage 1 — `find_candidates(transcript)`
  → up to `config.CANDIDATE_COUNT` (5) ranked `{clip_start, clip_end, hook, exposes,
  reframe, payoff}` dicts (no copywriting yet); `filter_candidates(candidates, transcript)`
  → survivors only, snapped to sentence boundaries and content-gated. Stage 2 —
  `extract_copy_for_window(transcript, clip_start, clip_end, seed)` (wrapped by
  `extract_copy_with_retry`, same 3-attempt/65s-sleep retry shape) writes the full
  copy for an ALREADY-FIXED window and returns exactly these keys (schema updated 2026-07-31):
  `hook, insights[3], best_quote, title, clip_start, clip_end, hashtags[3-8], wolf_outfit, image_scenes[6]`.
  - `image_scenes` is a list of **6 objects** `{"beat", "concept", "action", "setting", "camera"}` — structured scene CONTENT for the illustrated backgrounds (the visual STYLE is applied later by `image_gen.compose_prompts`, see Background selection below). Beats follow the fixed story arc `config.IMAGE_SCENE_BEATS` = `[problem, problem, stakes, reframe, payoff, payoff]` (the 4 content quarters map to 6 scenes; hook and payoff get two shots each). `wolf_outfit` is ONE outfit worn unchanged across all 6 scenes so the images read as one character's story. The SAME 6 images also back the entire slide carousel (see slide_gen below). `image_prompts`/`video_queries`/`search_queries` were **removed from the schema 2026-07-31** (old cached plans carrying them still render via fallbacks).
  - `_validate()` enforces types/counts. `clip_start`/`clip_end` must be real segment timestamps and the window must fall within `CLIP_WINDOW_MIN_SECONDS` (45) .. `CLIP_WINDOW_MAX_HARD_SECONDS` (58 — capped so the finished Short stays under 60s, where YouTube blocks the Pixabay music bed).
  - `_trim_to_cap()` runs **before** `_validate` (and again after the sentence snap): if the model insists on a complete thought that runs past the hard cap, it pulls `clip_end` back to the latest sentence-ending word under the cap rather than rejecting the clip (re-asking is wasteful and unreliable — **extraction is NOT deterministic**; identical inputs yield different picks/counts/JSON run to run, so recover in-code instead of retrying for a "better" pick).
  - After validation, `_snap_to_sentences()` snaps the clip to **word-level sentence boundaries** (using `words` punctuation) so the clip never cuts a word in half or opens/ends mid-thought. **`clip_end` snaps BACKWARD only:** it takes the *latest* sentence-ending word at or before `ce + 0.30s` (not the nearest by absolute distance), so the clip ends on the last complete sentence and never snaps forward into the next one (a forward snap produced the `"…freedom. It's—"` mid-sentence cut). `clip_start` still snaps to the nearest sentence-opening word.
  - **Render-time sentence guard (defence in depth):** even if a stale cached plan supplies a `clip_end` that lands mid-sentence, `video_gen.build_video()` re-checks at render time — after `_clip_window()`, before slicing the audio — by looking at the exact caption words in `[start, end]`; if the last word doesn't end a sentence it pulls `end` back to the latest caption word that does, but **only while the window stays ≥ `CLIP_WINDOW_MIN_SECONDS`**. If correcting would drop below the floor it KEEPS the cut and logs a `WARNING: Mid-sentence cut KEPT …` (visible, never silent). It imports `_ends_sentence` from `ai_extract`.
  - **Counts are normalized where recoverable:** `_normalize_image_scenes()` truncates extra scenes and always overwrites `beat` with the fixed `IMAGE_SCENE_BEATS` sequence, but RAISES on a scene shortfall (padding would duplicate images on screen) so the retry wrapper re-extracts. `_scene_safety_gate` scans the visual fields of `image_scenes` (`action`/`setting` — never `concept`, which restates the speech) for banned crowd/female/multi-person words.
- `background.select_backgrounds(highlights)` → a **homogeneous** list of paths (all `.mp4` or all `.png`).
- `video_gen.build_video(audio_path, words, highlights, podcast_name=, background_images=)` → output MP4 path.
- `slide_gen.build_slides(highlights)` → list of 6 ordered PNG paths (COVER, INSIGHT x3, QUOTE, FOLLOW).

### Content direction (the non-negotiable brand purpose)

Every clip and carousel must serve at least one of these four outcomes for the viewer:
- **Self-awareness** — they understand their own behavior, mind, or patterns better
- **New perspective** — they see themselves or life through a lens they didn't have before
- **Hope + agency** — they leave feeling there is a path forward, not trapped
- **Self-knowledge** — they learn something true about how humans (and therefore they) work

The content universe is: human behavior, neurology, focus, motivation, identity, resilience, self-improvement, meaning, and money/wealth **when reframed as freedom or identity** (never personal-finance tips). **Overthinking is a side-topic only** — never the primary theme. A clip that only diagnoses a problem without offering uplift, a new lens, or implied agency fails the brand. Banter, trivia, and entertainment anecdotes with no transferable insight are always rejected.

**Proven topic clusters (from the Jun 27 leaderboard, see performance data below):** fear/anxiety/rumination mechanisms are the single strongest recurring sub-topic (3 of the last 6 top performers) and should be actively favored within TIER 1 whenever present in a transcript; individuality-vs-conformity ("the courage to want more than the crowd finds acceptable") is a second proven cluster; money reframed as freedom is a validated third, newer cluster.

When a podcast episode is centered on a theme already covered recently (e.g. overthinking, procrastination), the pipeline must search harder for a different angle buried deeper in the same episode — identity, meaning, perspective, resilience, or self-knowledge.

### Content performance data (drives prompt decisions)

Production data from the same account (YouTube + TikTok + Instagram) showing 100x+ variance between best and worst content — these numbers directly shaped the `SYSTEM_PROMPT` rules. Updated Jun 16, 2026 (last-28-days window):

| Content | Platform | Result |
|---------|----------|--------|
| "Your Brain Is Addicted to Fake Scenarios" (neurological) | YT | **945 views** (last 28d), 100% like ratio — #1 performer |
| "Opt Out of Modern Culture Before It Breaks You" | YT | **621 views** (last 28d), 100% like ratio |
| "Always Grab the Right Handle" (Epictetus/stoic) | YT | **549 views** (last 28d), 93.8% like ratio |
| "You're Not Obsessed Enough" | YT | 170 views (last 28d) |
| "Your Brain Is Addicted to Fake Scenarios" | TikTok | **529 views**, 38 likes |
| "Always Grab the Right Handle" | TikTok | **502 views** |
| Dramatic winter silhouette thumbnail | IG | 397 views |
| "Why Students Tune Out The Real Reason" | YT | ~7 views, Shorts policy flag |
| "The 3 Word Trick That Makes Personal Change" | YT | 9 views, Shorts policy flag |
| Warm interior woman-at-window thumbnail | IG | 28 views |

**Key patterns:**
- Contrarian identity-frame hooks outperform instructional hooks by **100x+** (945 vs 9 views)
- **Neurological framing now leads stoic framing** — "Your Brain Is Addicted" (945 YT last 28d) has overtaken "Always Grab the Right Handle" (549 YT last 28d); neurological hooks that trap the viewer as the *subject* of a brain system are the current top format
- Both neurological and stoic frames share the same mechanic: viewer is acted upon by a force beyond their awareness — prioritize this over any other hook type
- Cross-platform consistency confirmed: YouTube ranking and TikTok ranking are nearly identical — what wins on one wins on the other
- Dramatic solitary landscape thumbnails outperform warm interior by **14x** on Instagram grid
- Hooks that could be YouTube tutorial titles ("Why X…", "The Y Trick…") get flagged by Shorts policy and die on TikTok too

These findings drove: the HOOK RULES contrarian-identity-frame formula, the TIER 1 (dramatic landscape) scene priority default, DRAMATIC-NATURAL as the default palette, and the cover slide priority rule for `search_queries[0]`.

**Updated Jun 27, 2026 — average view duration added (retention, not just raw views):**

| Rank | Content | Views | Avg view duration | Duration % |
|------|---------|-------|--------------------|-----------|
| 1 | "Your Fear Is a GPS — Here's How to Read It" | **1,389** | 0:18 | 39.2% |
| 2 | "Purpose of Money Is to Get Free! 7 Rules That Actually Work" | 1,190 | 0:31 | **61.1%** |
| 3 | "You're Killing Your Dreams Just to Fit In" | 1,083 | 0:27 | 52.8% |
| 4 | "You're Not Obsessed Enough" | 1,019 | 0:24 | 55.4% |
| 5 | "Your Brain Is Addicted to Fake Scenarios" | 951 | 0:34 | **69.0%** |
| 6 | "Your Brain Won't Let Go Until You Face It — Here's Why" | 931 | 0:34 | **68.0%** |

**Key pattern — views and retention are DIFFERENT axes, and they diverge:**
- The two neurological "Your Brain…" hooks have mediocre view counts (931-951, rank 5-6) but by far the **best retention** (68-69%, ~23s of a 34s clip). These are the most-trusted, best-converting hooks — the clip delivers exactly what the hook promises, immediately and concretely felt (addiction, refusal to let go), so almost nobody who clicks bails early. **This remains the default/priority hook pattern.**
- "Your Fear Is a GPS" is the #1 video by raw views (1,389) but has by far the **worst retention** (39.2%, only ~7s of an 18s clip). A GPS-for-fear metaphor is a strong curiosity-gap hook (great CTR) but requires the viewer to do interpretive work to see the payoff — most clickers bail before the metaphor cashes out. **Lesson: an abstract/clever metaphor hook drives clicks but only pays off in retention if the clip's opening seconds immediately and concretely unpack what the metaphor MEANS in practice — don't let the metaphor sit unexplained.**
- "Purpose of Money Is to Get Free! 7 Rules That Actually Work" **breaks the old "no listicle" hook ban outright** — it's a numbered-rules format — yet lands #2 in views AND #2 in retention (61.1%). The difference from a banned generic listicle ("7 tips for managing money") is that this hook is anchored to a deep IDENTITY-TRANSFORMATION stake (freedom) and frames the rules as a contrarian reveal ("that actually work" implies most rules people follow don't) rather than neutral information. **Lesson: numbered/listicle hooks are not universally banned — they work when the number is in service of an identity-stakes payoff (freedom, power, control), not generic self-help utility.**
- "You're Killing Your Dreams Just to Fit In" and "You're Not Obsessed Enough" are solid, consistent all-arounders (mid-1000s views, ~53-55% retention) — the standard contrarian-identity-frame hook doing exactly what it's supposed to.

These findings refined the HOOK RULES: the neurological "your brain/nervous system does X to you" frame stays the top-priority default (best retention, most reliable), a new "identity-stakes numbered rules" formula was added as a validated alternate structure, and a new rule requires metaphor-based hooks to be unpacked concretely within the clip's opening seconds rather than left abstract.

### One visual track: the illustrated wolf scenes back EVERYTHING

Since 2026-07-31 there is a single art-directed visual track: **`image_scenes`** (exactly 6 objects) + **`wolf_outfit`** — structured scene CONTENT for the clip's illustrated wolf images (see Background selection below for the composition architecture). NOT search queries: these describe what the wolf character is doing (`action` with a LITERAL prop tied to that quarter's words), where (`setting`, varied, out-in-the-world), and how it's framed (`camera`), following the fixed 6-beat story arc. A mandatory STEP-1 content analysis (name each quarter's specific concept before writing any scene) anchors every scene to the actual speech. The 6 generated images serve BOTH consumers: the video's Ken Burns background montage AND the full slide carousel (all 6 slides, including FOLLOW — one branded body of work per post).

(Deleted visual tracks, both 2026-07-31: the `video_queries` stock-video track — palette tiers, TIER 1/2/3 scene lists, spare-beat system — and the `search_queries` stock-photo track — per-slide Pexels queries, palette rule, NEVER-DEPICT human-figure blacklist, MALE-ONLY rule. The whole Pexels/Pixabay layer (`pexels_bg.py`, `pixabay_bg.py`, `bg_quality.py`, the footage-history ledger, and their config/keys) was **deleted from the repo** in the same purge — recover from git history if stock media is ever wanted back.)

**Cross-cutting prompt guards:**
- **Hook formula (contrarian identity frame)** — the `hook` field must use one of six proven structures that challenge the viewer's worldview and imply they are on the wrong side of a divide. Instructional hooks ("X tips for Y", "How to…", "The science of…") are explicitly banned — they average 2-4 views vs. 284-619 for identity-frame hooks. The hook must be under 15 words. Scientific content must be reframed through identity (BAD: "Higher fiber intake leads to more deep sleep" → GOOD: "The meal you ate last night stole 2 hours of deep sleep from you"). See HOOK RULES block in `SYSTEM_PROMPT`.
- **NEVER DEPICT (image scenes) blacklist** — inside the `IMAGE_SCENES` block: no skull/skeleton/death imagery; no cigarettes/alcohol/drugs/vices; no slumped or defeated posture; no violence/gore; no crowds or extra figures of any kind (a busy street/market as an anonymous backdrop is fine — the wolf is the only clearly-rendered figure); no props with readable words. Backed in code by `_scene_safety_gate` (regex on `action`/`setting`) and `image_gen.NEGATIVE_BLOCK` in every composed prompt.
- **(Historical) `search_queries` cover/palette rules** — the old stock-photo track carried a cover-slide priority rule (dramatic solitary landscape, proven 14x on the Instagram grid) and a shared-palette rule. Removed with the track on 2026-07-31 when slides switched to the wolf images — the wolf cover is an unproven-but-deliberate identity bet against that 14x data point; re-check grid performance after a few branded posts.
- **`insights` ≤ 100 chars** — hard cap (reduced from 110) so `slide_gen._fit_body` never shrinks copy to thumbnail-illegible size. Insights must be IDENTITY STATEMENTS, not explanations (BAD: explanatory sentence → GOOD: "Is this happening TO me or FOR me? That question changes everything.")
- **Quote character rule** — the `best_quote` must feel carved in stone: timeless, defiant, memorable. Never select quotes that are merely wise or pleasant — it must make someone want to screenshot it.

### Background selection — ordered fallback chain

`background.select_backgrounds()` is a degrade-never chain (see `modules/background.py`):
1. **OpenAI `gpt-image-2` AI image** (`image_gen.generate_backgrounds`, primary) — generates one on-brief image per composed prompt (`config.IMAGE_PROMPT_COUNT` = 6 since 2026-07-31, one per ~8-9s of the clip) via the OpenAI Images API (`config.OPENAI_IMAGE_MODEL`/`OPENAI_IMAGE_SIZE`/`OPENAI_IMAGE_QUALITY`, currently `gpt-image-2` / `1024x1536` / `medium`, ~$0.28/short); needs `OPENAI_API_KEY`. Retries on 429/5xx with backoff (`RETRY_BACKOFFS`).

   **Style/scene split architecture (2026-07-31 redesign):** the model no longer writes finished image prompts. `ai_extract` emits structured `image_scenes` CONTENT only (beat/concept/action/setting/camera + one per-clip `wolf_outfit`), and `image_gen.compose_prompts()` assembles the final prompt from **locked code constants** — `STYLE_BLOCK` (vintage halftone comic-book, warm vibrant mustard/terracotta/kelly-green palette, bright sunlit mood), `BEAT_MOODS` (per-beat arc mood: problem = confronting/determined, stakes = feeling the weight, reframe = realization, payoff = resolved), `DEFAULT_OUTFIT`, and `NEGATIVE_BLOCK` (no legible text, no vices, no defeated posture, no extra figures). **Edit the look in `image_gen.py`, never in the extraction prompt** — style can no longer drift with model paraphrasing, and a style tweak needs no re-extraction. The 6 images form one **story arc** (`config.IMAGE_SCENE_BEATS`: problem ×2 → stakes → reframe → payoff ×2) with the wolf in the SAME outfit throughout, each scene containing a LITERAL prop tied to that quarter's words (verified working: a sticky-note-covered wall for "you blame the world", a steering wheel for "puts you in the driver's seat"). The wolf-character rules (upright, human posture, always alone) and the bright/warm mood rule (dark 07-29-AM redesign was REJECTED — see [[project_wolf_mascot_image_style]]) are unchanged, now enforced in code. Known accepted gap: gpt-image-2 occasionally bakes in a small readable label despite the no-text rule.
2. **Local gradient PNGs** (`image_gen` fallback) — always succeeds; used when the key is missing or every retry is exhausted.

The returned list is **homogeneous**: always `.png` (image). `video_gen._background_layers()` dispatches on the first path's extension — `_image_background_layers()` applies the Ken Burns pan/zoom (see below) to however many stills it's given (6 now, 4 in old cached plans), whether AI-generated or gradient.

**Pexels/Pixabay are fully gone from the repo** (stock video dropped 2026-07-26 for breaking content rules — a smoking subject, a near-static clip; stock slide photos dropped 2026-07-31 when slides switched to the wolf images; `pexels_bg.py`/`pixabay_bg.py`/`bg_quality.py` and all their config deleted in the same purge). `background.select_backgrounds` composes from `image_scenes`, falling back to raw `image_prompts` strings for pre-2026-07-31 cached plans.

### video_gen is the core (1080×1920 karaoke clip)

`modules/video_gen.py` is the most intricate file:
- **MoviePy v2 API** — flat imports (`from moviepy import ...`), fluent `.with_*` / `.resized` / `.cropped` methods, and effect objects (`vfx.CrossFadeIn`, `afx.AudioLoop`, `afx.MultiplyVolume`, `afx.AudioFadeIn/Out`). Do not use MoviePy v1 patterns (`set_*`, `crossfadein=`).
- Word-level captions: `group_words()` chunks `words` into groups (max `CAPTION_WORDS_PER_GROUP`, also breaking on pauses > `CAPTION_GROUP_GAP`); `_render_block()` draws each group to an RGBA array with the active word highlighted (Pillow, not MoviePy `TextClip`, for per-word styling). The caption band is centered at `CAPTION_CENTER_Y` (0.62) to clear the Reels/Shorts bottom UI.
- Backgrounds: video clips are trimmed (long) or slowed (short, never looped) per slot, cover-cropped, crossfaded (`_video_background_layers`); images get a Ken Burns zoom/pan (`_image_background_layers`). Both get a 50% dark overlay. **Per-slot duration is capped at `config.MAX_BG_CLIP_DURATION` (18s):** `_slot_assignment()` adds extra (shorter) slots when too few distinct clips arrive to tile the window under the cap, and cycles the available clips across them (modulo, so no clip is adjacent to itself) with a different trim offset + Ken Burns motion per repeated occurrence — so a degenerate 3-clips-for-4-slots case shows variety instead of one shot stretching past 18s.
- **Background music bed:** `_music_track()` loops/trims `config.MUSIC_PATH` to the window, drops it to `MUSIC_GAIN_DB` (−18 dB) under the voice, and fades in/out. Absent file → voice-only, no crash.
- Watermark: `_render_watermark()` draws the podcast name as white text on a semi-transparent dark rounded pill, bottom-right, with its bottom edge at `WATERMARK_BASELINE_Y`. Skipped when `podcast_name` is empty (e.g. raw-URL runs).
- Fonts are loaded from `C:\Windows\Fonts` (`arialbd.ttf` etc.) — Windows-specific.

### slide_gen — separate editorial carousel (1080×1350, 4:5)

`modules/slide_gen.py` renders a 6-slide Instagram carousel: **COVER (hook) → INSIGHT 01/02/03 → QUOTE → FOLLOW (CTA)**. This is a **4:5 portrait (1080×1350)** deck — a different aspect ratio from the 9:16 video, and a wholly separate artifact (the published clip is the karaoke video; the slides are not fed into it).
- **Branded deck (2026-07-31):** every slide — including FOLLOW — is backed by the clip's **generated wolf images**, passed from the render as `build_slides(highlights, photo_paths=backgrounds)` and mapped by `_map_photos_to_slides`: cover = scene 1 (problem), insights = scenes 2-4, quote = scene 6 (resolved payoff), FOLLOW = scene 5 (payoff-in-action) — all 6 images used, video + carousel one body of work. `_fill_photo` cover-crops the 1024×1536 PNGs to 1080×1350. A flat black overlay + a vertical scrim that deepens over the text band keep copy legible; without `photo_paths` (the `__main__` harness) or when an image is missing, slides render on the solid `#0D0D12` background. The FOLLOW slide keeps its "STAY IN THE LOOP" eyebrow and accent-colored "FOLLOW FOR MORE" body text either way. (The old per-slide Pexels photo fetch was deleted with `search_queries`.)
- Design system: yellow accent eyebrow with a tick bar, near-white auto-fitting body (`_fit_body` shrinks + re-wraps), giant ghosted serif insight numbers, persistent footer (wordmark + `TOTAL_SLIDES` (6) progress dots). Fonts: bundled DejaVu in `assets/fonts/` (DejaVu Sans Bold / DejaVu Serif Bold) with Windows fallbacks.
- Quote attribution is rendered **only if** the highlights dict actually carries one (`_attribution()` checks keys like `quote_author`/`speaker`); the current `ai_extract` schema produces none, so the quote slide normally shows no attribution — we never invent a speaker.

### Caching to avoid burning API credits

- `tmp/<basename>.plan.json` = `{transcript, highlights}` — written by `main.py` and the `video_gen` harness, reused on re-runs so Groq/Claude are hit only once per episode.
- `image_gen` caches generated images as `tmp/<basename>_bg_<n>.png` (episode-namespaced — **fixed 2026-07-29**, see gotcha below; bank renders add a per-clip `_c<start>` suffix). Pass `force=True` to regenerate. A new episode/clip generates fresh images automatically; re-rendering the same clip reuses its own cache.
- When iterating on rendering, rely on these caches rather than re-running upstream stages.
- **Candidate bank:** `tmp/candidate_bank.json` — every `--scan`-ed episode and its Stage-1 surviving candidates, each individually `available`/`used`/`rejected` (see the Candidate bank section above). Delete a candidate's `status` back to `"available"` to make it pickable again; delete an episode's entry to allow a full re-scan.
- **Episode dedup ledger:** `tmp/posted_history.json` — keyed by RSS GUID. `posted_history.mark_used()` writes here immediately after the plan is built (step 2-3 of `run()`), so the episode is retired from random selection. `pick_random_entry` loads this file (plus `candidate_bank.scanned_guids()`) and excludes all known GUIDs before picking. **RSS window note:** some feeds expose only ~20-50 episodes, but e.g. Mindset Mentor's Simplecast feed serves its full ~1,900-episode archive. When every entry is used, `--auto` logs "all episodes already used" and exits cleanly (not an error).

## External services & secrets

Copy `.env.example` → `.env`. Exactly three keys (everything else was purged 2026-07-31 — publishing is manual, media is AI-generated):
- `ANTHROPIC_API_KEY` (Claude — extraction + gates), `GROQ_API_KEY` (Whisper transcription), `OPENAI_API_KEY` (gpt-image-2 wolf images).

Modules call `load_dotenv()` themselves so they work standalone.

Model IDs: Claude `claude-sonnet-4-6` (`config.EXTRACT_MODEL`), Groq `whisper-large-v3` (`transcribe.MODEL`), OpenAI `gpt-image-2` (`config.OPENAI_IMAGE_MODEL`, `1024x1536` @ `medium` quality — `image_gen.py`). The original `claude-sonnet-4-20250514` 404s on this account (retired) — keep the replacement.

## Gotchas / current state

- **Backgrounds switched from Pexels stock video to `gpt-image-1` AI images on 2026-07-26.** Reason: Pexels footage was uncontrollable relative to clip content and repeatedly broke content rules in ways the quality gate couldn't catch (a smoking subject slipped through; a near-static clip held nearly frozen for 13s of a Short). AI-generated images are on-brief every time and cost ~$0.28/short (6 images @ medium quality since 2026-07-31; was 4 @ ~$0.19 on gpt-image-2) — inside a $10/month budget at current posting cadence. Trade-off accepted knowingly: stills + Ken Burns pan/zoom, not real captured motion. (Since 2026-07-31 the slides reuse these same generated images too, so `PEXELS_API_KEY` is no longer needed by the live pipeline at all.)
- **Switched `OPENAI_IMAGE_MODEL` from `gpt-image-1` to `gpt-image-2` on 2026-07-29.** Reason: pure cost win, same API — `gpt-image-2` is OpenAI's newer image model on an identical Images API contract (`{model, prompt, size, quality}` → `data[0].b64_json`), same `1024x1536` portrait size and `low/medium/high/auto` quality tiers, so the swap was a one-line `config.py` change plus updating stale `gpt-image-1` mentions in `image_gen.py`/`background.py`/`main.py`/`video_gen.py` comments and log strings. Per-1M-token pricing (Standard tier) is ~20-25% cheaper across the board than gpt-image-1 (Input $8 vs $10, Cached $2 vs $2.50, Output $30 vs $40), so the ~$0.25/short cost assumption above should now trend closer to ~$0.19/short — not independently verified against OpenAI's own cost calculator, so re-check actual spend after a few renders. `gpt-image-2` also adds streaming (`stream=True, partial_images=N`) and a documented `moderation_blocked` error path, neither of which this pipeline uses yet.
- **A suspected "whole-clip caption/audio desync" turned out to be a false positive from a flawed verification method — corrected here so it isn't re-litigated.** An investigation using `ffmpeg -vf fps=1/3` to bulk-sample frames appeared to show captions running ~1-1.5s ahead of the audio for an entire clip, which led to `MAX_CHUNK_SECONDS` being lowered from 1200 to 300 (kept — harmless, just more/shorter Groq requests) on the theory that Whisper's word-timestamps drift within a long single transcription request. A later re-verification using precise exact-seek frame extraction (`ffmpeg -ss T -frames:v 1`, not the `fps` filter) showed **perfect sub-100ms caption/audio sync throughout an entire clip**, including deep into it — directly contradicting the original finding. **The `fps` filter's output frame timestamps do not reliably correspond to simple multiples of the sampling interval starting at 0**; using it for precise sync verification was itself the bug. If a caption/audio sync issue is ever reported again, verify with exact-seek single-frame extraction, never `fps`-filter bulk sampling, before concluding there's a real desync.
- **The real caption bug found during that same re-verification: out-of-order Whisper word timestamps can cause two caption blocks to render on top of each other.** Whisper occasionally emits a word with an earlier `start` than the word immediately before it in the transcript (confirmed case: word "that" timestamped before the preceding word "being", both correct in reading order). `video_gen.py`'s caption-clip loop assumes non-decreasing word start times when computing each word's on-screen window; an out-of-order word can start its caption clip before the previous word's clip has finished, producing a garbled overlap for a fraction of a second. Fixed at the source: `transcribe.transcribe()` now runs every returned `words` list through `_enforce_monotonic_words()`, which clamps each word's start/end to at least the previous word's end (in original text order — never reorders words, which would garble the sentence). This is now a guaranteed invariant of `transcribe()`'s return value.
- **`image_gen` background cache was NOT episode-namespaced until 2026-07-29 — every render since silently reused 4 stale images.** `image_gen.generate_backgrounds()` wrote to fixed filenames `tmp/bg_1.png`..`bg_4.png` regardless of episode, and `main.py`/`video_gen.py` called `background.select_backgrounds()` with the default `force=False`. Result: once those 4 files existed, **every subsequent episode's render reused them unconditionally**, completely ignoring that episode's own `image_prompts` — the "on-brief, art-directed per clip" design (and the ~$0.25/short cost assumption, which assumes fresh generation) was silently not happening. Caught when the user reported the AI backgrounds looked wrong ("so sad") right after the 2026-07-29 cinematic-lighting prompt redesign ([[project_wolf_mascot_image_style]]) — the redesign had in fact never been exercised by a real render; the video was still showing images generated the *previous* session, before the redesign. Fixed: `image_gen.generate_backgrounds()` and `background.select_backgrounds()` now take a `basename` param that namespaces the cache as `tmp/<episode-basename>_bg_<n>.png`; `main.py` and `video_gen.py`'s harness both pass the episode's audio basename through. A new episode now always regenerates; re-rendering the same episode still reuses its own cache. **If AI background art ever again looks stale/unrelated to the clip's content, check whether the `basename` plumbing regressed before assuming a prompt problem.**
- **Background music is mixed at −18 dB** (`MUSIC_GAIN_DB`) under the full-volume voice, with 1.0s/1.5s fades. The single track lives at `assets/music/background.mp3`; if it's missing the render silently goes voice-only.
- **Clip selection is completeness-first, but length-capped.** The extraction prompt forces a self-contained thought *with its payoff* (never a cliffhanger), within a 45-58s target and a hard 58s ceiling; the clip is snapped to real sentence boundaries via word timestamps. The 58s cap keeps the finished Short **under 60s** — at/above 60s YouTube blocks the Pixabay music bed on copyright grounds. If the model's best thought runs over, `_trim_to_cap` shortens it to a sentence boundary under the cap (it does not get rejected).
- **One visual track, two consumers** (see Architecture): `image_scenes` + `wolf_outfit` (6 structured scenes, composed into gpt-image-2 prompts) generate 6 images that back BOTH the video's Ken Burns montage AND the entire slide carousel. `search_queries` no longer exists in new plans.
- **Stale plan caches:** `main.py`'s `run()` treats a `*.plan.json` hit as final —
it does **not** check the cached highlights for missing/stale fields (that
migration path only exists in the `video_gen` harness, which regenerates Stage 2
copy for the cached window only when the plan has neither `image_scenes` nor
legacy `image_prompts`). `background.py` composes
prompts from `image_scenes` and falls back to raw `image_prompts` strings for
pre-2026-07-31 cached plans. Delete the `*.plan.json` (and, if you want a fresh
Stage 1 candidate scan too, the matching `*.transcript.json`) to force a clean
re-extract.
- **Windows-specific fonts:** captions/watermark load from `C:\Windows\Fonts`; slides prefer bundled DejaVu in `assets/fonts/`.
- **Config was purged 2026-07-31:** all dead constants (`HOOK_*`, `CLIP_MIN/MAX_SECONDS`, `MAX_CLIPS_PER_EPISODE`, `PEXELS_*`, `BG_QUALITY_*`/`BG_BRIGHTNESS_*`/`BG_FACE_*`/`BG_TEXT_*`, `FOOTAGE_HISTORY_*`, `VIDEO_QUERY_*`, `SEARCH_QUERY_COUNT`) were deleted along with their modules. The live clip-length knobs are the `CLIP_WINDOW_*` constants.
- **Extraction is non-deterministic — both stages go through retry helpers.** Identical
transcript inputs yield varying output: occasional trailing data after the JSON (the
model appends a note/second object), or off-by-one counts (e.g. 5 `image_scenes`
instead of 6) that trip `_validate`. `_strip_to_json` isolates the **first complete
JSON object** via `json.JSONDecoder().raw_decode` (tolerates trailing data), but the
count/validation variance raises `ValueError`. `ai_extract.extract_copy_with_retry(transcript,
clip_start, clip_end, seed, attempts=3)` wraps `extract_copy_for_window`, catching
only `ValueError` (schema/parse — transport/API errors propagate) and `anthropic.RateLimitError`,
logging each failed attempt, and re-raising the last error after the 3rd — critically,
every retry regenerates copy for the SAME fixed window, so it can never drift to a
different segment. Stage 1's `find_candidates` has no retry wrapper (a weak batch
just yields fewer/zero survivors after filtering, which the episode loop already
handles by moving to the next episode). `main.py`'s `run()` calls `extract_copy_with_retry`
for every approved candidate; if it exhausts all 3 attempts, `run()` retires the
episode and re-invokes the picker for a fresh candidate/episode rather than crashing.
The `video_gen` harness's stale-cache regeneration path also calls `extract_copy_with_retry`
(reusing the cached `clip_start`/`clip_end`, with an empty `seed`). Don't call
`extract_copy_for_window` directly from a pipeline path — it dies on the first throw.
- **Background pillarboxing (FIXED — was NOT final-slot-only).** The earlier claim that black side bars only affected the final slot (hidden elsewhere by crossfades) was **empirically false**: a bare edge appeared mid-slot (verified at ~22s, a ~20–49px right bar) on any slot that ran the **Ken Burns** path, with no successor crossfade needed. Root cause: `_ken_burns_motion` used a **time-varying `.resized()` AND time-varying `.with_position()` together**, and MoviePy v2 does **not** composite that combination the way the centering math predicts — the geometry said it over-covered by 60px while pixels showed a 20px bar. Fix: `_ken_burns_motion` now resizes ONCE by a **constant** over-scale `s = max(z0, z1, 1 + 2·max(|pan|)/min(w,h) + 0.06)` (a fixed, known oversized frame → predictable blit) and pans only via a **clamped** `with_position` (`x ∈ [w−fw, 0]`). The zoom *ramp* is dropped in favour of the fixed over-scale + drift — bar-proof at the cost of the slow zoom-in. `_video_background_layers` also re-crops each slot to **exactly** 1080×1920 before Ken Burns (resize round-off could leave a 1px-under frame that the KB position math then under-covered) and logs `BG slot N cover-crop dims:` to prove it. **Verify every render with the edge-brightness scan**, not just a visual tail grab: sample one frame every ~2s across the WHOLE video, measure mean brightness of the leftmost/rightmost 15px strips over the middle 60% of height, and FAIL if any frame has one edge < 8 while the opposite is > 15. Beware false positives where the footage itself is genuinely dark on one side — confirm a suspected bar by checking for a *contiguous near-zero column run with a sharp cliff* (a real added bar) vs. a soft gradient (real content), and by probing the raw source clip's edges.
- `ffmpeg`/`ffprobe` are on PATH (winget Gyan.FFmpeg) for manual dimension/frame checks — separate from the `imageio_ffmpeg` binary MoviePy uses.

## What's still TODO

Scope decisions made 2026-07-31 (user directives — do NOT re-propose): publishing
stays MANUAL on every platform (YouTube/Instagram/TikTok upload code deleted, no
OAuth work), no scheduling/automation, no R2/cloud storage, no wolf avatar/badge
assets unasked.

Genuinely open:
- **First real branded render** via `main.py --bank` (2 candidates banked) — everything since the 2026-07-31 redesigns has been verified by tests/samples but not yet by a full production render.
- **Gate telemetry** — log why Stage-1 candidates die (which gate/criterion) to distinguish "weak episodes" from "over-strict gate".
- **Retention tracking file** — per published short: hook family, topic cluster, feed, then retention % filled in weekly; average-view-duration % is the metric that predicts Shorts distribution.
- **Wolf-cover grid experiment** — the branded carousel cover is an unproven bet against the 14x dramatic-landscape data point; judge after a few posts.
- **Optional 2-second hook-card A/B** on the video's opening frames.
- **No pytest suite** — the per-module `__main__` harnesses are the only "tests".
