# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A podcast-to-short-form-video pipeline. For the latest episode of an RSS feed it:
ingest → transcribe → AI clip plan → pick background → render a vertical karaoke-captioned MP4 (with a music bed) → render a 5-slide editorial carousel. Publish/upload stages are scaffolded but not wired in yet.

Windows-first (font paths, PowerShell setup, UTF-8 console shim). Python 3.12.

## Commands

Always use the venv interpreter explicitly — there is no activated shell assumed:

```powershell
# Full pipeline, end-to-end (this WORKS now — see main.py below)
.\venv\Scripts\python.exe main.py mindset_mentor      # feed key from config.PODCAST_FEEDS
.\venv\Scripts\python.exe main.py https://...rss      # or a raw RSS URL
.\venv\Scripts\python.exe main.py                      # no arg -> config.DEFAULT_FEED (mindset_mentor)

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
.\venv\Scripts\python.exe -m modules.slide_gen       # render 5 slides from built-in SAMPLE_HIGHLIGHTS (no AI calls; Pexels photos if PEXELS_API_KEY set)
.\venv\Scripts\python.exe -m modules.pexels_bg       # fetch stock VIDEO backgrounds from cached plan (video_queries)
.\venv\Scripts\python.exe -m modules.image_gen       # generate Gemini/gradient backgrounds from cached plan (image_prompts)
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
1. `rss_ingest.fetch_latest(feed_url)` — parse feed, download latest episode audio.
2-3. `_load_or_build_plan(audio_path)` — transcribe + extract, cached together as `tmp/<basename>.plan.json`.
4. `background.select_backgrounds(highlights)` — Pexels video → Gemini → gradient.
5. `video_gen.build_video(...)` — the karaoke MP4.
6. `slide_gen.build_slides(highlights)` — the 5-PNG carousel.
Publish is then explicitly skipped (logged as a warning).

### Data contracts (what each module produces/consumes now)

- `rss_ingest.fetch_latest(feed_url)` → `{"title", "audio_path", "description", "link"}`.
- `transcribe.transcribe(audio_path)` → `{"text", "segments":[{start,end,text}], "words":[{word,start,end}]}` (Groq `whisper-large-v3`, word + segment granularity).
- `ai_extract.extract_highlights(transcript)` → validated JSON dict with exactly these keys:
  `hook, insights[3], best_quote, title, clip_start, clip_end, hashtags[3-8], image_prompts[4], search_queries[5], video_queries[5]`.
  - `video_queries` is a list of **5 objects** `{"keyword": <one concept word>, "query": <2-4 word portrait video search>}`, not bare strings: **4 primary beats + 1 spare backup** (`config.VIDEO_QUERY_EXTRACT_COUNT` = `VIDEO_QUERY_COUNT` + `VIDEO_QUERY_SPARE`). `pexels_bg` fills `VIDEO_QUERY_COUNT` (4) slots and dips into the spare when a primary query yields a duplicate/empty result.
  - `_validate()` enforces types/counts. `clip_start`/`clip_end` must be real segment timestamps and the window must fall within `CLIP_WINDOW_MIN_SECONDS` (45) .. `CLIP_WINDOW_MAX_HARD_SECONDS` (58 — capped so the finished Short stays under 60s, where YouTube blocks the Pixabay music bed).
  - `_trim_to_cap()` runs **before** `_validate` (and again after the sentence snap): if the model insists on a complete thought that runs past the hard cap, it pulls `clip_end` back to the latest sentence-ending word under the cap rather than rejecting the clip (re-asking is wasteful and unreliable — **extraction is NOT deterministic**; identical inputs yield different picks/counts/JSON run to run, so recover in-code instead of retrying for a "better" pick).
  - After validation, `_snap_to_sentences()` snaps the clip to **word-level sentence boundaries** (using `words` punctuation) so the clip never cuts a word in half or opens/ends mid-thought. **`clip_end` snaps BACKWARD only:** it takes the *latest* sentence-ending word at or before `ce + 0.30s` (not the nearest by absolute distance), so the clip ends on the last complete sentence and never snaps forward into the next one (a forward snap produced the `"…freedom. It's—"` mid-sentence cut). `clip_start` still snaps to the nearest sentence-opening word.
  - **Render-time sentence guard (defence in depth):** even if a stale cached plan supplies a `clip_end` that lands mid-sentence, `video_gen.build_video()` re-checks at render time — after `_clip_window()`, before slicing the audio — by looking at the exact caption words in `[start, end]`; if the last word doesn't end a sentence it pulls `end` back to the latest caption word that does, but **only while the window stays ≥ `CLIP_WINDOW_MIN_SECONDS`**. If correcting would drop below the floor it KEEPS the cut and logs a `WARNING: Mid-sentence cut KEPT …` (visible, never silent). It imports `_ends_sentence` from `ai_extract`.
  - **Query counts are normalized, never fatal:** `_normalize_query_list()` drops blanks/extras and pads by cycling, coercing `search_queries` to exactly 5 and `video_queries` to exactly 5 (4 primary + 1 spare), logging a warning when it adjusts. It only raises if a list is entirely empty.
- `background.select_backgrounds(highlights)` → a **homogeneous** list of paths (all `.mp4` or all `.png`).
- `video_gen.build_video(audio_path, words, highlights, podcast_name=, background_images=)` → output MP4 path.
- `slide_gen.build_slides(highlights)` → list of 5 ordered PNG paths.

### Content performance data (drives prompt decisions)

Production data from the same account (YouTube + TikTok + Instagram) showing 400x variance between best and worst content — these numbers directly shaped the `SYSTEM_PROMPT` rules. Updated Jun 14, 2026:

| Content | Platform | Result |
|---------|----------|--------|
| "Always Grab the Right Handle" (Epictetus/stoic) | YT | **948 views**, 93.8% like ratio |
| "Your Brain Is Addicted to Fake Scenarios" (neurological) | YT | **873 views**, 100% like ratio (posted Jun 13) |
| "Your Brain Is Addicted to Fake Scenarios" | TikTok | **529 views**, 38 likes |
| "Always Grab the Right Handle" | TikTok | **502 views** |
| "Opt Out of Modern Culture Before It Breaks You" | YT | 621 views, 100% like ratio |
| Dramatic winter silhouette thumbnail | IG | 397 views |
| "Why Students Tune Out The Real Reason" | YT | ~7 views, Shorts policy flag |
| "The 3 Word Trick That Makes Personal Change" | YT | 2 views, Shorts policy flag |
| Warm interior woman-at-window thumbnail | IG | 28 views |

**Key patterns:**
- Contrarian identity-frame hooks outperform instructional hooks by **100–400x** (873–948 vs 2-7 views)
- **Neurological framing now matches stoic framing** — "Your Brain Is Addicted" (873 YT, 529 TikTok) is neck-and-neck with "Always Grab the Right Handle" (948 YT, 502 TikTok); both trap the viewer as the *subject* of a system acting on them
- Cross-platform consistency confirmed: YouTube ranking and TikTok ranking are nearly identical — what wins on one wins on the other
- Dramatic solitary landscape thumbnails outperform warm interior by **14x** on Instagram grid
- Hooks that could be YouTube tutorial titles ("Why X…", "The Y Trick…") get flagged by Shorts policy and die on TikTok too

These findings drove: the HOOK RULES contrarian-identity-frame formula, the TIER 1 (dramatic landscape) scene priority default, DRAMATIC-NATURAL as the default palette, and the cover slide priority rule for `search_queries[0]`.

### Two distinct sets of AI-art-directed queries

`ai_extract` emits two separate, independently-tuned query lists — keep them straight:

- **`search_queries`** (exactly 5) — stock **PHOTO** queries, one per carousel slide, in slide order `[cover, insight 1, insight 2, insight 3, quote]`. Consumed by `slide_gen` (1:1, no cycling) for the full-bleed slide photo backgrounds.
- **`video_queries`** (exactly 5 `{keyword, query}` objects = **4 primary + 1 spare**) — stock **VIDEO** beats for the clip's moving background. **Palette-first, tiered-scene art direction:** the model first chooses ONE palette (default: **DRAMATIC-NATURAL** — lone figures against vast landscapes, dawn/dusk/storm light — proven highest-performing), then selects scenes from a priority tier (TIER 1: dramatic landscape/silhouette/cliff-edge/mist; TIER 2: journal/window-light/trail-runner; TIER 3: lifestyle-blog interiors — avoid). Each beat names one aspirational `keyword` and builds a 2-4 word portrait video-search `query` that appends the palette's treatment word. The first 4 map 1:1 to the 4 background slots; the 5th is a **spare backup** (same tone, distinct scene). All 5 keywords distinct, all 5 queries distinct, tonally consistent so they crossfade as one film. Consumed by `background.py`, which passes each `.query` to the Pexels video search.

Both follow the same concept→filmable-scene art-director rules in `ai_extract.SYSTEM_PROMPT`. The slide rules favor still compositions; the video rules favor motion. The `video_queries` block uses a **palette-first, tiered scene system** driven by production performance data (see subsection below): (1) **choose palette first** — DRAMATIC-NATURAL (default, highest performing), COOL-CINEMATIC, or WARM-INTERIOR; (2) **tiered scene priority** — TIER 1 (lone figure against vast landscape, silhouette at sunrise/sunset, person at edge of cliff/ocean/rooftop, figure in mist/rain) preferred by default; TIER 2 (hands writing, reading by window, trail runner from behind) when TIER 1 doesn't fit; TIER 3 (coffee shops, desk/laptop, lifestyle-blog interiors) avoided; (3) aspirational version only — never the failure-state; (4) calm and inward, never performative; (5) avoid legible on-screen text; (6) keyword must be aspirational/neutral (never `overwhelm`/`fear`/`anxiety`/`defeat`); (7) safe-scene fallback for abstract concepts; (8) append palette treatment word to EVERY query; (9) `Format:` line. Keep rule numbering contiguous and `Format:` last when editing.

**Cross-cutting prompt guards:**
- **Hook formula (contrarian identity frame)** — the `hook` field must use one of six proven structures that challenge the viewer's worldview and imply they are on the wrong side of a divide. Instructional hooks ("X tips for Y", "How to…", "The science of…") are explicitly banned — they average 2-4 views vs. 284-619 for identity-frame hooks. The hook must be under 15 words. Scientific content must be reframed through identity (BAD: "Higher fiber intake leads to more deep sleep" → GOOD: "The meal you ate last night stole 2 hours of deep sleep from you"). See HOOK RULES block in `SYSTEM_PROMPT`.
- **NEVER DEPICT hard blacklist** — a shared block (just before the `SEARCH_QUERIES` section) that binds **both** `video_queries` AND `search_queries`: no hunched/slumped/head-down/seated-in-defeat postures; no smoking/vaping/alcohol/drugs/junk food; no readable signage/graffiti/legible book text/flipcharts/on-screen words; no crowds/stadiums/conferences/audiences; no identifiable-person face close-ups; no person lying in bed or intimate/sensual positioning; no traffic/cars/urban infrastructure; no flowers/food styling/Pinterest flat-lays.
- **`search_queries` cover slide priority** — `search_queries[0]` is the Instagram grid thumbnail; it MUST be visually dramatic at 1:1 crop (high contrast, lone figure against vast landscape or silhouette at sunrise/sunset). Dramatic solitary landscape outperforms warm interior by 14x on Instagram grid — bias the cover hard toward TIER 1 scenes.
- **`search_queries` palette rule** — all 5 slide photos must share the **same single palette chosen for the video**, so slides and clip feel like one body of work.
- **`insights` ≤ 100 chars** — hard cap (reduced from 110) so `slide_gen._fit_body` never shrinks copy to thumbnail-illegible size. Insights must be IDENTITY STATEMENTS, not explanations (BAD: explanatory sentence → GOOD: "Is this happening TO me or FOR me? That question changes everything.")
- **Quote character rule** — the `best_quote` must feel carved in stone: timeless, defiant, memorable. Never select quotes that are merely wise or pleasant — it must make someone want to screenshot it.

**No repeated clips — two-tier dedup (within-run + cross-episode):** `pexels_bg.fetch_backgrounds()` keeps two id sets. `used_ids` is the **hard, within-run** block: a Pexels id taken for one slot is skipped for later queries (pulling `PEXELS_VIDEO_PER_PAGE` candidates so it can fall to the next result), so the same footage never fills two of the four slots even if two queries match the same video. `history_ids` is the **soft, cross-episode** avoid: seeded from a persistent ledger (`config.FOOTAGE_HISTORY_PATH` = `tmp/footage_history.json`, `{"used_video_ids":[...]}`) of ids used by *previous* episodes, loaded by `_load_footage_history()`. `_find_video()` prefers a clip in neither set; ids in `history_ids` are held only as a **fallback** and returned (with a `Slot fell back to previously-used footage id …` warning) when a query is otherwise exhausted — a cross-episode repeat beats an empty slot. A cache hit is rejected if its id is in *either* set (so two episodes sharing a query/slug don't reuse the same `bg_*.mp4`). After the run, `_save_footage_history(prior + newly_used)` appends this run's picks, capped to the most-recent `config.FOOTAGE_HISTORY_MAX` (300) ids. If a primary query still can't yield a fresh distinct clip, the slot falls back through the **spare** query/queries (`_acquire_for_query` per query; spare = the 5th+ `video_queries` entry) so all 4 slots reliably fill. The final line logs `Final background video ids: [...] (n/4 slots filled, distinct=<bool>, spare_used=<bool>)`.

**Post-fetch QUALITY GATE (`modules/bg_quality.py`) — rejects footage the prompt can't see.** The SYSTEM_PROMPT can *ask* for on-brand footage but can't see what Pexels actually returns (a "sunlit hallway" query yields a black corridor; "teacher" yields a classroom; "open book" yields legible pages). `_find_video()` runs `bg_quality.assess(video)` on every **fresh** candidate before committing — it inspects the candidate's **poster frames only** (`video_pictures` previews, **no full clip download**) and rejects on three checks tuned in `config` (`BG_*`): (1) **brightness** — median frame luma < `BG_BRIGHTNESS_MIN` (42) → too dark/dim/off-palette; numpy-only, **always on**; (2) **faces** — any face bbox > `BG_FACE_AREA_MAX` (0.10) of frame (close-up/identifiable subject) or > `BG_FACE_COUNT_MAX` (1) faces (group/crowd/classroom); (3) **text** — text-like regions > `BG_TEXT_COVER_MAX` of frame (signage/flipchart/book). Checks 2–3 need **opencv** (`opencv-python-headless`); without it the gate **degrades to brightness-only** and logs once (import is wrapped — never a hard dep). A failing candidate is skipped to the next of the `PEXELS_VIDEO_PER_PAGE` results; it's held as a **best-effort last resort** (returned with an `All fresh candidates … failed the quality gate` warning) **only after** the history fallback, so the gate **never empties a slot**. Cache hits bypass the gate (already vetted on first fetch). Set `BG_QUALITY_ENABLED=False` to bypass. **Empirically tuned (see `scripts/probe_quality.py`):** brightness is the reliable, high-value check (kills the near-black corridor at luma 24, raised the slot to a warm ~44, zero false positives across the good-clip set). **Known limits of the cheap heuristics:** Haar misses *small/distant* faces (a classroom of children at desks reads as 0 faces), and the gradient-based text heuristic can't reliably see book/sign text without OCR (it's deliberately conservative to avoid false-positiving on horizons/foliage — a looser version wrongly rejected a sunset-silhouette hill). Real text/posture filtering would need Tesseract/EAST OCR and a pose model; brightness + close-up/crowd faces are what the gate enforces today.

### Background selection — ordered fallback chain

`background.select_backgrounds()` is a degrade-never chain (see `modules/background.py`):
1. **Pexels stock video** (`pexels_bg.fetch_backgrounds`, primary) — uses `video_queries` (falls back to `search_queries` only for older cached plans that predate `video_queries`); needs `PEXELS_API_KEY`; returns `None` if it yields nothing.
2. **Gemini AI image** (`image_gen.generate_backgrounds`, fed `image_prompts`) — model fallback chain in `IMAGE_MODELS`.
3. **Local gradient PNGs** (`image_gen` fallback) — always succeeds.

The returned list is **homogeneous**: either all `.mp4` (video) or all `.png` (image). `video_gen._background_layers()` dispatches on the first path's extension, so never mix types. Known constraint: the Gemini image quota on this account hard-429s, so steps 2-3 almost always land on gradients — **Pexels video is the de-facto primary** and the gradient PNG path is the real safety net.

### video_gen is the core (1080×1920 karaoke clip)

`modules/video_gen.py` is the most intricate file:
- **MoviePy v2 API** — flat imports (`from moviepy import ...`), fluent `.with_*` / `.resized` / `.cropped` methods, and effect objects (`vfx.CrossFadeIn`, `afx.AudioLoop`, `afx.MultiplyVolume`, `afx.AudioFadeIn/Out`). Do not use MoviePy v1 patterns (`set_*`, `crossfadein=`).
- Word-level captions: `group_words()` chunks `words` into groups (max `CAPTION_WORDS_PER_GROUP`, also breaking on pauses > `CAPTION_GROUP_GAP`); `_render_block()` draws each group to an RGBA array with the active word highlighted (Pillow, not MoviePy `TextClip`, for per-word styling). The caption band is centered at `CAPTION_CENTER_Y` (0.62) to clear the Reels/Shorts bottom UI.
- Backgrounds: video clips are trimmed (long) or slowed (short, never looped) per slot, cover-cropped, crossfaded (`_video_background_layers`); images get a Ken Burns zoom/pan (`_image_background_layers`). Both get a 50% dark overlay. **Per-slot duration is capped at `config.MAX_BG_CLIP_DURATION` (18s):** `_slot_assignment()` adds extra (shorter) slots when too few distinct clips arrive to tile the window under the cap, and cycles the available clips across them (modulo, so no clip is adjacent to itself) with a different trim offset + Ken Burns motion per repeated occurrence — so a degenerate 3-clips-for-4-slots case shows variety instead of one shot stretching past 18s.
- **Background music bed:** `_music_track()` loops/trims `config.MUSIC_PATH` to the window, drops it to `MUSIC_GAIN_DB` (−18 dB) under the voice, and fades in/out. Absent file → voice-only, no crash.
- Watermark: `_render_watermark()` draws the podcast name as white text on a semi-transparent dark rounded pill, bottom-right, with its bottom edge at `WATERMARK_BASELINE_Y`. Skipped when `podcast_name` is empty (e.g. raw-URL runs).
- Fonts are loaded from `C:\Windows\Fonts` (`arialbd.ttf` etc.) — Windows-specific.

### slide_gen — separate editorial carousel (1080×1350, 4:5)

`modules/slide_gen.py` renders a 5-slide Instagram carousel: **COVER (hook) → INSIGHT 01/02/03 → QUOTE**. This is a **4:5 portrait (1080×1350)** deck — a different aspect ratio from the 9:16 video, and a wholly separate artifact (the published clip is the karaoke video; the slides are not fed into it).
- Each slide gets a **full-bleed Pexels PHOTO background** fetched per-slide via `pexels_bg.fetch_photo()` (cached as `tmp/slide_bg_<n>.jpg`), mapped 1:1 from `search_queries`. A flat black overlay + a vertical scrim that deepens over the text band keep copy legible; any slide whose photo can't be fetched degrades to the solid `#0D0D12` background.
- Design system: yellow accent eyebrow with a tick bar, near-white auto-fitting body (`_fit_body` shrinks + re-wraps), giant ghosted serif insight numbers, persistent footer (wordmark + 5 progress dots). Fonts: bundled DejaVu in `assets/fonts/` (DejaVu Sans Bold / DejaVu Serif Bold) with Windows fallbacks.
- Quote attribution is rendered **only if** the highlights dict actually carries one (`_attribution()` checks keys like `quote_author`/`speaker`); the current `ai_extract` schema produces none, so the quote slide normally shows no attribution — we never invent a speaker.

