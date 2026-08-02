# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A synthetic-script short-form-video pipeline. Claude picks a topic and writes a full script (hook → reframe → payoff) from scratch — no podcast, no transcript. You paste that script into ElevenLabs by hand, download the voiceover, and hand it back to the pipeline, which transcribes it (for word-level caption timestamps only), generates 6 illustrated wolf-mascot background images, and renders a vertical karaoke-captioned MP4 (with a music bed) plus a 6-slide editorial carousel. Publish/upload stages are not wired in — publishing is manual by design.

Windows-first (font paths, PowerShell setup, UTF-8 console shim). Python 3.12.

**Migrated from a podcast-RSS-transcript pipeline to this synthetic-script pipeline on 2026-07-31** (user decision, after a manual test render validated the approach end-to-end). `rss_ingest.py`, `ai_extract.py`, `candidate_bank.py`, and `posted_history.py` were deleted; `modules/script_gen.py` and `modules/script_history.py` replace their job. See git history if the podcast-sourcing flow is ever wanted back — design spec at `docs/superpowers/specs/2026-07-31-synthetic-script-pipeline-design.md`, implementation plan at `docs/superpowers/plans/2026-07-31-synthetic-script-pipeline.md`.

## Commands

Always use the venv interpreter explicitly — there is no activated shell assumed:

