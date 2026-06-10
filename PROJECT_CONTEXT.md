# Podcast → Short-Form Video Pipeline — Project Context

> Paste this into a Claude chat to give it full context on the project before asking for help.

## TL;DR

A **Windows-first, Python 3.12** pipeline that turns the latest episode of an RSS podcast
feed into ready-to-post social content:

```
RSS ingest → transcribe → AI clip plan → pick background → render vertical karaoke MP4 (+ music bed) → render 5-slide carousel
```

Two finished artifacts per run:
1. A **9:16 (1080×1920) karaoke-captioned MP4 Short** with stock-video background + music bed — this is *the* published clip.
2. A **4:5 (1080×1350) 5-slide Instagram carousel** (cover → 3 insights → quote) — a separate deck, not fed into the video.

Publish/upload stages exist as scaffolds but are **not wired in yet**.

---

## How to run

Always use the venv interpreter explicitly (no activated shell assumed):

```powershell
# Full pipeline end-to-end (WORKS today)
.\venv\Scripts\python.exe main.py mindset_mentor      # feed key from config.PODCAST_FEEDS
.\venv\Scripts\python.exe main.py https://...rss      # raw RSS URL
.\venv\Scripts\python.exe main.py                      # no arg -> config.DEFAULT_FEED
.\venv\Scripts\python.exe main.py --url https://...mp3 --title "..."  # direct audio bypass
.\venv\Scripts\python.exe main.py --auto               # weekly rotation, posts only on scheduled weekday
```

Logs go to console **and** `logs/pipeline.log`. Console streams are reconfigured to UTF-8 so
non-ASCII episode titles don't crash on Windows cp1252.

### Per-stage smoke harnesses (there is NO pytest suite)

Each `modules/*` file has a `__main__` block that *is* its test. Most run downstream-to-upstream
and reuse `tmp/` artifacts + the `*.plan.json` cache so Groq/Claude aren't re-hit:

```powershell
.\venv\Scripts\python.exe -m modules.rss_ingest    # fetch + download latest -> tmp/
.\venv\Scripts\python.exe -m modules.transcribe    # transcribe newest tmp/*.mp3
.\venv\Scripts\python.exe -m modules.ai_extract    # transcribe + extract clip plan
.\venv\Scripts\python.exe -m modules.slide_gen     # 5 slides from baked-in SAMPLE_HIGHLIGHTS (zero setup)
.\venv\Scripts\python.exe -m modules.pexels_bg     # stock VIDEO backgrounds from cached plan
.\venv\Scripts\python.exe -m modules.image_gen     # Gemini/gradient backgrounds from cached plan
.\venv\Scripts\python.exe -m modules.video_gen     # full render + 3 sample frames
```

`slide_gen` is the only one that runs with zero setup.

---

## Architecture: linear, config-driven

`main.py` orchestrates a fixed 6-step sequence. Every module is a thin function taking/returning
plain dicts and file paths. **`config.py` is the single source of truth** for paths, dimensions,
feed URLs, model IDs, and tuning constants. Change behavior there, not in module bodies.

`main.py` flow:
1. `rss_ingest.fetch_latest(feed_url)` — parse feed, download latest episode audio.
2–3. `_load_or_build_plan(audio_path)` — transcribe + extract, cached as `tmp/<basename>.plan.json`.
4. `background.select_backgrounds(highlights)` — Pexels video → Gemini → gradient.
5. `video_gen.build_video(...)` — the karaoke MP4.
6. `slide_gen.build_slides(highlights)` — the 5-PNG carousel.
Publish is then explicitly skipped (logged as a warning).

### Data contracts (what each module produces / consumes)

- `rss_ingest.fetch_latest(feed_url)` → `{"title", "audio_path", "description", "link"}`.
- `transcribe.transcribe(audio_path)` → `{"text", "segments":[{start,end,text}], "words":[{word,start,end}]}`
  (Groq `whisper-large-v3`, word + segment granularity).