### Caching to avoid burning API credits

- `tmp/<basename>.plan.json` = `{transcript, highlights}` — written by `main.py` and the `video_gen` harness, reused on re-runs so Groq/Claude are hit only once per episode.
- `pexels_bg` caches video as **`tmp/bg_<query-sha1[:12]>_<video-id>.mp4`** (keyed by the search query, with the Pexels id embedded — **not** `bg_<n>` by slot index) and slide photos as `tmp/slide_bg_<query-hash>.jpg`; `image_gen` caches `tmp/bg_<n>.png`. Pass `force=True` to regenerate. Because the filename is query-keyed, a new episode whose `video_queries` differ fetches fresh footage automatically, while re-rendering the same episode (identical queries) reuses the cached download.
- Cross-episode footage ledger: `tmp/footage_history.json` records every Pexels id used by past runs (see the two-tier dedup note above), capped at `config.FOOTAGE_HISTORY_MAX` (300, **oldest-evicted**). It's a **separate file from the `bg_*.mp4` clips**, so it persists across runs and survives `tmp/bg_*.mp4` cache cleanup — wiping the cached clips does *not* reset dedup history. Delete `footage_history.json` itself to forget history.
- When iterating on rendering, rely on these caches rather than re-running upstream stages. (To force *new* footage for the same queries, pass `force=True` or delete that episode's `tmp/bg_*.mp4` — there is no shared-by-index reuse, so deleting one episode's files won't disturb another's.)

