# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A podcast-to-short-form-video pipeline. For the latest episode of an RSS feed it:
ingest → transcribe → AI clip plan → pick background → render a vertical karaoke-captioned MP4 → upload → publish.

Windows-first (font paths, PowerShell setup). Python 3.12.

## Commands

Always use the venv interpreter explicitly — there is no activated shell assumed:

```powershell
# Full pipeline (see "main.py is stale" gotcha below before relying on this)
.\venv\Scripts\python.exe main.py mindset_mentor      # feed key from config.PODCAST_FEEDS
.\venv\Scripts\python.exe main.py https://...rss       # or a raw RSS URL

# Install deps
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Running / "testing" individual stages

There is **no pytest suite**. Each module under `modules/` has a `__main__` smoke harness that *is* the test — run a stage in isolation from the repo root:

```powershell
.\venv\Scripts\python.exe -m modules.rss_ingest     # fetch + download latest episode -> tmp/
.\venv\Scripts\python.exe -m modules.transcribe      # transcribe newest tmp/*.mp3
.\venv\Scripts\python.exe -m modules.ai_extract      # transcribe + extract clip plan
.\venv\Scripts\python.exe -m modules.slide_gen       # render slides from built-in SAMPLE_HIGHLIGHTS (no API calls)
.\venv\Scripts\python.exe -m modules.pexels_bg       # fetch stock backgrounds from cached plan
.\venv\Scripts\python.exe -m modules.image_gen       # generate Gemini/gradient backgrounds from cached plan
.\venv\Scripts\python.exe -m modules.video_gen       # full render of newest episode + sample frames
```

These harnesses run downstream-to-upstream and assume upstream artifacts already exist in `tmp/` (e.g. `video_gen` needs an MP3 from `rss_ingest`). `slide_gen` is the only one runnable with zero setup.

## Architecture

### Linear pipeline, config-driven

`main.py` orchestrates a fixed sequence; every module is a thin function taking/returning plain dicts and file paths. **`config.py` is the single source of truth** for all paths, dimensions, feed URLs, model IDs, and tuning constants — modules import from it rather than hardcoding. Change behavior there, not in module bodies.

Data contract flowing through the pipeline:
- `transcribe.transcribe()` → `{"text", "segments":[{start,end,text}], "words":[{word,start,end}]}`
- `ai_extract.extract_highlights()` → validated JSON: `hook, insights[3], best_quote, title, clip_start, clip_end, hashtags, image_prompts[4], search_queries[4]`. The schema is enforced by `_validate()`; `clip_start`/`clip_end` must be real segment timestamps and the window must fall within `CLIP_WINDOW_MIN/MAX_SECONDS`.

### Background selection — ordered fallback chain

`background.select_backgrounds()` is a degrade-never chain (see `modules/background.py`):
1. **Pexels stock video** (`pexels_bg`, primary) — needs `PEXELS_API_KEY`; returns `None` if it yields nothing.
2. **Gemini AI image** (`image_gen`) — model fallback chain in `IMAGE_MODELS`.
3. **Local gradient PNGs** (`image_gen` fallback) — always succeeds.

The returned list is **homogeneous**: either all `.mp4` (video) or all `.png` (image). `video_gen._background_layers()` dispatches on the first path's extension, so never mix types. Known constraint: the Gemini image quota on this account hard-429s, so image_gen almost always falls through to gradients.

### video_gen is the core

`modules/video_gen.py` builds the 1080×1920 karaoke clip and is the most intricate file:
- **MoviePy v2 API** — flat imports (`from moviepy import ...`), fluent `.with_*` / `.resized` / `.cropped` methods, and `vfx.CrossFadeIn` / `vfx.Loop` effect objects. Do not use MoviePy v1 patterns (`set_*`, `crossfadein=`).
- Word-level captions: `group_words()` chunks `words` into groups, `_render_block()` draws each group to an RGBA array with the active word highlighted (Pillow, not MoviePy TextClip, for per-word styling).
- Backgrounds get a Ken Burns zoom/pan (`_ken_burns_clip`) + crossfade + 50% dark overlay.
- Fonts are loaded from `C:\Windows\Fonts` (`arialbd.ttf` etc.) — this is Windows-specific.

### Caching to avoid burning API credits

The `video_gen` harness writes `tmp/<basename>.plan.json` = `{transcript, highlights}` and reuses it on re-runs, so Groq/Claude are only hit once per episode. `pexels_bg` and `image_gen` cache `tmp/bg_<n>.{mp4,png}` per index. Pass `force=True` to regenerate. When iterating on rendering, rely on these caches rather than re-running upstream stages.

## External services & secrets

Copy `.env.example` → `.env`. Keys grouped: Anthropic + Groq + Gemini (AI), Pexels (backgrounds), Cloudflare R2 (storage, S3-compatible via boto3), YouTube Data API (OAuth), Meta Graph API (Instagram Reels). Modules call `load_dotenv()` themselves so they work standalone.

Model IDs (in `config.py` / module constants): Claude `claude-sonnet-4-6`, Groq `whisper-large-v3`, Gemini image `gemini-2.5-flash-image`. The original `claude-sonnet-4-20250514` 404s on this account (retired) — keep the replacement.

## Gotchas / current state

- **`main.py` is stale and does not run end-to-end.** It calls `rss_ingest.download_audio(episode)` (no such function — `fetch_latest` already downloads and returns `audio_path`) and reads keys like `episode["show_title"]` that `fetch_latest` never produces. The working, exercised entrypoints today are the per-module `__main__` harnesses, especially `python -m modules.video_gen`. If you wire up the real end-to-end run, reconcile `main.py` against the actual module signatures.
- **`youtube_publish.publish()` and `instagram_publish.publish()` raise `NotImplementedError`** — only scaffolds with TODOs. `storage.upload()` (R2) is implemented.
- **`slide_gen` output is not consumed by `video_gen`.** Slides are a separate static-deck artifact; the published clip is the karaoke video. Don't assume slides feed the video.