- `ai_extract.extract_highlights(transcript)` → validated JSON with exactly:
  `hook, insights[3], best_quote, title, clip_start, clip_end, hashtags[3-8], image_prompts[4], search_queries[5], video_queries[5]`.
- `background.select_backgrounds(highlights)` → a **homogeneous** list of paths (all `.mp4` OR all `.png`).
- `video_gen.build_video(audio_path, words, highlights, podcast_name=, background_images=)` → output MP4 path.
- `slide_gen.build_slides(highlights)` → list of 5 ordered PNG paths.

---

## The two AI query lists (don't cross them)

`ai_extract` emits **two separate, independently-tuned** query lists:

| List | Count | Type | Consumer | Purpose |
|------|-------|------|----------|---------|
| `search_queries` | exactly 5 | stock **PHOTO** | `slide_gen` (1:1, no cycling) | full-bleed slide backgrounds, slide order `[cover, insight1, insight2, insight3, quote]` |
| `video_queries` | exactly 5 = **4 primary + 1 spare** | stock **VIDEO** | `background.py` → Pexels video | moving background for the clip (4 slots; spare = fallback) |

`video_queries` items are **objects**, not strings: `{"keyword": <one concept word>, "query": <2-4 word portrait video search>}`.
Keyword-first art direction — name the emotional anchor (`focus`, `solitude`, `freedom`), then build a
portrait-video search around it. Tuned for *motion* footage that exists in portrait (walking figures,
water/fog/wind, city movement, slow aerials, hands doing things).

Both follow the same concept→filmable-scene art-director rules in `ai_extract.SYSTEM_PROMPT`
(identify the emotion → translate to a real scene a stock shooter captured → match mood not literal words).
Video-query extra rules: warm/soft/natural light + calm energy; always the **aspirational** version
(never failure-state); avoid legible on-screen text; subjects calm/inward, never performative.

### Clip selection rules

- `clip_start`/`clip_end` must be real segment timestamps; window must fall within
  `CLIP_WINDOW_MIN_SECONDS` (45) .. `CLIP_WINDOW_MAX_HARD_SECONDS` (58).
- **58s hard cap keeps the finished Short under 60s** — at/above 60s YouTube blocks the Pixabay music bed (copyright).
- Pipeline order: `_trim_to_cap()` (pull overrun back to a sentence boundary) → `_validate()` →
  `_snap_to_sentences()` (word-level punctuation boundaries so it never cuts mid-word/mid-thought) →
  post-snap recovery (`_extend_to_floor` / `_trim_to_cap` again).
- Query counts are **normalized, never fatal**: `_normalize_query_list()` drops blanks/extras and pads
  by cycling to coerce both lists to 5; only raises if a list is entirely empty.
- **Extraction is NON-deterministic** — identical inputs yield different picks/counts/JSON. Recover in-code
  rather than retrying for a "better" pick. `_strip_to_json` isolates the first complete JSON object
  (tolerates trailing data). Count/validation variance isn't caught, so loop `extract_highlights` a few
  times until one passes rather than failing on first throw.

---

## Background selection — degrade-never chain

`background.select_backgrounds()` (in `modules/background.py`):
1. **Pexels stock video** (`pexels_bg.fetch_backgrounds`, primary) — uses `video_queries`; needs `PEXELS_API_KEY`; `None` if nothing.
2. **Gemini AI image** (`image_gen.generate_backgrounds`, fed `image_prompts`) — model fallback chain.
3. **Local gradient PNGs** — always succeeds.

Returned list is **homogeneous** (all `.mp4` or all `.png`); `video_gen._background_layers()` dispatches
on the first path's extension, so never mix types. **Reality:** Gemini quota hard-429s on this account,
so steps 2–3 almost always land on gradients. **Pexels video is the de-facto primary; gradient PNG is the safety net.**

### No repeated clips — two-tier dedup

