# Podcast-to-Short-Form-Video Pipeline — Technical Specification

**Status:** Working end-to-end (render); publish partially wired (R2 + YouTube live, Instagram/TikTok manual).
**Platform:** Windows-first, Python 3.12.
**Brand:** Icarus Wings (`config.BRAND_NAME`).
**Last reviewed:** 2026-06-11.

---

## 1. Purpose

Turn the **latest episode** of a podcast RSS feed into ready-to-post short-form
content, fully automatically:

- a vertical (1080×1920) **karaoke-captioned MP4** clip with a stock-video
  background montage and a music bed, and
- a 5-slide editorial **Instagram carousel** (1080×1920, hook → 3 insights → quote).

The clip is the published asset; the carousel is a companion artifact. A weekly
**rotation** mode picks a different show per weekday and skips already-posted
episodes, making the whole thing schedulable as a single cron-style invocation.

## 2. Scope

### In scope (implemented)
- RSS ingest + audio download (feed key, raw RSS URL, or direct audio URL).
- Transcription (Groq Whisper, word + segment timestamps).
- AI clip-plan extraction (Claude) with schema validation + non-deterministic
  retry recovery.
- Background selection: Pexels stock video → Gemini image → gradient fallback,
  with within-run and cross-episode footage dedup.
- Karaoke video render (MoviePy v2).
- Editorial slide-deck render (Pillow).
- Weekly rotation (`--auto`) with posted-history dedup by RSS GUID.
- Best-effort publish: R2 upload + YouTube Short upload; Instagram/TikTok emitted
  as manual-post reminders.

### Out of scope / TODO
- `instagram_publish.publish()` — `NotImplementedError` scaffold (Meta Graph
  two-step container→publish).
- TikTok — no API path; manual only.
- Native scheduler — relies on an external scheduler invoking `--auto`.
- Automated test suite — only per-module `__main__` smoke harnesses exist.

## 3. Runtime / commands

Always use the venv interpreter explicitly (no activated shell assumed):