## External services & secrets

Copy `.env.example` → `.env`. Keys grouped:
- **AI:** `ANTHROPIC_API_KEY` (Claude), `GROQ_API_KEY` (Whisper), `GEMINI_API_KEY` (images).
- **Backgrounds:** `PEXELS_API_KEY` (stock video + slide photos).
- **Storage (R2, S3-compatible via boto3):** `CLOUDFLARE_R2_ENDPOINT`, `CLOUDFLARE_R2_ACCESS_KEY_ID`, `CLOUDFLARE_R2_SECRET_ACCESS_KEY`, `CLOUDFLARE_R2_BUCKET`, `CLOUDFLARE_R2_PUBLIC_URL`.
- **YouTube Data API (OAuth):** `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`. (Uploads target whatever channel the refresh token was authorized for — no channel ID needed.)
- **Meta Graph API (Instagram Reels):** `META_APP_ID`, `META_APP_SECRET`, `META_ACCESS_TOKEN`, `META_IG_USER_ID`.

Modules call `load_dotenv()` themselves so they work standalone.

Model IDs: Claude `claude-sonnet-4-6` (`config.EXTRACT_MODEL`), Groq `whisper-large-v3` (`transcribe.MODEL`), Gemini image `gemini-2.5-flash-image` with a `gemini-3.1-flash-image-preview` fallback (`image_gen.IMAGE_MODELS`). The original `claude-sonnet-4-20250514` 404s on this account (retired) — keep the replacement.