`pexels_bg.fetch_backgrounds()` keeps two id sets:
- `used_ids` — **hard within-run** block: an id taken for one slot is skipped for later queries, so the
  same footage never fills two of four slots.
- `history_ids` — **soft cross-episode** avoid, seeded from a persistent ledger
  `tmp/footage_history.json` (`config.FOOTAGE_HISTORY_PATH`). `_find_video()` prefers a clip in neither
  set; history ids are a fallback (logs `Slot fell back to previously-used footage id …`) — a cross-episode
  repeat beats an empty slot. After the run, picks are appended, capped to `FOOTAGE_HISTORY_MAX` (300, oldest-evicted).
- If a primary query can't yield a fresh distinct clip, the slot falls back through the **spare** (5th) query.
- Final log: `Final background video ids: [...] (n/4 slots filled, distinct=<bool>, spare_used=<bool>)`.

---

## video_gen — the core (1080×1920 karaoke clip)

`modules/video_gen.py` is the most intricate file. **MoviePy v2 API only** — flat imports
(`from moviepy import ...`), fluent `.with_*` / `.resized` / `.cropped`, effect objects
(`vfx.CrossFadeIn`, `afx.AudioLoop`, `afx.MultiplyVolume`, `afx.AudioFadeIn/Out`). **No v1 patterns**
(`set_*`, `crossfadein=`).

- **Word-level captions:** `group_words()` chunks words (max `CAPTION_WORDS_PER_GROUP`, breaks on pauses
  > `CAPTION_GROUP_GAP`); `_render_block()` draws each group to an RGBA array with active word highlighted
  (Pillow, not MoviePy `TextClip`, for per-word styling). Band centered at `CAPTION_CENTER_Y` (0.62) to clear Reels/Shorts UI.
- **Backgrounds:** video clips trimmed (long) or slowed (short, never looped), cover-cropped, crossfaded
  (`_video_background_layers`); images get Ken Burns zoom/pan. Both get a 50% dark overlay. Per-slot
  duration capped at `MAX_BG_CLIP_DURATION` (18s); `_slot_assignment()` adds shorter slots + cycles clips
  (modulo, different trim/motion per repeat) when too few distinct clips arrive.
- **Music bed:** `_music_track()` loops/trims `config.MUSIC_PATH` (`assets/music/background.mp3`), drops to
  `MUSIC_GAIN_DB` (−18 dB) under the voice, fades in/out. Missing file → voice-only, no crash.
- **Watermark:** `_render_watermark()` draws podcast name (always "Icarus Wings" branding policy) as white
  text on a dark rounded pill, bottom-right at `WATERMARK_BASELINE_Y`.
- Fonts load from `C:\Windows\Fonts` (`arialbd.ttf` etc.) — Windows-specific.

## slide_gen — editorial carousel (1080×1350, 4:5)

5 slides: **COVER (hook) → INSIGHT 01/02/03 → QUOTE**. Separate artifact from the video.
- Each slide gets a full-bleed Pexels PHOTO via `pexels_bg.fetch_photo()` (cached `tmp/slide_bg_<n>.jpg`),
  mapped 1:1 from `search_queries`. Black overlay + vertical scrim keep copy legible; failed fetch → solid `#0D0D12`.
- Design: yellow accent eyebrow + tick bar, auto-fitting near-white body (`_fit_body`), ghosted serif insight
  numbers, footer (wordmark + 5 progress dots). Fonts: bundled DejaVu in `assets/fonts/` with Windows fallbacks.
- Quote attribution rendered **only if** present (`_attribution()`); current schema produces none, so the
  quote slide normally shows no speaker. We never invent one.

---

## Caching (to avoid burning API credits)

- `tmp/<basename>.plan.json` = `{transcript, highlights}` — reused on re-runs so Groq/Claude hit once per episode.
- Pexels video cached as `tmp/bg_<query-sha1[:12]>_<video-id>.mp4` (query-keyed, id embedded — **not** by slot index);
  slide photos `tmp/slide_bg_<query-hash>.jpg`; `image_gen` caches `tmp/bg_<n>.png`. Pass `force=True` to regenerate.