```powershell
# Single episode, end-to-end
.\venv\Scripts\python.exe main.py mindset_mentor          # feed key
.\venv\Scripts\python.exe main.py https://...rss          # raw RSS URL
.\venv\Scripts\python.exe main.py                         # config.DEFAULT_FEED

# Direct audio URL (bypass RSS; tagged as the "manual" feed)
.\venv\Scripts\python.exe main.py --url https://...mp3 --title "Episode title"

# Rotation mode — pick today's feed by weekday, skip if already posted
.\venv\Scripts\python.exe main.py --auto

# YouTube visibility (default private)
.\venv\Scripts\python.exe main.py mindset_mentor --privacy unlisted

# Install deps
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Per-stage smoke harnesses (each module's `__main__`):

```powershell
.\venv\Scripts\python.exe -m modules.rss_ingest    # fetch + download -> tmp/
.\venv\Scripts\python.exe -m modules.transcribe    # transcribe newest tmp/*.mp3
.\venv\Scripts\python.exe -m modules.ai_extract    # transcribe + extract plan
.\venv\Scripts\python.exe -m modules.slide_gen     # 5 slides from SAMPLE_HIGHLIGHTS (no AI)
.\venv\Scripts\python.exe -m modules.pexels_bg     # stock VIDEO from cached plan
.\venv\Scripts\python.exe -m modules.image_gen     # Gemini/gradient from cached plan
.\venv\Scripts\python.exe -m modules.video_gen     # full render of newest episode
```

`main.py` logs to console **and** `logs/pipeline.log`, and forces UTF-8 on the
console streams so non-ASCII episode titles never crash on Windows' cp1252.

## 4. Architecture

### 4.1 Principles
- **Linear, config-driven pipeline.** `main.py` orchestrates a fixed sequence;
  every module is a thin function over plain dicts and file paths.
- **`config.py` is the single source of truth** for paths, dimensions, feed URLs,
  model IDs, and tuning constants. Change behavior there, not in module bodies.
- **Publish is best-effort, never fatal.** Once content is rendered, no external
  step (R2/YouTube) may raise and discard the artifacts.
- **Caching to protect API credit.** Transcript + plan are cached per episode;
  footage is cached + deduped across episodes.

### 4.2 Orchestration flow (`main.run`)
1. **Ingest** — `rss_ingest.fetch_latest(feed_url)` parses the feed and downloads
   the latest episode's audio (skipped if `run_auto` already handed in an episode).
2–3. **Plan** — `_load_or_build_plan(audio_path)`: transcribe (Groq) + extract
   (Claude), cached together as `tmp/<basename>.plan.json`. On a cache hit both
   are reused; extraction is re-run (no Groq) only if the cached plan predates a
   required schema field (`search_queries`).
4. **Backgrounds** — `background.select_backgrounds(highlights)`: Pexels video →
   Gemini image → gradient. Returns a **homogeneous** path list (all `.mp4` or all
   `.png`).
5. **Video** — `video_gen.build_video(...)` renders the karaoke MP4.
6. **Slides** — `slide_gen.build_slides(highlights)` renders the 5-PNG carousel.
7. **Publish** — `_publish_stage(...)`: R2 upload (video + slides) → YouTube Short
   → Instagram/TikTok logged as manual reminders. Each external step is wrapped;
   failures are logged, not raised.
8. **History** — on a **successful** YouTube upload, record the episode GUID in
   `posted_history` so `--auto` won't re-post it. A failed upload records nothing
   (next run retries).

### 4.3 Rotation flow (`main.run_auto`)
- Resolve today's weekday against `config.ROTATION` (Mon=0…Sun=6).
- Not a posting day → log + return `None` (process exits 0).
- Parse the feed **once** (`rss_ingest.parse_latest`, no download). Unreachable
  feed → clean no-op, exit 0.
- If latest GUID already in `posted_history` → log + exit 0.
- Otherwise download that same parsed entry and hand it to `run()`.

## 5. Module data contracts

| Module | Entry point | Produces |
|---|---|---|
| `rss_ingest` | `fetch_latest(feed_url)` / `parse_latest` / `download_latest` / `fetch_from_url` | `{title, audio_path, description, link, guid}` |
| `transcribe` | `transcribe(audio_path)` | `{text, segments:[{start,end,text}], words:[{word,start,end}]}` (Groq `whisper-large-v3`) |
| `ai_extract` | `extract_highlights_with_retry(transcript, attempts=3)` | validated highlights dict (see 5.1) |
| `background` | `select_backgrounds(highlights)` | homogeneous list of paths (all `.mp4` or all `.png`) |
| `pexels_bg` | `fetch_backgrounds(...)` / `fetch_photo(...)` | stock video paths / slide photo path |
| `image_gen` | `generate_backgrounds(...)` | Gemini or gradient PNG paths |
| `video_gen` | `build_video(audio_path, words, highlights, podcast_name=, background_images=)` | output MP4 path |
| `slide_gen` | `build_slides(highlights)` | list of 5 ordered PNG paths |
| `storage` | `upload(path)` | public R2 URL (implemented; was untested) |
| `youtube_publish` | `publish(video_path, episode, highlights, privacy_status=)` | YouTube URL |
| `instagram_publish` | `publish(...)` | **NotImplementedError (scaffold)** |
| `posted_history` | `is_posted(guid)` / `record(...)` | persistent GUID ledger |

### 5.1 Highlights schema (`ai_extract`)

Validated dict (`_validate`) with **exactly** these keys:

```
hook, insights[3], best_quote, title,
clip_start, clip_end,
hashtags[3-8],
image_prompts[4],
search_queries[5],
video_queries[5]   # 4 primary + 1 spare, each {keyword, query}
```

- **`clip_start` / `clip_end`** must be real segment timestamps; the window must
  fall within `CLIP_WINDOW_MIN_SECONDS` (25) … `CLIP_WINDOW_MAX_HARD_SECONDS` (58).
  The 58s ceiling keeps the finished Short **under 60s** (YouTube blocks the
  Pixabay music bed at ≥60s).
- **Order of operations:** `_trim_to_cap` (pull `clip_end` back to a sentence end
  under the cap) → `_validate` → `_snap_to_sentences` → `_trim_to_cap` again.
  `clip_end` snaps **backward only** (latest sentence-ending word at/before
  `ce + 0.30s`) so the clip never opens/ends mid-thought; `clip_start` snaps to
  the nearest sentence-opening word.
- **Render-time guard (defence in depth):** `video_gen.build_video` re-checks the
  clip end against caption words; if mid-sentence it pulls `end` back, but only
  while the window stays ≥ `CLIP_WINDOW_MIN_SECONDS`. Otherwise it KEEPS the cut
  and logs `WARNING: Mid-sentence cut KEPT …` (never silent).
- **Non-determinism is expected.** Identical transcripts yield varying picks /
  counts / trailing JSON. `_strip_to_json` isolates the first complete JSON object
  (`raw_decode`, tolerates trailing data); `extract_highlights_with_retry` retries
  3× on `ValueError` (schema/parse only — transport errors propagate). **All
  re-extract sites must call the retry helper**, never the bare function.
- **Query counts are normalized, never fatal** (`_normalize_query_list`): drops
  blanks/extras, pads by cycling to exactly 5 + 5; raises only if a list is empty.

### 5.2 Two distinct query lists (keep separate)
- **`search_queries`** (5) — stock **PHOTO** queries, one per carousel slide in
  order `[cover, insight 1, 2, 3, quote]`. Consumed 1:1 by `slide_gen`.
- **`video_queries`** (5 = 4 primary + 1 spare; `{keyword, query}` objects) —
  stock **VIDEO** beats for the clip background. Keyword-first art direction;
  tuned for motion footage in portrait, warm/soft natural light, aspirational
  subjects, no on-screen text, calm/inward subjects. The first 4 map 1:1 to the 4
  background slots; the 5th is a fallback. Consumed by `background.py`.

## 6. Background selection (degrade-never chain)

`background.select_backgrounds()`:
1. **Pexels stock video** (primary) — `pexels_bg.fetch_backgrounds`, uses
   `video_queries` (falls back to `search_queries` for older cached plans).
   Returns `None` if it yields nothing.
2. **Gemini AI image** — `image_gen.generate_backgrounds(image_prompts)` via
   `IMAGE_MODELS` fallback chain.
3. **Local gradient PNGs** — always succeeds.

The Gemini quota hard-429s on this account, so in practice **Pexels video is the
de-facto primary** and gradients are the real safety net. The returned list is
homogeneous; `video_gen._background_layers()` dispatches on the first path's
extension — never mix types.

### 6.1 Footage dedup (two-tier)
`pexels_bg.fetch_backgrounds()` keeps two id sets:
- **`used_ids`** — hard, within-run: an id taken for one slot is skipped for later
  queries, so no clip fills two of the four slots.
- **`history_ids`** — soft, cross-episode: seeded from `tmp/footage_history.json`
  (`{"used_video_ids":[...]}`). `_find_video` prefers a clip in neither set; a
  history id is returned only as a fallback (logged) when a query is exhausted —
  a cross-episode repeat beats an empty slot.

After the run, picks are appended to the ledger, capped to
`config.FOOTAGE_HISTORY_MAX` (300, oldest-evicted). **Known limitation:** the
ledger commits at **render** time, not publish time, so a non-forced re-render of
the same episode pulls *different* footage. To reproduce an approved render,
remove those ids from `footage_history.json` first (the render re-adds them).

## 7. Video render (`video_gen`, the core)

- **MoviePy v2 API only** — flat imports, fluent `.with_*` / `.resized` /
  `.cropped`, effect objects (`vfx.CrossFadeIn`, `afx.AudioLoop`,
  `afx.MultiplyVolume`, `afx.AudioFadeIn/Out`). No v1 patterns (`set_*`,
  `crossfadein=`).
- **Captions** — word-level karaoke. `group_words()` chunks `words` (max
  `CAPTION_WORDS_PER_GROUP=5`, break on pauses > `CAPTION_GROUP_GAP=0.7s`);
  `_render_block()` draws each group to RGBA with the active word highlighted
  (Pillow, not MoviePy `TextClip`). Band centered at `CAPTION_CENTER_Y=0.62` to
  clear the Reels/Shorts bottom UI.
- **Backgrounds** — video slots trimmed (long) or slowed (short, never looped),
  cover-cropped to exactly 1080×1920, crossfaded; images get Ken Burns. Both get a
  50% dark overlay. Per-slot duration capped at `MAX_BG_CLIP_DURATION=18s`;
  `_slot_assignment()` adds shorter slots and cycles clips (modulo, distinct
  offsets/motion) when too few distinct clips arrive.
- **Ken Burns (bar-proof)** — `_ken_burns_motion` resizes ONCE by a constant
  over-scale and pans via a clamped `with_position`. Do **not** combine
  time-varying `.resized()` with time-varying `.with_position()` (MoviePy v2
  miscomposites → edge bars). Verify every render with the edge-brightness scan
  (sample ~every 2s; fail if one edge <8 while the opposite >15; confirm a real
  bar = contiguous near-zero column run with a sharp cliff).
- **Music bed** — `_music_track()` loops/trims `MUSIC_PATH` to the window, drops
  to `MUSIC_GAIN_DB` (−18 dB) under the voice, fades in/out. Missing file →
  voice-only, no crash.
- **Watermark** — `_render_watermark()` draws `BRAND_NAME` as white text on a
  semi-transparent dark rounded pill, bottom-right, bottom edge at
  `WATERMARK_BASELINE_Y`. Always branded Icarus Wings regardless of source.
- **Fonts** — `C:\Windows\Fonts` (`arialbd.ttf`, …), Windows-specific.

## 8. Slide render (`slide_gen`)

Separate editorial carousel, **1080×1920 9:16** (per current `config`):
COVER (hook) → INSIGHT 01/02/03 → QUOTE.

- Full-bleed Pexels **photo** per slide (`pexels_bg.fetch_photo`, cached
  `tmp/slide_bg_<query-hash>.jpg`), mapped 1:1 from `search_queries`. Black
  overlay + deepening scrim keep copy legible; a failed photo degrades to a solid
  background.
- Design system: amber eyebrow + tick bar, auto-fitting near-white body
  (`_fit_body`), ghosted serif insight numbers, footer wordmark + 5 progress dots.
  Fonts: bundled DejaVu in `assets/fonts/` with Windows fallbacks.
- Quote attribution rendered **only if** the highlights dict carries one; current
  schema produces none — never invent a speaker.

## 9. Caching & persistent state (`tmp/`)

| File | Written by | Purpose |
|---|---|---|
| `tmp/<basename>.plan.json` | `main`, `video_gen` harness | `{transcript, highlights}` — reused so Groq/Claude hit once per episode |
| `tmp/bg_<query-sha1[:12]>_<video-id>.mp4` | `pexels_bg` | cached stock video, **query-keyed** (new episode's queries fetch fresh; same queries reuse) |
| `tmp/slide_bg_<query-hash>.jpg` | `pexels_bg` | cached slide photo |
| `tmp/bg_<n>.png` | `image_gen` | cached generated background |
| `tmp/footage_history.json` | `pexels_bg` | cross-episode Pexels id ledger (cap 300); survives `bg_*.mp4` cleanup |
| `tmp/posted_history.json` | `posted_history` | posted episode GUIDs; written only on YouTube success |

Pass `force=True` (or delete the relevant `tmp/` files) to regenerate. Delete the
`*.plan.json` to force a clean re-extract.

## 10. Configuration constants (key knobs, `config.py`)

- **Feeds / rotation:** `PODCAST_FEEDS`, `DEFAULT_FEED` (`mindset_mentor`),
  `ROTATION` (Mon→modern_wisdom, Tue→jordan_peterson, Wed→huberman_lab,
  Thu→jocko_podcast, Fri→daily_stoic, Sat→mindset_mentor; Sun = no post).
- **Clip window:** `CLIP_WINDOW_MIN_SECONDS=25`, `CLIP_WINDOW_MAX_SECONDS=58`,
  `CLIP_WINDOW_MAX_HARD_SECONDS=58`.
- **Models:** `EXTRACT_MODEL="claude-sonnet-4-6"`, Groq `whisper-large-v3`,
  Gemini `gemini-2.5-flash-image` (+ preview fallback). The retired
  `claude-sonnet-4-20250514` 404s — keep the replacement.
- **Query counts:** `SEARCH_QUERY_COUNT=5`, `VIDEO_QUERY_COUNT=4`,
  `VIDEO_QUERY_SPARE=1`, `VIDEO_QUERY_EXTRACT_COUNT=5`, `IMAGE_PROMPT_COUNT=4`.
- **Video:** `SLIDE_WIDTH=1080`, `SLIDE_HEIGHT=1920`, `VIDEO_FPS=30`,
  `MAX_BG_CLIP_DURATION=18`, caption + Ken Burns + watermark constants.
- **Audio:** `MUSIC_GAIN_DB=-18`, `MUSIC_FADE_IN=1.0`, `MUSIC_FADE_OUT=1.5`,
  `MUSIC_PATH=assets/music/background.mp3`.
- **Pexels:** `PEXELS_VIDEO_PER_PAGE=12`, `PEXELS_ORIENTATIONS=[portrait,
  square, landscape]`, `PEXELS_BACKOFFS=[2,4,8,16]` (free tier 200 req/hr).
- **Brand:** `BRAND_NAME="Icarus Wings"`.
- **Dead constants** (no live references): `HOOK_*`, `CLIP_MIN_SECONDS`,
  `CLIP_MAX_SECONDS`, `MAX_CLIPS_PER_EPISODE`.

## 11. External services & secrets

Copy `.env.example` → `.env`. Modules call `load_dotenv()` themselves (standalone).

- **AI:** `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`.
- **Backgrounds:** `PEXELS_API_KEY` (video + slide photos).
- **Storage (R2, S3-compatible via boto3):** `CLOUDFLARE_R2_ENDPOINT`,
  `CLOUDFLARE_R2_ACCESS_KEY_ID`, `CLOUDFLARE_R2_SECRET_ACCESS_KEY`,
  `CLOUDFLARE_R2_BUCKET`, `CLOUDFLARE_R2_PUBLIC_URL`.
- **YouTube Data API (OAuth):** `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`,
  `YOUTUBE_REFRESH_TOKEN` (token helper: `get_youtube_token.py`).
- **Meta Graph (Instagram):** `META_APP_ID`, `META_APP_SECRET`,
  `META_ACCESS_TOKEN`, `META_IG_USER_ID` (token helper: `get_instagram_token.py`).

## 12. Non-functional requirements / constraints

- **Resilience:** publish never crashes a successful render; feed problems in
  `--auto` are clean no-ops (exit 0); genuine pipeline failures log a full
  traceback and exit 1.
- **Cost control:** transcript/plan + footage caching; GUID dedup prevents
  re-posting; footage ledger prevents cross-episode repeats.
- **Compliance:** Shorts kept under 60s for the music bed; brand watermark always
  applied.
- **Determinism caveat:** extraction is non-deterministic — recover in-code
  (trim/snap/retry), never re-ask for a "better" pick.
- **Windows-specific:** font paths, UTF-8 console shim, `C:\Windows\Fonts`.
  `ffmpeg`/`ffprobe` on PATH (winget Gyan.FFmpeg) for manual dimension/frame
  checks, separate from MoviePy's `imageio_ffmpeg` binary.

## 13. Open items / roadmap

1. Implement `instagram_publish.publish()` (Meta Graph container→publish).
2. Move the footage ledger commit from **render** time to **publish** time so
   unpublished re-renders reuse footage and audiences' footage is what's tracked.
3. Add a real test suite (replace `__main__` smoke harnesses).
4. Native scheduling around `--auto` (currently external scheduler only).
5. Validate R2 `storage.upload()` against live credentials end-to-end.
```