## Gotchas / current state

- **Gemini image quota is effectively zero on this account** — `image_gen` hard-429s through its retry/model chain and falls back to local gradient PNGs. In practice Pexels stock video is the working background source; treat gradients as the safety net, not Gemini images.
- **Background music is mixed at −18 dB** (`MUSIC_GAIN_DB`) under the full-volume voice, with 1.0s/1.5s fades. The single track lives at `assets/music/background.mp3`; if it's missing the render silently goes voice-only.
- **Clip selection is completeness-first, but length-capped.** The extraction prompt forces a self-contained thought *with its payoff* (never a cliffhanger), within a 45-58s target and a hard 58s ceiling; the clip is snapped to real sentence boundaries via word timestamps. The 58s cap keeps the finished Short **under 60s** — at/above 60s YouTube blocks the Pixabay music bed on copyright grounds. If the model's best thought runs over, `_trim_to_cap` shortens it to a sentence boundary under the cap (it does not get rejected).
- **Two query lists, two consumers** (see Architecture): `search_queries` (5, photos) feeds the slides; `video_queries` (5 = 4 primary + 1 spare, motion) feeds the video (4 slots, spare as fallback). Don't cross them.
- **Stale plan caches:** `main.py`'s `_load_or_build_plan` only auto-regenerates extraction when `search_queries` is missing — it does **not** check for `video_queries`. A cache written before `video_queries` existed will keep an older highlights dict; `background.py` handles this by falling back to `search_queries` for the video search. (The `video_gen` harness *does* regenerate on a missing `video_queries`.) Delete the `*.plan.json` to force a clean re-extract.
- **Windows-specific fonts:** captions/watermark load from `C:\Windows\Fonts`; slides prefer bundled DejaVu in `assets/fonts/`.
- **Dead config constants:** `HOOK_*` (the old top-of-frame hook banner was removed from the video), and `CLIP_MIN_SECONDS`/`CLIP_MAX_SECONDS`/`MAX_CLIPS_PER_EPISODE` are no longer referenced anywhere. The live clip-length knobs are the `CLIP_WINDOW_*` constants.
- **Extraction is non-deterministic — re-extracts go through a 3-attempt retry helper.** Identical transcript inputs yield varying output: occasional trailing data after the JSON (the model appends a note/second object), or off-by-one counts (e.g. 5 `image_prompts` instead of 4) that trip `_validate`. `_strip_to_json` isolates the **first complete JSON object** via `json.JSONDecoder().raw_decode` (tolerates trailing data), but the count/validation variance raises `ValueError`. `ai_extract.extract_highlights_with_retry(transcript, attempts=3)` wraps `extract_highlights`, catching only `ValueError` (schema/parse — transport/API errors propagate), logging each failed attempt, and re-raising the last error after the 3rd. **All re-extract sites call the retry helper**, not the bare function: `main.py._load_or_build_plan` (both the cache-miss path and the missing-`search_queries` regeneration path) and `video_gen.__main__` (both the cache-miss and stale-cache regeneration paths). Don't call `extract_highlights` directly from a pipeline path — it dies on the first throw.
- **Background pillarboxing (FIXED — was NOT final-slot-only).** The earlier claim that black side bars only affected the final slot (hidden elsewhere by crossfades) was **empirically false**: a bare edge appeared mid-slot (verified at ~22s, a ~20–49px right bar) on any slot that ran the **Ken Burns** path, with no successor crossfade needed. Root cause: `_ken_burns_motion` used a **time-varying `.resized()` AND time-varying `.with_position()` together**, and MoviePy v2 does **not** composite that combination the way the centering math predicts — the geometry said it over-covered by 60px while pixels showed a 20px bar. Fix: `_ken_burns_motion` now resizes ONCE by a **constant** over-scale `s = max(z0, z1, 1 + 2·max(|pan|)/min(w,h) + 0.06)` (a fixed, known oversized frame → predictable blit) and pans only via a **clamped** `with_position` (`x ∈ [w−fw, 0]`). The zoom *ramp* is dropped in favour of the fixed over-scale + drift — bar-proof at the cost of the slow zoom-in. `_video_background_layers` also re-crops each slot to **exactly** 1080×1920 before Ken Burns (resize round-off could leave a 1px-under frame that the KB position math then under-covered) and logs `BG slot N cover-crop dims:` to prove it. **Verify every render with the edge-brightness scan**, not just a visual tail grab: sample one frame every ~2s across the WHOLE video, measure mean brightness of the leftmost/rightmost 15px strips over the middle 60% of height, and FAIL if any frame has one edge < 8 while the opposite is > 15. Beware false positives where the footage itself is genuinely dark on one side — confirm a suspected bar by checking for a *contiguous near-zero column run with a sharp cliff* (a real added bar) vs. a soft gradient (real content), and by probing the raw source clip's edges.
- **Re-rendering an *approved* episode pulls DIFFERENT footage unless you intervene** (the render-time-ledger gotcha, made concrete): a standalone `python -m modules.pexels_bg` run commits its ids to `footage_history.json` immediately, so a follow-up `video_gen` run rejects those now-in-history cached clips and fetches fresh. To re-render with the exact approved clips, **remove just those ids from `tmp/footage_history.json` first** (the render re-adds them on completion). `ffmpeg`/`ffprobe` are on PATH (winget Gyan.FFmpeg) for these manual dimension/frame checks — separate from the `imageio_ffmpeg` binary MoviePy uses.

## What's still TODO

- **`youtube_publish.publish()` and `instagram_publish.publish()` raise `NotImplementedError`** — only scaffolds with TODOs (YouTube resumable upload via OAuth; Meta Graph two-step container→publish).
- **`storage.upload()` (R2) is implemented but untested**, and `main.py` does not call it yet — the publish/upload steps are deliberately skipped (logged) at the end of `run()`.
- **No scheduling / automation** around `main.py` yet — it's a single manual end-to-end run per invocation.
- **No pytest suite** — the per-module `__main__` harnesses are the only "tests".
- **Footage ledger commits at render time, not publish time.** Currently `fetch_backgrounds()` seeds `used_ids` from history and persists picks as soon as they're rendered, so a non-forced re-render of the same episode yields different footage (its own prior picks are now in history) — you can't reproduce a render you liked. Preferred fix once `youtube_publish`/`instagram_publish` are implemented: only commit video ids to `footage_history.json` at the publish step, so the ledger tracks what audiences actually saw, and unpublished re-renders reuse cached footage.
