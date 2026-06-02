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
  `hook, insights[3], best_quote, title, clip_start, clip_end, hashtags[3-8], image_prompts[4], search_queries[5], video_queries[4]`.
  - `video_queries` is a list of **4 objects** `{"keyword": <one concept word>, "query": <2-4 word portrait video search>}`, not bare strings.
  - `_validate()` enforces types/counts. `clip_start`/`clip_end` must be real segment timestamps and the window must fall within `CLIP_WINDOW_MIN_SECONDS` (45) .. `CLIP_WINDOW_MAX_HARD_SECONDS` (75).
  - After validation, `_snap_to_sentences()` snaps the clip to **word-level sentence boundaries** (using `words` punctuation) so the clip never cuts a word in half or opens/ends mid-thought.
  - **Query counts are normalized, never fatal:** `_normalize_query_list()` drops blanks/extras and pads by cycling, coercing `search_queries` to exactly 5 and `video_queries` to exactly 4, logging a warning when it adjusts. It only raises if a list is entirely empty.
- `background.select_backgrounds(highlights)` → a **homogeneous** list of paths (all `.mp4` or all `.png`).
- `video_gen.build_video(audio_path, words, highlights, podcast_name=, background_images=)` → output MP4 path.
- `slide_gen.build_slides(highlights)` → list of 5 ordered PNG paths.

### Two distinct sets of AI-art-directed queries

`ai_extract` emits two separate, independently-tuned query lists — keep them straight:

- **`search_queries`** (exactly 5) — stock **PHOTO** queries, one per carousel slide, in slide order `[cover, insight 1, insight 2, insight 3, quote]`. Consumed by `slide_gen` (1:1, no cycling) for the full-bleed slide photo backgrounds.
- **`video_queries`** (exactly 4 `{keyword, query}` objects) — stock **VIDEO** beats for the clip's moving background. **Keyword-first art direction:** the model first names one core concept keyword per beat (e.g. `focus`, `solitude`, `freedom`) as the emotional anchor, then builds a 2-4 word portrait video-search `query` around it. Tuned for *motion* footage that exists in portrait (walking figures, water/fog/wind, city movement, slow aerials, hands doing things), mapped 1:1 to the 4 background slots — no cycling. All 4 keywords distinct and all 4 queries distinct, kept tonally consistent so the four crossfade as one piece. Consumed by `background.py`, which passes each `.query` to the Pexels video search.

Both follow the same concept→filmable-scene art-director rules in `ai_extract.SYSTEM_PROMPT` (identify the emotion, translate to a real scene a stock shooter actually captured, match the mood not the literal words). The slide rules favor still compositions; the video rules favor motion.

**No repeated clips:** `pexels_bg.fetch_backgrounds()` tracks the Pexels video id taken for each slot and skips it for later queries (pulling `PEXELS_VIDEO_PER_PAGE` candidates so it can fall to the next result), so the same footage never fills two of the four slots even if two queries happen to match the same video.

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
- Backgrounds: video clips are looped/trimmed per slot, cover-cropped, crossfaded (`_video_background_layers`); images get a Ken Burns zoom/pan (`_image_background_layers`). Both get a 50% dark overlay.
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
- `pexels_bg` caches `tmp/bg_<n>.mp4` (video) and `tmp/slide_bg_<n>.jpg` (slide photos); `image_gen` caches `tmp/bg_<n>.png`. Pass `force=True` to regenerate.
- When iterating on rendering, rely on these caches rather than re-running upstream stages. (To pick up *new* `video_queries`/backgrounds you must delete the cached `tmp/bg_*.mp4`, since paths are reused by index.)

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
- **Clip selection is completeness-first.** The extraction prompt forces a self-contained thought *with its payoff* (never a cliffhanger), within a 45-65s target but a hard 75s ceiling; the clip is then snapped to real sentence boundaries via word timestamps.
- **Two query lists, two consumers** (see Architecture): `search_queries` (5, photos) feeds the slides; `video_queries` (4, motion) feeds the video. Don't cross them.
- **Stale plan caches:** `main.py`'s `_load_or_build_plan` only auto-regenerates extraction when `search_queries` is missing — it does **not** check for `video_queries`. A cache written before `video_queries` existed will keep an older highlights dict; `background.py` handles this by falling back to `search_queries` for the video search. (The `video_gen` harness *does* regenerate on a missing `video_queries`.) Delete the `*.plan.json` to force a clean re-extract.
- **Windows-specific fonts:** captions/watermark load from `C:\Windows\Fonts`; slides prefer bundled DejaVu in `assets/fonts/`.
- **Dead config constants:** `HOOK_*` (the old top-of-frame hook banner was removed from the video), and `CLIP_MIN_SECONDS`/`CLIP_MAX_SECONDS`/`MAX_CLIPS_PER_EPISODE` are no longer referenced anywhere. The live clip-length knobs are the `CLIP_WINDOW_*` constants.

## What's still TODO

- **`youtube_publish.publish()` and `instagram_publish.publish()` raise `NotImplementedError`** — only scaffolds with TODOs (YouTube resumable upload via OAuth; Meta Graph two-step container→publish).
- **`storage.upload()` (R2) is implemented but untested**, and `main.py` does not call it yet — the publish/upload steps are deliberately skipped (logged) at the end of `run()`.
- **No scheduling / automation** around `main.py` yet — it's a single manual end-to-end run per invocation.
- **No pytest suite** — the per-module `__main__` harnesses are the only "tests".