- Because filenames are query-keyed, a new episode with different `video_queries` fetches fresh footage
  automatically, while re-rendering the same episode reuses cached downloads.
- `tmp/footage_history.json` is a **separate file** from `bg_*.mp4` clips — survives clip cache cleanup.
  Delete it to forget dedup history.

---

## Secrets / external services

Copy `.env.example` → `.env`. Modules call `load_dotenv()` themselves (work standalone).
- **AI:** `ANTHROPIC_API_KEY` (Claude), `GROQ_API_KEY` (Whisper), `GEMINI_API_KEY` (images).
- **Backgrounds:** `PEXELS_API_KEY` (stock video + slide photos).
- **Storage (R2, S3-compatible via boto3):** `CLOUDFLARE_R2_*`.
- **YouTube Data API (OAuth):** `YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN`.
- **Meta Graph API (IG Reels):** `META_APP_ID/SECRET/ACCESS_TOKEN/IG_USER_ID`.

Model IDs: Claude `claude-sonnet-4-6` (`config.EXTRACT_MODEL`), Groq `whisper-large-v3`,
Gemini image `gemini-2.5-flash-image` → `gemini-3.1-flash-image-preview` fallback.
The original `claude-sonnet-4-20250514` 404s on this account (retired).

---

## Known gotchas / current state

- **Gemini image quota is effectively zero** — `image_gen` hard-429s and falls back to gradient PNGs. Pexels video is the working background source.
- **Final-slot background pillarboxing (open bug):** the last video slot can show black side bars growing
  toward the very end (~last 4s) — its Ken Burns/cover-crop scale drifts below full 1080-wide coverage.
  Slots 1–3 hide the same drift under the crossfade; the final slot has no successor. Fix lives in
  `video_gen`'s video-slot Ken Burns path (clamp min scale to cover-crop floor). **Always verify renders by frame-grabbing the tail.**
- **Re-rendering an approved episode pulls DIFFERENT footage** unless you intervene: a standalone
  `python -m modules.pexels_bg` run commits ids to `footage_history.json` immediately, so a follow-up
  `video_gen` rejects those now-in-history cached clips and fetches fresh. To reproduce the approved clips,
  **remove just those ids from `tmp/footage_history.json` first** (the render re-adds them on completion).
- **Stale plan caches:** `main.py`'s `_load_or_build_plan` only auto-regenerates extraction when
  `search_queries` is missing — it does NOT check for `video_queries`. Old caches keep old highlights;
  `background.py` falls back to `search_queries` for the video search. Delete `*.plan.json` to force a clean re-extract.
- `ffmpeg`/`ffprobe` are on PATH (winget Gyan.FFmpeg) for manual dimension/frame checks — separate from the
  `imageio_ffmpeg` binary MoviePy uses.
- **Dead config constants:** `HOOK_*`, `CLIP_MIN_SECONDS`/`CLIP_MAX_SECONDS`/`MAX_CLIPS_PER_EPISODE` are
  no longer referenced. Live clip-length knobs are the `CLIP_WINDOW_*` constants.

## What's still TODO

- `youtube_publish.publish()` and `instagram_publish.publish()` raise `NotImplementedError` — only scaffolds
  (YouTube resumable OAuth upload; Meta Graph two-step container→publish).
- `storage.upload()` (R2) is implemented but untested, and `main.py` doesn't call it yet.
- **Footage ledger commits at render time, not publish time** — so a non-forced re-render yields different
  footage. Preferred fix once publish is implemented: only commit ids at the publish step, so the ledger
  tracks what audiences actually saw and unpublished re-renders reuse cached footage.
- No pytest suite (per-module `__main__` harnesses are the only "tests").
- No scheduling/automation beyond `--auto` weekly rotation; otherwise single manual end-to-end run per invocation.