```powershell
# Step 1: pick a topic, write the script + art-direction package
.\venv\Scripts\python.exe main.py
# -> prints tmp/<slug>.script.txt (paste this into ElevenLabs) and the exact
#    --render command to run once you've downloaded the voiceover audio

# Step 2: paste the script into ElevenLabs, download the audio, then:
.\venv\Scripts\python.exe main.py --render <slug> <path-to-audio-file>
# -> transcribes the audio for caption timestamps, generates 6 wolf images,
#    renders the karaoke MP4 + 6-slide carousel

# Install deps
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

`main.py` logs to both the console and `logs/pipeline.log`, and reconfigures the
console streams to UTF-8 so non-ASCII titles (curly quotes, em dashes)
never crash the run on Windows' cp1252 default.

### Running / "testing" individual stages

There is **no pytest suite**. Each module under `modules/` has a `__main__` smoke harness that *is* the test — run a stage in isolation from the repo root:

```powershell
.\venv\Scripts\python.exe -m modules.script_gen       # generate a script + art-direction package (real Claude call)
.\venv\Scripts\python.exe -m modules.script_history    # print the dedup ledger (no API calls)
.\venv\Scripts\python.exe -m modules.transcribe         # transcribe newest tmp/*.mp3
.\venv\Scripts\python.exe -m modules.slide_gen          # render the deck from built-in SAMPLE_HIGHLIGHTS (no AI calls; solid backgrounds)
.\venv\Scripts\python.exe -m modules.image_gen          # generate gpt-image-2/gradient backgrounds from cached plan (image_scenes -> composed prompts) — primary background source
.\venv\Scripts\python.exe -m modules.video_gen          # render the newest cached script+audio pair (requires an existing tmp/<slug>.plan.json — run main.py --render first)
```

Most harnesses run downstream-to-upstream and assume upstream artifacts already
exist in `tmp/` (e.g. `video_gen`'s harness needs an existing `tmp/<slug>.plan.json` +
matching mp3, both produced by `main.py`/`main.py --render`). `slide_gen` is the
only one runnable with zero setup (it has a baked-in `SAMPLE_HIGHLIGHTS`).

## Architecture

### Two-command pipeline, split around the manual voiceover step

`main.py` is deliberately split into two commands because voiceover generation is
manual (ElevenLabs, pasted by hand — no API integration). Every module is a thin
function taking/returning plain dicts and file paths. **`config.py` is the single
source of truth** for all paths, dimensions, model IDs, and tuning constants —
modules import from it rather than hardcoding. Change behavior there, not in
module bodies.

**Step 1 — `main.py`** (`main.generate()`):
1. `script_gen.generate_script_with_retry()` — one Claude call: picks a topic
   (informed by `script_history.recent(8)`, drawn evenly from all 5 clusters —
   see Content direction below), and writes the full script + copy +
   art-direction package in one shot: `hook, script, insights, key_line,
   title, hashtags, wolf_outfit, image_scenes[6]`. No transcript involved —
   this is original narration, not an extraction.
2. Writes `tmp/<slug>.script.txt` (the plain narration text — paste this into
   ElevenLabs) and caches the full package as `tmp/<slug>.plan.json`.
3. `script_history.record(topic_cluster, hook, title)` — logs the topic to the
   dedup ledger immediately, so it's never regenerated even if step 2 never
   runs.
4. Prints the exact `main.py --render <slug> <audio_path>` command to run next.

**Step 2 — `main.py --render <slug> <audio_path>`** (`main.render()`), once
you've pasted the script into ElevenLabs and downloaded the audio:
1. Loads the cached `tmp/<slug>.plan.json`.
2. `transcribe.transcribe(audio_path)` — Groq Whisper, but now purely for
   word-level **caption timestamps**; the words aren't used for content
   selection the way they were in the old transcript-extraction pipeline.
3. `background.select_backgrounds(highlights, basename=slug)` — gpt-image-2
   AI image → gradient (unchanged from before the migration).
4. `video_gen.build_video(...)` — the karaoke MP4, watermarked with
   `config.BRAND_NAME` ("Icarus Wings") always (there's no per-episode
   podcast name anymore).
5. `slide_gen.build_slides(highlights, photo_paths=backgrounds)` — the 6-PNG
   carousel on the same wolf images.
6. Prints a MANUAL-POST checklist of local file paths. **Publishing is fully
   manual by user decision** — no YouTube/Meta/R2 upload code exists.

### Data contracts (what each module produces/consumes now)

- `transcribe.transcribe(audio_path)` → `{"text", "segments":[{start,end,text}], "words":[{word,start,end}]}` (Groq `whisper-large-v3`, word + segment granularity). Same module, same contract as before the migration — it's audio-source-agnostic, so pointing it at an ElevenLabs file instead of a podcast episode needed zero changes.
- `script_gen.generate_script_with_retry(attempts=3)` → wraps `generate_script(recent_history)`, catching only `ValueError` (schema/brand-gate failure) and `anthropic.RateLimitError`, same 3-attempt/65s-sleep retry shape the old `extract_copy_with_retry` used. Returns exactly these keys:
  `topic_cluster, hook, script, insights[3], key_line, title, hashtags[3-8], wolf_outfit, image_scenes[6], clip_start, clip_end`.
  - `topic_cluster` is one of `config.TOPIC_CLUSTERS` (5 fixed buckets, all treated equally by the prompt: `fear_anxiety_rumination`, `individuality_vs_conformity`, `money_as_freedom`, `neurology_focus_motivation`, `identity_resilience_meaning`). Used only for the dedup ledger; the model still free-picks a specific topic each run.
  - `script` is the full spoken narration as one continuous piece (what gets written to `tmp/<slug>.script.txt`) — never bullet points, never stage directions.
  - `key_line` replaces the old `best_quote` — nothing is quoted from a real speaker anymore, so the field is named for what it actually is: a punchy written line for the QUOTE slide. Same "carved in stone" character bar as before.
  - `clip_start`/`clip_end` are always the fixed sentinel `0.0`/`9999.0`, injected in code — there's no transcript window to compute, and `video_gen.build_video` already clamps to the real audio duration at render time regardless of what's cached (proven by the first manual test render).
  - `image_scenes` is a list of **6 objects** `{"beat", "concept", "action", "setting", "camera"}` — structured scene CONTENT for the illustrated backgrounds (the visual STYLE is applied later by `image_gen.compose_prompts`, see Background selection below). Beats follow the fixed story arc `config.IMAGE_SCENE_BEATS` = `[problem, problem, stakes, reframe, payoff, payoff]`, mapped onto 4 quarters of the **script** (not a transcript excerpt) via a mandatory STEP-1 content-analysis instruction in the prompt. `wolf_outfit` is ONE outfit worn unchanged across all 6 scenes. The SAME 6 images also back the entire slide carousel (see slide_gen below).
  - `_validate()` enforces types/counts (`insights` must have exactly 3 items, `topic_cluster` must be a known cluster) and normalizes `image_scenes` to exactly 6 via `_normalize_image_scenes()` (truncates extras, RAISES on a shortfall so the retry wrapper re-generates rather than padding — padding would duplicate images on screen). `_scene_safety_gate()` scans `action`/`setting` (never `concept`, which restates the script) for banned crowd/female/multi-person words — ported unchanged from the old `ai_extract.py`. `_brand_gate()` checks the hook uses a viewer-addressed identity frame and at least 2/3 insights are 2nd-person — also ported unchanged.
  - **Generation is non-deterministic** — identical calls yield different topics/hooks/JSON run to run, same as the old two-stage extraction was. `generate_script_with_retry` recovers by re-generating a whole new script on `ValueError`/rate-limit (there's no "fixed window" to preserve the way Stage 2 extraction had — a full re-generation is the natural retry unit here).
- `script_history.recent(limit=8)` / `record(topic_cluster, hook, title)` — the dedup ledger (`tmp/script_history.json`, a flat JSON list of `{date, topic_cluster, hook, title}`). `script_gen`'s prompt is fed the recent entries and told not to repeat the immediately-preceding `topic_cluster` or reuse a similar hook/title.
- `background.select_backgrounds(highlights, basename)` → a list of `.png` paths (unchanged).
- `video_gen.build_video(audio_path, words, highlights, podcast_name=, background_images=)` → output MP4 path (unchanged signature; `main.py` always passes `config.BRAND_NAME` now instead of a per-episode podcast name).
- `slide_gen.build_slides(highlights, photo_paths=)` → list of 6 ordered PNG paths (COVER, INSIGHT x3, QUOTE, FOLLOW) (unchanged signature, `key_line` key rename only).

### Content direction (the non-negotiable brand purpose)

Every script and carousel must serve at least one of these four outcomes for the viewer:
- **Self-awareness** — they understand their own behavior, mind, or patterns better
- **New perspective** — they see themselves or life through a lens they didn't have before
- **Hope + agency** — they leave feeling there is a path forward, not trapped
- **Self-knowledge** — they learn something true about how humans (and therefore they) work

The content universe is: human behavior, neurology, focus, motivation, identity, resilience, self-improvement, meaning, and money/wealth **when reframed as freedom or identity** (never personal-finance tips). **Overthinking is a side-topic only** — never the primary theme. **Relationships are banned entirely** — no romantic/dating/marriage content under any framing (standing user rule). A script that only diagnoses a problem without offering uplift, a new lens, or implied agency fails the brand. Banter, trivia, and entertainment anecdotes with no transferable insight are always rejected.

**Topic clusters — all 5 treated equally by `script_gen`'s prompt (user decision, 2026-08-02: stop favoring the historically "proven" 3):** `fear_anxiety_rumination` (fear/anxiety/rumination mechanisms), `individuality_vs_conformity` ("the courage to want more than the crowd finds acceptable"), `money_as_freedom` (money reframed as freedom/identity), `neurology_focus_motivation` (brain/nervous-system mechanics, focus, motivation), `identity_resilience_meaning` (identity, resilience, meaning/purpose). The performance data in the section below is retained as historical context only — it no longer biases which cluster the prompt favors.

`script_gen` is told not to repeat the most-recently-used `topic_cluster` (via `script_history`), so back-to-back runs naturally spread across the content universe rather than converging on one theme.

### Content performance data (drives prompt decisions)

Production data from the same account (YouTube + TikTok + Instagram), captured while this pipeline still sourced clips from real podcast episodes — showing 100x+ variance between best and worst content. These numbers directly shaped `script_gen.SYSTEM_PROMPT`'s HOOK RULES and topic-cluster priority, and remain the operating assumption now that scripts are written from scratch: the same hook mechanics and topic clusters are what's being targeted, just via original narration instead of extracted speech. Updated Jun 16, 2026 (last-28-days window):

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

These findings drove the HOOK RULES contrarian-identity-frame formula in `script_gen.SYSTEM_PROMPT`.

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

These findings refined `script_gen`'s HOOK RULES: the neurological "your brain/nervous system does X to you" frame stays the top-priority default (best retention, most reliable), identity-stakes numbered rules are a validated alternate structure, and metaphor-based hooks must be unpacked concretely within the script's opening seconds rather than left abstract.

### One visual track: the illustrated wolf scenes back EVERYTHING

There is a single art-directed visual track: **`image_scenes`** (exactly 6 objects) + **`wolf_outfit`** — structured scene CONTENT for the script's illustrated wolf images (see Background selection below for the composition architecture). These describe what the wolf character is doing (`action` with a LITERAL prop tied to that quarter's words), where (`setting`, varied, out-in-the-world), and how it's framed (`camera`), following the fixed 6-beat story arc. A mandatory STEP-1 content analysis (name each quarter's specific concept before writing any scene, now dividing the **script** into quarters instead of a transcript window) anchors every scene to the actual narration. The 6 generated images serve BOTH consumers: the video's Ken Burns background montage AND the full slide carousel (all 6 slides, including FOLLOW — one branded body of work per post).

**Cross-cutting prompt guards (all in `script_gen.SYSTEM_PROMPT`):**
- **Hook formula (contrarian identity frame OR self-categorization frame)** — the `hook` field must use one of several proven structures. Contrarian-identity hooks challenge the viewer's worldview and imply they're on the wrong side of a divide. **Self-categorization hooks (added 2026-08-02, inspired by a competitor-channel analysis)** instead force the viewer to sort themselves into one of two sharply contrasting types ("Anxious or avoidant — which one are you?") — both types must be instantly recognizable from a single clause so the viewer self-sorts within the hook itself, not at the payoff; weighted as heavily as the default brain/nervous-system formula. Instructional hooks ("X tips for Y", "How to…", "The science of…") are explicitly banned. The hook must be under 15 words. **Named mechanism rule (added 2026-08-02):** when the REFRAME rests on a real psychological phenomenon (cognitive dissonance, loss aversion, the Zeigarnik effect, sunk-cost fallacy, etc.), the script must name it explicitly rather than leaving it as a vague unnamed claim — but never fabricate a fake-sounding scientific name for something that isn't real.
- **NEVER DEPICT (image scenes) blacklist** — inside the IMAGE_SCENES rules: no skull/skeleton/death imagery; no cigarettes/alcohol/drugs/vices; no slumped or defeated posture; no violence/gore; no crowds or extra figures of any kind (a busy street/market as an anonymous backdrop is fine — the wolf is the only clearly-rendered figure); no floating/decorative typography. Contextual PROP TEXT is allowed and encouraged: the `action` field may specify 2-6 short readable words for a prop that naturally carries them, taken from that quarter's idea. Backed in code by `script_gen._scene_safety_gate` (regex on `action`/`setting`) and `image_gen.NEGATIVE_BLOCK` in every composed prompt.
- **`insights` ≤ 100 chars** — hard cap so `slide_gen._fit_body` never shrinks copy to thumbnail-illegible size. Insights must be IDENTITY STATEMENTS, not explanations.
- **`key_line` character rule** — must feel carved in stone: timeless, defiant, memorable, under 25 words. It's a written line from the script now, not an extracted quote — never implied to be someone's real spoken words.

### Background selection — ordered fallback chain

`background.select_backgrounds()` is a degrade-never chain (see `modules/background.py`), unchanged by the migration:
1. **OpenAI `gpt-image-2` AI image** (`image_gen.generate_backgrounds`, primary) — generates one on-brief image per composed prompt (`config.IMAGE_PROMPT_COUNT` = 6, one per ~8-9s of the clip) via the OpenAI Images API (`config.OPENAI_IMAGE_MODEL`/`OPENAI_IMAGE_SIZE`/`OPENAI_IMAGE_QUALITY`, currently `gpt-image-2` / `1024x1536` / `medium`, ~$0.28/short); needs `OPENAI_API_KEY`. Retries on 429/5xx with backoff.

   **Style/scene split architecture:** `script_gen` emits structured `image_scenes` CONTENT only (beat/concept/action/setting/camera + one per-script `wolf_outfit`), and `image_gen.compose_prompts()` assembles the final prompt from **locked code constants** — `STYLE_BLOCK` (vintage halftone comic-book, warm vibrant mustard/terracotta/kelly-green palette, bright sunlit mood), `BEAT_MOODS` (per-beat arc mood), `DEFAULT_OUTFIT`, and `NEGATIVE_BLOCK`. **Edit the look in `image_gen.py`, never in `script_gen.SYSTEM_PROMPT`** — style can no longer drift with model paraphrasing, and a style tweak needs no re-generation. The 6 images form one **story arc** (`config.IMAGE_SCENE_BEATS`: problem ×2 → stakes → reframe → payoff ×2) with the wolf in the SAME outfit throughout, each scene containing a LITERAL prop tied to that quarter's words. The wolf-character rules (upright, human posture, always alone) and the bright/warm mood rule are unchanged, enforced in code.
2. **Local gradient PNGs** (`image_gen` fallback) — always succeeds; used when the key is missing or every retry is exhausted.

The returned list is **homogeneous**: always `.png` (image). `video_gen._background_layers()` dispatches on the first path's extension.

### video_gen is the core (1080×1920 karaoke clip)

`modules/video_gen.py` is the most intricate file, and its rendering logic is **entirely unchanged by the migration** — proven by rendering fully synthetic content through it unmodified on the first test:
- **MoviePy v2 API** — flat imports (`from moviepy import ...`), fluent `.with_*` / `.resized` / `.cropped` methods, and effect objects (`vfx.CrossFadeIn`, `afx.AudioLoop`, `afx.MultiplyVolume`, `afx.AudioFadeIn/Out`). Do not use MoviePy v1 patterns (`set_*`, `crossfadein=`).
- Word-level captions: `group_words()` chunks `words` into groups (max `CAPTION_WORDS_PER_GROUP`, also breaking on pauses > `CAPTION_GROUP_GAP`); `_render_block()` draws each group to an RGBA array with the active word highlighted (Pillow, not MoviePy `TextClip`, for per-word styling). The caption band is centered at `CAPTION_CENTER_Y` (0.62) to clear the Reels/Shorts bottom UI.
- Backgrounds: images get a Ken Burns zoom/pan (`_image_background_layers`), with a 50% dark overlay. **Per-slot duration is capped at `config.MAX_BG_CLIP_DURATION` (18s)**.
- **Background music bed:** `_music_track()` loops/trims `config.MUSIC_PATH` to the window, drops it to `MUSIC_GAIN_DB` (−18 dB) under the voice, and fades in/out. Absent file → voice-only, no crash.
- Watermark: `_render_watermark()` draws `config.BRAND_NAME` ("Icarus Wings") as white text on a semi-transparent dark rounded pill, bottom-right — always on now (there's no per-episode podcast name to omit anymore; `main.py` always passes `config.BRAND_NAME`).
- Fonts are loaded from `C:\Windows\Fonts` (`arialbd.ttf` etc.) — Windows-specific.
- `_ends_sentence()` (the render-time sentence guard's helper) is now defined locally in this file — it used to be imported from the deleted `ai_extract.py`. Same implementation, just relocated since it had a single caller here.

### slide_gen — separate editorial carousel (1080×1350, 4:5)

`modules/slide_gen.py` renders a 6-slide Instagram carousel: **COVER (hook) → INSIGHT 01/02/03 → QUOTE → FOLLOW (CTA)**. This is a **4:5 portrait (1080×1350)** deck — a different aspect ratio from the 9:16 video, and a wholly separate artifact. **Unchanged by the migration except one key rename** (`best_quote` → `key_line`, since nothing is a real quote anymore):
- **Branded deck:** every slide — including FOLLOW — is backed by the script's **generated wolf images**, passed from the render as `build_slides(highlights, photo_paths=backgrounds)` and mapped by `_map_photos_to_slides`: cover = scene 1 (problem), insights = scenes 2-4, quote = scene 6 (resolved payoff), FOLLOW = scene 5 (payoff-in-action) — all 6 images used, video + carousel one body of work.
- Design system: yellow accent eyebrow with a tick bar, near-white auto-fitting body (`_fit_body` shrinks + re-wraps), persistent footer (wordmark + `TOTAL_SLIDES` (6) progress dots). Fonts: bundled DejaVu Sans Bold in `assets/fonts/` with Windows fallbacks.
- Quote attribution is rendered **only if** the highlights dict actually carries one (`_attribution()` checks keys like `quote_author`/`speaker`); `script_gen`'s schema produces none, so the quote slide always shows no attribution — we never invent a speaker, and now there genuinely isn't one.

### Caching to avoid burning API credits

- `tmp/<slug>.script.txt` — the plain narration text written by `main.generate()`, for you to paste into ElevenLabs.
- `tmp/<slug>.plan.json` = `{highlights}` — the full `script_gen` output, cached under the slugified title so `main.py --render <slug> <audio_path>` can pick it back up without re-hitting Claude.
- `tmp/script_history.json` — the topic dedup ledger (see Architecture above). `record()` writes here immediately after step 1's package is built, so a topic is never regenerated even if step 2 never runs.
- `image_gen` caches generated images as `tmp/<slug>_bg_<n>.png` (namespaced per script). Pass `force=True` to regenerate. A new script always generates fresh images; re-rendering the same slug reuses its own cache.
- When iterating on rendering, rely on these caches rather than re-running `main.py` (which would burn a fresh Claude call for a new topic).

## External services & secrets

Copy `.env.example` → `.env`. Exactly three keys:
- `ANTHROPIC_API_KEY` (Claude — script generation via `script_gen.py`), `GROQ_API_KEY` (Whisper — now used only for caption timestamps on the ElevenLabs audio, not content selection), `OPENAI_API_KEY` (gpt-image-2 wolf images).

No ElevenLabs API key — voiceover generation is a manual copy-paste-download step by design (not automated in this pass; see the design spec for the deferred API-integration option).

Modules call `load_dotenv()` themselves so they work standalone.

Model IDs: Claude `claude-sonnet-5` (`config.EXTRACT_MODEL` — used by `script_gen.py`), Groq `whisper-large-v3` (`transcribe.MODEL`), OpenAI `gpt-image-2` (`config.OPENAI_IMAGE_MODEL`, `1024x1536` @ `medium` quality — `image_gen.py`). The old `claude-sonnet-4-20250514` 404s on this account (retired) — never regress past the current Sonnet.

## Gotchas / current state

- **Backgrounds switched from Pexels stock video to AI images on 2026-07-26**, then `gpt-image-1` → `gpt-image-2` on 2026-07-29 (pure cost win, same API contract). AI-generated images are on-brief every time and cost ~$0.28/short (6 images @ medium quality) — inside a $10/month budget at current posting cadence. Trade-off accepted knowingly: stills + Ken Burns pan/zoom, not real captured motion.
- **A suspected "whole-clip caption/audio desync" turned out to be a false positive from a flawed verification method — corrected here so it isn't re-litigated.** An investigation using `ffmpeg -vf fps=1/3` to bulk-sample frames appeared to show captions running ~1-1.5s ahead of the audio for an entire clip. A later re-verification using precise exact-seek frame extraction (`ffmpeg -ss T -frames:v 1`, not the `fps` filter) showed **perfect sub-100ms caption/audio sync throughout an entire clip**. **The `fps` filter's output frame timestamps do not reliably correspond to simple multiples of the sampling interval starting at 0**; using it for precise sync verification was itself the bug. If a caption/audio sync issue is ever reported again, verify with exact-seek single-frame extraction, never `fps`-filter bulk sampling.
- **The real caption bug found during that same re-verification: out-of-order Whisper word timestamps can cause two caption blocks to render on top of each other.** Whisper occasionally emits a word with an earlier `start` than the word immediately before it in the transcript. `video_gen.py`'s caption-clip loop assumes non-decreasing word start times; an out-of-order word can start its caption clip before the previous one has finished, producing a garbled overlap. Fixed at the source: `transcribe.transcribe()` runs every returned `words` list through `_enforce_monotonic_words()`, which clamps each word's start/end to at least the previous word's end (in original text order — never reorders words). This is a guaranteed invariant of `transcribe()`'s return value, and applies exactly the same to ElevenLabs audio as it did to podcast audio.
- **`image_gen` background cache is namespaced per `basename`/`slug`** (fixed 2026-07-29, before the migration) — `tmp/<slug>_bg_<n>.png`. A new script always regenerates; re-rendering the same slug reuses its own cache. If AI background art ever looks stale/unrelated to the script's content, check whether the `basename`/`slug` plumbing regressed before assuming a prompt problem.
- **Background music is mixed at −18 dB** (`MUSIC_GAIN_DB`) under the full-volume voice, with 1.0s/1.5s fades. The single track lives at `assets/music/background.mp3`; if it's missing the render silently goes voice-only.
- **Script *duration* is a soft target; script *word count* is a hard-enforced cap (as of 2026-08-02).** `script_gen`'s prompt targets `config.CLIP_WINDOW_MIN_SECONDS`-`CLIP_WINDOW_MAX_HARD_SECONDS` (35-50s, tightened from 45-58s per user directive — their ElevenLabs renders must never cross 50s) of spoken narration, but there's no transcript to snap to, so the REAL, final duration is whatever the ElevenLabs voiceover comes out to; `video_gen.build_video` clamps `clip_end` to the actual audio duration regardless of what's cached (`highlights["clip_end"]` is always the sentinel `9999.0`). What IS enforced in code now: `config.SCRIPT_MAX_WORDS` (130) — `script_gen._validate()` raises `ValueError` (triggering a full retry, same as any other gate) if the generated `script` exceeds it, because the prompt-only target of ~150 words was previously missed (a 154-word script slipped through). Proven fine at 39.7s in the first manual test render, well under either bound.
- **`script_gen` generation is non-deterministic — wrapped in a retry helper.** Identical calls (same recent-history context) yield different topics/hooks/JSON run to run. `_strip_to_json` isolates the **first complete JSON object** via `json.JSONDecoder().raw_decode` (tolerates trailing data the model sometimes appends), but schema/count/brand-gate failures raise `ValueError`. `script_gen.generate_script_with_retry(attempts=3)` catches only `ValueError` and `anthropic.RateLimitError`, sleeps 65s between attempts, and re-raises the last error after the 3rd. Unlike the old two-stage extraction (which could retry copy for a fixed, already-approved window), there's no "fixed window" to preserve here — a retry regenerates an entirely new topic/script from scratch.
- **Background pillarboxing (FIXED — was NOT final-slot-only).** The earlier claim that black side bars only affected the final slot (hidden elsewhere by crossfades) was **empirically false**: a bare edge appeared mid-slot on any slot that ran the **Ken Burns** path. Root cause: `_ken_burns_motion` used a **time-varying `.resized()` AND time-varying `.with_position()` together**, and MoviePy v2 does **not** composite that combination the way the centering math predicts. Fix: `_ken_burns_motion` now resizes ONCE by a **constant** over-scale and pans only via a **clamped** `with_position`. `_video_background_layers` also re-crops each slot to **exactly** 1080×1920 before Ken Burns. **Verify every render with the edge-brightness scan**, not just a visual tail grab: sample one frame every ~2s across the WHOLE video, measure mean brightness of the leftmost/rightmost 15px strips over the middle 60% of height, and FAIL if any frame has one edge < 8 while the opposite is > 15. The `render-verifier` agent runs this scan automatically.
- `ffmpeg`/`ffprobe` are on PATH (winget Gyan.FFmpeg) for manual dimension/frame checks — separate from the `imageio_ffmpeg` binary MoviePy uses.

## What's still TODO

Scope decisions (user directives — do NOT re-propose): publishing stays MANUAL
on every platform, no scheduling/automation, no R2/cloud storage, no wolf
avatar/badge assets unasked, no ElevenLabs API integration (voiceover stays a
manual paste-and-download step).

Genuinely open:
- **Post the first real synthetic-script render** — the migration's own manual test (`brain_replays_the_argument.mp4`) validated the mechanics, and the first `main.py`/`main.py --render` end-to-end run produced a second clean render, but nothing from this track has been posted/judged for real performance yet.
- **ElevenLabs API integration** — deferred future upgrade: a `tts_gen.py` module calling ElevenLabs directly (with word-level timestamps from their API, dropping the `transcribe.py` step) once the manual flow is validated over more than a couple of renders.
- **Retention tracking file** — per published short: hook family, topic cluster, then retention % filled in weekly; average-view-duration % is the metric that predicts Shorts distribution.
- **Wolf-cover grid experiment** — the branded carousel cover is an unproven bet against a 14x dramatic-landscape data point from the old pipeline's Instagram grid; judge after a few posts.
- **Optional 2-second hook-card A/B** on the video's opening frames.
- **No pytest suite** — the per-module `__main__` harnesses are the only "tests".
