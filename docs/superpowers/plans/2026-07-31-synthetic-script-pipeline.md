# Synthetic Script Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace podcast RSS ingestion + transcript extraction with a topic-driven script generator, keeping every downstream render module (images, video, slides) unchanged, and wire it into a two-command `main.py` around a manual ElevenLabs voiceover step.

**Architecture:** `modules/script_gen.py` (one Claude call: picks a topic, writes hook/script/insights/key_line/title/hashtags/wolf_outfit/6 image_scenes) + `modules/script_history.py` (flat JSON dedup ledger) replace `rss_ingest.py`, `ai_extract.py`, `candidate_bank.py`, `posted_history.py`. `main.py` becomes `main.py` (generate script + images... no — generate script only, per Approach B) / `main.py --render <slug> <audio_path>` (transcribe the manually-supplied ElevenLabs audio for word timestamps, then run the untouched `background`/`video_gen`/`slide_gen` chain).

**Tech Stack:** Python 3.12, `anthropic` SDK (Claude), existing `modules/transcribe.py` (Groq Whisper — repurposed to timestamp the ElevenLabs audio instead of podcast audio), existing `modules/image_gen.py`/`background.py`/`video_gen.py`/`slide_gen.py` (all untouched). No pytest — per-module `__main__` harnesses are this repo's only tests.

## Global Constraints

- No pytest suite exists or is being added — every task's test step is a `__main__` harness run or a `python -c` snippet, per this repo's established convention (see CLAUDE.md's "Running / testing individual stages").
- Secrets stay at exactly 3 keys: `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `OPENAI_API_KEY`. No ElevenLabs API key — voiceover generation stays a manual copy-paste-download step.
- Brand/content rules carry over unchanged from `ai_extract.py`: contrarian-identity-frame hook formula, the 4-outcome brand mission (self-awareness / new perspective / hope+agency / self-knowledge), banned topics (relationships, personal-finance tips, banter/trivia — relationships are a hard exclusion per standing user direction), `insights` ≤100 chars as identity statements, exactly 6 `image_scenes` following the fixed `config.IMAGE_SCENE_BEATS` arc with the NEVER-DEPICT safety blacklist.
- `best_quote` is renamed to `key_line` everywhere (schema field + the one `slide_gen.py` key it's hard-required under) — nothing is being quoted from a real speaker anymore.
- `video_gen.build_video`, `slide_gen.build_slides`, `background.select_backgrounds`, `image_gen.*` get ZERO logic changes — proven working end-to-end with synthetic content by the 2026-07-31 manual test render (`output/videos/brain_replays_the_argument.mp4`, verified clean by the render-verifier: 1080x1920, no pillarboxing).
- `config.BRAND_NAME = "Icarus Wings"` already exists and is already wired into `main.py`'s watermark call — no change needed there.

---

### Task 1: Clean up `config.py`

**Files:**
- Modify: `config.py`

**Interfaces:**
- Consumes: nothing (pure constants file).
- Produces: `config.TOPIC_CLUSTERS: list[str]` (5-element fixed taxonomy), `config.SCRIPT_HISTORY_PATH: str` — both consumed by Task 2/3's new modules. `config.CLIP_WINDOW_MIN_SECONDS`/`config.CLIP_WINDOW_MAX_HARD_SECONDS` keep their existing names and values (40→ wait, keep at 45/58 as already set) but their meaning changes from "hard extraction window" to "soft script-length guidance" — same names so `video_gen.py:474`'s existing fallback reference (`highlights.get("clip_end", req_start + config.CLIP_WINDOW_MIN_SECONDS)`) needs no edit.

- [ ] **Step 1: Remove dead podcast/RSS constants**

Delete these blocks from `config.py` (confirmed by repo-wide grep: every reference lives in `modules/rss_ingest.py`, `modules/ai_extract.py`, or `modules/candidate_bank.py`, all deleted in Task 7):

```python
# DELETE lines 20-52: PODCAST_FEEDS = { ... }
# DELETE lines 54-70: PODCAST_HOSTS = { ... }
# DELETE lines 72-73: DEFAULT_FEED = "mindset_mentor"
# DELETE lines 75-85: ROTATION = { ... }
# DELETE lines 87-114: EPISODE_REJECT_KEYWORDS = [ ... ]
# DELETE lines 116-145: EPISODE_REJECT_DESC_PHRASES = [ ... ]
# DELETE lines 147-166: EPISODE_BRAND_KEYWORDS = [ ... ]
# DELETE lines 168-176: PRESCREEN_ENABLED / PRESCREEN_MODEL / PRESCREEN_MAX_ATTEMPTS (+ comment)
# DELETE lines 178-182: CONTENT_GATE_ENABLED / CONTENT_GATE_MODEL (+ comment)
# DELETE lines 184-187: POSTED_HISTORY_PATH (+ comment)
# DELETE lines 189-198: BROWSER_HEADERS = { ... } (+ "--- HTTP ---" comment)
# DELETE lines 200-202: FEED_NAME_MAX_LEN / EPISODE_TITLE_MAX_LEN (+ comment)
# DELETE lines 255-256: CANDIDATE_COUNT (+ comment)
# DELETE lines 258-266: CANDIDATE_BANK_PATH / SCAN_EPISODES_PER_RUN / EPISODE_CLIP_SPACING_DAYS / BANK_REVIEW_COUNT (+ comment block)
```

- [ ] **Step 2: Repurpose the clip-window constants, drop the unused one**

Replace lines 212-222 (the `CLIP_WINDOW_*` block) with:

```python
# Target spoken duration for modules/script_gen.py's scripts, in seconds.
# There is no transcript to snap to anymore -- the REAL, final duration is
# whatever the ElevenLabs voiceover comes out to; video_gen.build_video
# already clamps clip_end to the actual audio duration regardless of what's
# cached here (proven by the 2026-07-31 manual test: a 39.7s script rendered
# clean with no floor enforcement needed). Capped at 58s so the finished
# Short stays UNDER 60s -- YouTube blocks the music bed on Shorts >= 60s.
CLIP_WINDOW_MIN_SECONDS = 45
CLIP_WINDOW_MAX_HARD_SECONDS = 58
```

(`CLIP_WINDOW_MAX_SECONDS = 58` is dropped entirely — confirmed unused anywhere in live code, only self-referenced.)

- [ ] **Step 3: Add the topic taxonomy and dedup-ledger path**

Add this block right after the `IMAGE_SCENE_BEATS` line (currently line 284, inside the "AI-generated themed backgrounds" section is fine, or its own section — add as its own section right before "--- Slide dimensions ---"):

```python
# --- Script topic clusters (modules/script_gen.py) ---
# Fixed taxonomy so modules/script_history.py's dedup ledger stays consistent
# across runs. The model still free-picks a SPECIFIC topic each run; this only
# tags which bucket it falls in. First 3 are the proven top performers (see
# CLAUDE.md's content-performance data) and should be favored by the prompt;
# the other 2 cover the rest of the content universe.
TOPIC_CLUSTERS = [
    "fear_anxiety_rumination",
    "individuality_vs_conformity",
    "money_as_freedom",
    "neurology_focus_motivation",
    "identity_resilience_meaning",
]

# Dedup ledger: every topic modules/script_gen.py has generated, so each new
# run can avoid repeating a recent topic_cluster or hook. See
# modules/script_history.py.
SCRIPT_HISTORY_PATH = os.path.join(TMP_DIR, "script_history.json")
```

- [ ] **Step 4: Verify the module still imports cleanly**

Run: `venv\Scripts\python.exe -c "import config; print(config.TOPIC_CLUSTERS); print(config.SCRIPT_HISTORY_PATH); print(config.CLIP_WINDOW_MIN_SECONDS, config.CLIP_WINDOW_MAX_HARD_SECONDS); print(config.BRAND_NAME)"`

Expected: prints the 5-element cluster list, the `tmp\script_history.json` path, `45 58`, and `Icarus Wings` — no `ImportError`/`AttributeError`.

- [ ] **Step 5: Commit**

```bash
git add config.py
git commit -m "config: drop podcast/RSS/bank constants, add script topic taxonomy + dedup ledger path"
```

---

### Task 2: Create `modules/script_history.py`

**Files:**
- Create: `modules/script_history.py`

**Interfaces:**
- Consumes: `config.SCRIPT_HISTORY_PATH`.
- Produces: `load() -> list[dict]`, `recent(limit: int = 8) -> list[dict]`, `record(topic_cluster: str, hook: str, title: str) -> None` — all three consumed by `modules/script_gen.py` (Task 3, `recent`) and `main.py` (Task 6, `record`).

- [ ] **Step 1: Write the module**

```python
"""Dedup ledger for synthetic script topics.

A tiny JSON store at ``config.SCRIPT_HISTORY_PATH`` (``tmp/script_history.json``)
listing every topic ``modules/script_gen.py`` has generated, so each run's
topic pick can avoid repeating a recent topic_cluster or hook. Much simpler
than the old ``posted_history.py``: no GUIDs, no used/rejected states -- just
a flat, append-only log.

Contract:
  * shape: a JSON list of ``{"date", "topic_cluster", "hook", "title"}``,
    newest last.
  * ``record()`` is the only writer, called once by ``main.py`` right after
    ``script_gen``'s output is written to disk -- the topic is spent whether
    or not the render step ever runs, same timing philosophy as the old
    ``posted_history.mark_used()``.
  * read/write are both crash-safe: a missing or corrupt file reads as empty
    so a bad log never breaks a run.
"""

import json
import logging
import os
from datetime import date

import config

logger = logging.getLogger(__name__)

PATH = config.SCRIPT_HISTORY_PATH


def load() -> list[dict]:
    """Return the history list, or ``[]`` if the file is missing/unreadable."""
    if not os.path.exists(PATH):
        return []
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read script history (%s); treating as empty", exc)
        return []


def recent(limit: int = 8) -> list[dict]:
    """Return the ``limit`` most recently recorded entries, oldest first within
    the slice (so ``entries[-1]`` is always 'the most recently used topic')."""
    return load()[-limit:]


def record(topic_cluster: str, hook: str, title: str) -> None:
    """Append a new entry."""
    data = load()
    data.append({
        "date": date.today().isoformat(),
        "topic_cluster": topic_cluster,
        "hook": hook,
        "title": title,
    })
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Recorded script topic: [%s] %r", topic_cluster, title)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    hist = load()
    print(f"Script history: {PATH}")
    print(f"Entries: {len(hist)}")
    for e in hist:
        print(f"  - [{e.get('topic_cluster')}] {e.get('title')!r} (hook: {e.get('hook')!r}, {e.get('date')})")
```

- [ ] **Step 2: Verify empty-state read**

Run: `venv\Scripts\python.exe -m modules.script_history`

Expected: `Entries: 0` (assuming `tmp/script_history.json` doesn't already exist — it shouldn't at this point in the migration).

- [ ] **Step 3: Verify write/read round-trip**

Run:
```
venv\Scripts\python.exe -c "from modules import script_history as sh; sh.record('fear_anxiety_rumination', 'Your brain replays that argument at 2am', 'brain replays the argument'); sh.record('money_as_freedom', 'Test hook 2', 'test title 2'); print(sh.recent(8))"
```

Expected: prints a 2-element list of dicts with the recorded fields; no exception.

Run again: `venv\Scripts\python.exe -m modules.script_history`

Expected: `Entries: 2`, both entries printed.

- [ ] **Step 4: Clean up the test entries**

Run: `venv\Scripts\python.exe -c "import os; os.remove('tmp/script_history.json')"`

(So the real first entry Task 6 records isn't preceded by throwaway test data.)

- [ ] **Step 5: Commit**

```bash
git add modules/script_history.py
git commit -m "Add script_history: dedup ledger for synthetic script topics"
```

---

### Task 3: Create `modules/script_gen.py`

**Files:**
- Create: `modules/script_gen.py`

**Interfaces:**
- Consumes: `config.EXTRACT_MODEL`, `config.EXTRACT_MAX_TOKENS`, `config.TOPIC_CLUSTERS`, `config.IMAGE_PROMPT_COUNT`, `config.IMAGE_SCENE_BEATS`, `config.CLIP_WINDOW_MIN_SECONDS`/`MAX_HARD_SECONDS`, `modules.script_history.recent()`.
- Produces: `generate_script(recent_history: list[dict]) -> dict` and `generate_script_with_retry(attempts: int = 3) -> dict` — both consumed by `main.py` (Task 6). The returned dict always has exactly these keys: `topic_cluster, hook, script, insights, key_line, title, hashtags, wolf_outfit, image_scenes, clip_start, clip_end` (the last two are the fixed sentinel `0.0`/`9999.0`, injected here so downstream `video_gen`/`slide_gen` never need a schema branch for this content source).

- [ ] **Step 1: Write the module**

```python
"""Synthetic script generation via Claude — replaces the old two-stage
transcript extraction (ai_extract.py, deleted) with a single from-scratch
call: pick a topic, write the full short-form script + copy + art-direction
package in one shot.

Sends a system prompt (brand rules ported from the old ai_extract.py) plus
the topic-cluster taxonomy and recent dedup history to Claude, then parses
and validates the JSON response exactly the way the old pipeline did.
"""

import json
import logging
import os
import re
import time

import anthropic
from anthropic import Anthropic
from dotenv import load_dotenv

import config

load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a short-form video scriptwriter. You do NOT extract from any "
    "transcript -- you pick a topic yourself and write an ORIGINAL spoken "
    "script for it, plus its full copy and art-direction package, in one "
    "shot. Nothing in your output is a real quote from a real person; never "
    "claim or imply otherwise.\n\n"
    "You MUST respond with ONLY a single valid JSON object and nothing else "
    "-- no markdown, no code fences, no commentary before or after.\n\n"
    "BRAND MISSION -- READ THIS BEFORE EVERYTHING ELSE:\n"
    "This channel is for people in the gap: they know what they should do, "
    "they've read the books, they're self-aware enough to see their own "
    "patterns -- but they still can't make the shift. Every script must "
    "create a MOMENT OF RECOGNITION ('that's exactly what I do') and then "
    "hand the viewer a new frame, a hidden mechanism, or a realization about "
    "how they actually work -- not a pep talk, not a list of tips. The "
    "viewer must leave feeling: 'I finally understand WHY I do this.' Every "
    "script must serve at least one of these outcomes:\n"
    "  (A) SELF-AWARENESS: they understand their own behavior, mind, or "
    "patterns better.\n"
    "  (B) NEW PERSPECTIVE: they see themselves or life through a lens they "
    "didn't have before.\n"
    "  (C) HOPE + AGENCY: they leave feeling there is a path forward -- not "
    "hopeless, not trapped.\n"
    "  (D) SELF-KNOWLEDGE: they learn something true about how humans (and "
    "therefore they) work.\n"
    "The content universe is human behavior, neurology, focus, motivation, "
    "identity, resilience, self-improvement, meaning, and money/wealth WHEN "
    "reframed as freedom, power, or identity (never as personal-finance tips "
    "or a savings hack). Overthinking is a SIDE TOPIC only -- never the "
    "primary theme. RELATIONSHIPS ARE BANNED ENTIRELY -- do not write about "
    "romantic relationships, dating, marriage, or breakups under any "
    "framing. Banter, trivia, and entertainment anecdotes with no "
    "transferable insight are banned. A script that only diagnoses a problem "
    "without offering a new lens, self-awareness, or implied agency FAILS "
    "the brand mission.\n\n"
    "TOPIC SELECTION:\n"
    "You will be given the list of allowed topic_cluster values and a log of "
    "recently used topics. Pick ONE topic_cluster and a SPECIFIC concrete "
    "topic within it. Favor these three clusters whenever a strong angle is "
    "available -- they are proven top performers: fear_anxiety_rumination "
    "(the single strongest recurring sub-topic), individuality_vs_conformity "
    "(the courage to want more than the crowd finds acceptable), and "
    "money_as_freedom (money reframed as freedom/identity, never budgeting "
    "advice). Do NOT pick the same topic_cluster as the most recently used "
    "one. Never write a hook or title that substantially repeats a recent "
    "one -- pick a genuinely different angle.\n\n"
    "HOOK RULES -- these determine 90% of whether the clip gets views.\n\n"
    "The hook MUST use a CONTRARIAN IDENTITY FRAME. It must challenge the "
    "viewer's current behavior or worldview and imply they are on the wrong "
    "side of a divide. The viewer should feel: 'wait -- am I doing this "
    "wrong?'\n\n"
    "WINNING FORMULA (use one of these structures):\n"
    "  - 'Your [brain/nervous system/body] [won't let go of/is addicted to/"
    "is hijacking you with] [common behavior] -- [until/unless] [condition]' "
    "-- DEFAULT / HIGHEST RETENTION. Prioritize this whenever the script "
    "reveals a psychological/neurological mechanism acting on the viewer "
    "without their awareness. The mechanism must be immediately, viscerally "
    "felt -- not abstract -- so the payoff lands with almost no drop-off.\n"
    "  - 'You're [doing common thing] and it's [unexpected negative "
    "consequence]'\n"
    "  - '[Common belief] is a lie -- here's what [wise/successful people] "
    "actually do'\n"
    "  - 'Every [person/situation] has [two sides] -- [one destroys], [one "
    "elevates]'\n"
    "  - '[Uncomfortable truth] that nobody wants to hear'\n"
    "  - 'Stop [common behavior] -- it's [destroying/weakening] [something "
    "you value]'\n"
    "  - IDENTITY-STAKES NUMBERED RULES: '[Deep identity outcome -- freedom/"
    "power/respect/control] -- [N] [rules/things/truths] that actually "
    "[work/matter]'. The number must serve a contrarian identity payoff, "
    "framed as 'most people get this wrong', never neutral how-to "
    "information.\n\n"
    "BANNED HOOK PATTERNS (these get 2-4 views, proven by data):\n"
    "  - 'X tips/tricks/hacks for Y' -- instructional, zero identity "
    "tension. EXCEPTION: numbered format IS allowed when it follows the "
    "IDENTITY-STAKES NUMBERED RULES structure above.\n"
    "  - 'How to [achieve thing]' -- promises information, not "
    "transformation.\n"
    "  - 'The science behind X' / 'Why X happens' -- educational/"
    "explanatory, no stakes.\n"
    "  - Any hook that could be a YouTube tutorial title.\n\n"
    "METAPHOR HOOK RULE: treat a hook built on a clever/abstract metaphor "
    "(e.g. 'Your fear is a GPS') as HIGH RISK for retention -- confirmed "
    "data shows these win on views but land in the worst retention tier "
    "because the payoff never cashes out before the viewer leaves. Only use "
    "a metaphor hook when the script's own first sentence after the hook "
    "already states plainly what the metaphor means in practice.\n\n"
    "The hook must be under 15 words.\n\n"
    "SCRIPT WRITING RULES:\n"
    "Write the FULL spoken narration as one continuous piece the viewer "
    "hears start to finish -- not a summary, not bullet points. Structure: "
    f"HOOK -> REFRAME (the mechanism/lens, made concrete and felt, not "
    "abstract) -> PAYOFF (a concrete, immediately actionable close -- "
    "something the viewer can DO or SAY, not just a feeling). Target "
    f"{config.CLIP_WINDOW_MIN_SECONDS}-{config.CLIP_WINDOW_MAX_HARD_SECONDS} "
    "seconds at natural spoken pace (roughly 110-150 words). Write in "
    "second person, direct address to the viewer. Never write stage "
    "directions, sound effects, or bracketed notes -- ``script`` is spoken "
    "words ONLY, exactly what a narrator would read aloud.\n\n"
    "The JSON object must have exactly these keys:\n"
    '  "topic_cluster" : string -- one of the provided topic_cluster values.\n'
    '  "hook"           : string -- a contrarian identity-frame hook, under '
    "15 words. See HOOK RULES above.\n"
    '  "script"         : string -- the full spoken narration. See SCRIPT '
    "WRITING RULES above.\n"
    '  "insights"       : array of exactly 3 strings -- the key takeaways, '
    "each <= 100 characters. Write them as IDENTITY STATEMENTS, not "
    "explanations. EVERY insight must use 'you/your', 'me/my' (viewer's "
    "internal voice), or open with a direct imperative ('Move first', "
    "'Stop', 'Choose') -- pure 3rd-person descriptions are banned:\n"
    "    BAD: 'Rumination happens because the brain seeks closure' "
    "(explanatory)\n"
    "    GOOD: 'Your brain won't let go until it gets the ending it never "
    "had' (identity frame)\n"
    '  "key_line"       : string -- the single most quotable line FROM YOUR '
    "OWN SCRIPT (verbatim, or a tightened version of a line that appears in "
    "it). It MUST pass ALL of these tests: (1) works as a standalone "
    "screenshot with zero context; (2) has gravity or precision, not casual "
    "filler; (3) under 25 words; (4) sounds like something worth writing on "
    "a wall. It must feel carved in stone -- timeless, defiant, memorable. "
    "Never merely wise or pleasant.\n"
    '  "title"          : string -- a punchy video title (<= 80 chars).\n'
    '  "hashtags"       : array of strings -- 3 to 8 relevant hashtags, '
    'each starting with "#".\n'
    '  "wolf_outfit"    : string -- ONE outfit for the illustrated wolf '
    "character, worn unchanged in every scene. See IMAGE_SCENES below.\n"
    f'  "image_scenes"   : array of exactly {config.IMAGE_PROMPT_COUNT} '
    "OBJECTS -- structured scene directions for the illustrated background "
    'images, each {"beat", "concept", "action", "setting", "camera"}. See '
    "IMAGE_SCENES below.\n\n"
    f"IMAGE_SCENES -- {config.IMAGE_PROMPT_COUNT} structured scene "
    "directions for the script's illustrated background images. You write "
    "the CONTENT of each scene ONLY -- the locked visual style (a vintage "
    "halftone comic-book illustration of ONE anthropomorphic wolf character "
    "in a warm, bright palette) is appended in code afterwards and is NOT "
    "yours to describe or vary. Never mention art style, palette, lighting "
    "quality, or the wolf's species/appearance in your fields -- only what "
    "happens, where, and how it is framed.\n\n"
    "STEP 1 -- CONTENT ANALYSIS (MANDATORY, do this before writing any "
    "scene): divide YOUR SCRIPT into 4 roughly equal quarters. For EACH "
    "quarter, name in one plain sentence the SPECIFIC concept, claim, or "
    "action it expresses:\n"
    "  Q1: the problem, behavior, or situation being introduced (the hook).\n"
    "  Q2: the mechanism, tension, or why it matters.\n"
    "  Q3: the insight, reframe, or turning point.\n"
    "  Q4: the payoff, resolution, or call to action.\n\n"
    f"STEP 2 -- {config.IMAGE_PROMPT_COUNT} SCENES, ONE STORY ARC, mapped "
    'IN ORDER with these exact "beat" values: '
    f"{json.dumps(config.IMAGE_SCENE_BEATS)}.\n"
    "  Scenes 1-2 (problem): the wolf FACING Q1's specific problem -- two "
    "DIFFERENT settings and camera angles on the same struggle.\n"
    "  Scene 3 (stakes): Q2 made visible -- the weight, cost, or mechanism "
    "of the problem.\n"
    "  Scene 4 (reframe): Q3's turning point -- the moment the new lens "
    "lands.\n"
    "  Scenes 5-6 (payoff): Q4 lived out -- first in action, then resolved "
    "and forward-looking.\n"
    "ARC TENSION RULE: scenes 1-3 may show confrontation and tension -- the "
    "wolf looks AT the problem, upright, jaw set, determined. NEVER "
    "slumped, defeated, head-in-hands, or despairing.\n\n"
    "Each scene object has exactly these keys:\n"
    '  "beat"    : string -- the fixed value for its position.\n'
    '  "concept" : string -- one plain sentence: the specific idea from '
    "STEP 1 this scene illustrates.\n"
    '  "action"  : string -- what the wolf is physically DOING, including a '
    "LITERAL PROP tied to that quarter's actual words (a to-do list, a "
    "phone, a mirror, money, a clock -- whatever it actually is). A viewer "
    "on mute should guess the topic from the image alone. If the prop "
    "naturally carries words, you MAY specify 2-6 short readable words for "
    "it taken from that quarter's idea.\n"
    '  "setting" : string -- where it happens. VARY ACROSS ALL SCENES -- '
    "never the same setting twice; lean OUT-IN-THE-WORLD (gym, sunlit "
    "street, driving a car, rooftop, market, park bench, workshop, balcony, "
    "bus stop) over domestic (home settings at most once per clip).\n"
    '  "camera"  : string -- dynamic film-still framing: low/high angle, '
    "three-quarter view, through a window, tracking alongside a moving "
    "subject, strong foreground/background depth.\n\n"
    'WOLF_OUTFIT: ONE outfit of ordinary human clothes that plausibly works '
    "in ALL of this script's settings, worn UNCHANGED in every scene. No "
    "logos, no readable text on the clothing.\n"
    "NEVER DEPICT (image scenes hard blacklist): no skull, skeleton, or "
    "death imagery; no cigarettes, alcohol, drugs, or vices; no slumped or "
    "defeated posture; no violence or gore; no crowds or extra figures of "
    "any kind (a busy street/market as an anonymous BACKDROP is fine -- the "
    "wolf must remain the only clearly-rendered figure); no floating or "
    "decorative typography (short readable words ON a prop are allowed per "
    "the action rule above)."
)


def _client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (check your .env file)")
    return Anthropic(api_key=api_key)


def _strip_to_json(text: str) -> str:
    """Strip markdown fences / surrounding prose to isolate the JSON object."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start != -1:
        try:
            obj, end = json.JSONDecoder().raw_decode(text, start)
            return json.dumps(obj)
        except json.JSONDecodeError:
            last = text.rfind("}")
            if last > start:
                text = text[start : last + 1]
    return text


_SCENE_TEXT_KEYS = ("concept", "action", "setting", "camera")


def _normalize_image_scenes(data: dict) -> None:
    """Coerce ``data['image_scenes']`` to EXACTLY ``config.IMAGE_PROMPT_COUNT``
    usable scene objects (in place) and stamp the fixed beat sequence. Ported
    unchanged from the old ai_extract.py."""
    target = config.IMAGE_PROMPT_COUNT
    cleaned: list[dict] = []
    for item in data.get("image_scenes") or []:
        if not isinstance(item, dict):
            continue
        scene = {k: str(item.get(k) or "").strip() for k in _SCENE_TEXT_KEYS}
        if not (scene["concept"] and scene["action"] and scene["setting"]):
            continue
        scene["camera"] = scene["camera"] or "dynamic three-quarter view with strong depth"
        scene["beat"] = str(item.get("beat") or "").strip().lower()
        cleaned.append(scene)

    if len(cleaned) < target:
        raise ValueError(
            f"'image_scenes' must contain {target} usable scene objects "
            f"(concept/action/setting all non-empty), got {len(cleaned)}"
        )
    if len(cleaned) > target:
        logger.warning("image_scenes count %d > %d; truncating extras", len(cleaned), target)
        cleaned = cleaned[:target]

    for i, scene in enumerate(cleaned):
        expected = config.IMAGE_SCENE_BEATS[i]
        if scene["beat"] != expected:
            logger.warning(
                "image_scenes[%d] beat %r != expected %r; correcting", i, scene["beat"], expected
            )
            scene["beat"] = expected

    data["image_scenes"] = cleaned

    outfit = str(data.get("wolf_outfit") or "").strip()
    if not outfit:
        logger.warning("wolf_outfit missing/empty; image_gen will use its default outfit")
    data["wolf_outfit"] = outfit


_BANNED_SCENE_WORDS = re.compile(
    r"\b("
    r"crowd|crowds|congregation|gathering|protest|march|rally|parade|festival|"
    r"audience|stadium|conference|classroom|students|congregants|"
    r"couple|couples|duo|pair|partners|team|"
    r"group|groups|friends|family|families|"
    r"woman|women|female|girl|girls|wife|girlfriend|mother|sister|daughter|"
    r"she|her|"
    r"together|each other"
    r")\b",
    re.IGNORECASE,
)
_BANNED_SCENE_PHRASES = (
    "two men", "two women", "two people", "3 men", "three men", "group of",
    "several people", "many people", "people gathering", "people passing",
    "crowd passing", "crowd rushing",
)


def _scene_safety_gate(data: dict) -> None:
    """Raise ValueError if any generated scene violates the single-subject /
    no-crowd rule. Ported unchanged from the old ai_extract.py."""
    offenders: list[str] = []

    def _check(text: str) -> None:
        low = text.lower()
        if _BANNED_SCENE_WORDS.search(low) or any(p in low for p in _BANNED_SCENE_PHRASES):
            offenders.append(text)

    for scene in data.get("image_scenes", []):
        if isinstance(scene, dict):
            _check(str(scene.get("action") or ""))
            _check(str(scene.get("setting") or ""))

    if offenders:
        raise ValueError(
            "SCENE SAFETY GATE -- one or more image scenes describe a banned "
            f"subject (crowd/group/multi-person/female figure): {offenders!r}. "
            "Every scene must depict the wolf character alone."
        )


def _validate(data: dict) -> None:
    """Raise ValueError if ``data`` doesn't match the required schema."""
    required = {
        "topic_cluster": str,
        "hook": str,
        "script": str,
        "insights": list,
        "key_line": str,
        "title": str,
        "hashtags": list,
        "wolf_outfit": str,
        "image_scenes": list,
    }
    for key, expected_type in required.items():
        if key not in data:
            raise ValueError(f"Missing required key: {key!r}")
        if not isinstance(data[key], expected_type):
            raise ValueError(
                f"Key {key!r} has wrong type: expected {expected_type}, "
                f"got {type(data[key]).__name__}"
            )

    if data["topic_cluster"] not in config.TOPIC_CLUSTERS:
        raise ValueError(
            f"'topic_cluster' {data['topic_cluster']!r} not in {config.TOPIC_CLUSTERS}"
        )

    if len(data["insights"]) != 3:
        raise ValueError(f"'insights' must have exactly 3 items, got {len(data['insights'])}")

    if not data["script"].strip():
        raise ValueError("'script' must be non-empty spoken narration")

    _normalize_image_scenes(data)
    _scene_safety_gate(data)


def _brand_gate(data: dict) -> None:
    """Raise ValueError if the generated script fails the brand identity gate.
    Ported unchanged from the old ai_extract.py's hook/insight identity-frame
    checks (the transcript-content-gate half has no analog here -- this
    module fully controls its own output in one shot, so there's no separate
    source text to re-verify against)."""
    hook = data.get("hook", "")
    insights = data.get("insights", [])
    hook_lower = hook.lower()

    identity_signals = ["you", "your", "we ", "our ", "stop ", "every ", "nobody "]
    if not any(sig in hook_lower for sig in identity_signals):
        raise ValueError(
            f"BRAND GATE -- hook fails identity-frame check (no viewer address or "
            f"imperative): {hook!r}. Must contain 'you/your' or a contrarian imperative."
        )

    _VIEWER_SIGNALS = ("you", "your", " me", " my", " i ")
    _IMPERATIVES = ("move ", "stop ", "choose ", "act ", "be ", "start ", "ask ", "drop ")

    def _is_viewer_addressed(ins: str) -> bool:
        low = ins.lower()
        if any(sig in low for sig in _VIEWER_SIGNALS):
            return True
        if any(low.startswith(imp) or low.startswith(imp.strip() + ",") for imp in _IMPERATIVES):
            return True
        return False

    second_person_count = sum(1 for ins in insights if _is_viewer_addressed(ins))
    if second_person_count < 2:
        raise ValueError(
            f"BRAND GATE -- only {second_person_count}/3 insights are viewer-addressed "
            f"(need 'you/your', 'me/my', or an imperative opening). Insights: {insights}"
        )

    no_agency_phrases = [
        "nobody apologized", "nobody told", "they never told", "lied to",
        "the scandal", "exposed the", "they hid", "covered up",
    ]
    if any(phrase in hook_lower for phrase in no_agency_phrases):
        raise ValueError(
            f"BRAND GATE -- hook is a pure diagnosis/scandal frame with no viewer "
            f"agency: {hook!r}."
        )

    logger.info(
        "Brand gate PASSED -- hook identity: yes, 2nd-person insights: %d/3 | hook=%r",
        second_person_count, hook,
    )


_METAPHOR_HOOK_RE = re.compile(
    r"\bis a\b|\bis an\b|\bis like\b|\bacts? like\b|\bworks? like\b|\bis basically\b",
    re.IGNORECASE,
)


def is_metaphor_hook(hook: str) -> bool:
    """Best-effort flag for analogy-style hooks ('Your fear is a GPS')."""
    return bool(_METAPHOR_HOOK_RE.search(hook or ""))


def _history_context(recent_entries: list[dict]) -> str:
    if not recent_entries:
        return "No prior scripts yet -- pick freely, favoring the top 3 clusters."
    lines = [
        f"- [{e.get('topic_cluster')}] {e.get('title')!r} (hook: {e.get('hook')!r})"
        for e in recent_entries
    ]
    return (
        "Recently used topics, most recent LAST (do not repeat the last "
        "entry's topic_cluster; never reuse a hook/title close to any of "
        "these):\n" + "\n".join(lines)
    )


def generate_script(recent_history: list[dict] | None = None) -> dict:
    """Pick a topic and write the full script + copy + art-direction package
    in one Claude call. Returns a dict with exactly: topic_cluster, hook,
    script, insights, key_line, title, hashtags, wolf_outfit, image_scenes,
    clip_start, clip_end (the last two are a fixed sentinel -- video_gen
    clamps clip_end to the real audio duration at render time regardless)."""
    body = (
        f"Available topic clusters: {json.dumps(config.TOPIC_CLUSTERS)}\n\n"
        f"{_history_context(recent_history or [])}\n\n"
        "Write a new script now."
    )

    logger.info("Generating script via %s", config.EXTRACT_MODEL)
    client = _client()

    response = client.messages.create(
        model=config.EXTRACT_MODEL,
        max_tokens=config.EXTRACT_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": body}],
    )

    raw = next((b.text for b in response.content if b.type == "text"), "")
    parsed = json.loads(_strip_to_json(raw))

    parsed["clip_start"] = 0.0
    parsed["clip_end"] = 9999.0

    _validate(parsed)
    _brand_gate(parsed)

    logger.info("Generated script | topic_cluster=%r title=%r", parsed.get("topic_cluster"), parsed.get("title"))
    return parsed


_RETRY_SLEEP_S = 65  # sleep between attempts to clear the 1-min rate-limit window


def generate_script_with_retry(attempts: int = 3) -> dict:
    """Call :func:`generate_script` up to ``attempts`` times, tolerating the
    model's non-deterministic output. Re-fetches recent history each attempt
    (cheap, local) so a slow retry loop never works off a stale dedup view.
    Only ``ValueError`` and ``anthropic.RateLimitError`` are caught; other
    transport/API errors propagate immediately."""
    from modules import script_history

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return generate_script(script_history.recent(8))
        except ValueError as exc:
            last_exc = exc
            logger.warning(
                "generate_script attempt %d/%d failed (non-deterministic): %s",
                attempt, attempts, exc,
            )
        except anthropic.RateLimitError as exc:
            last_exc = exc
            logger.warning(
                "generate_script attempt %d/%d hit rate limit: %s",
                attempt, attempts, exc,
            )
        if attempt < attempts:
            logger.info("Sleeping %ds before retry %d/%d ...", _RETRY_SLEEP_S, attempt + 1, attempts)
            time.sleep(_RETRY_SLEEP_S)
    assert last_exc is not None
    raise last_exc


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    result = generate_script_with_retry()

    print("\n=== Generated script ===")
    print(json.dumps(result, indent=2, ensure_ascii=False).encode("ascii", "replace").decode("ascii"))
    flag = "  [!] METAPHOR HOOK" if is_metaphor_hook(result["hook"]) else ""
    print(f"\nTopic cluster: {result['topic_cluster']}")
    print(f"Hook: {result['hook']!r}{flag}")
    print(f"Script ({len(result['script'].split())} words): {result['script']}")
    print(f"Image scenes: {len(result['image_scenes'])}")
```

- [ ] **Step 2: Run the harness (real Anthropic API call)**

Run: `venv\Scripts\python.exe -m modules.script_gen`

Expected: prints "Brand gate PASSED" in the log, then the full JSON, then a summary. Verify manually: `topic_cluster` is one of the 5 taxonomy values, `hook` is under 15 words and uses a "you/your" contrarian frame, `image_scenes` has exactly 6 entries with beats `["problem","problem","stakes","reframe","payoff","payoff"]`, `script` is a single continuous paragraph (no bracketed stage directions).

- [ ] **Step 3: Verify schema-failure retry behavior tolerates transient errors**

This is a non-deterministic model call, so there's no deterministic failing-test step here (unlike a pure-code unit). Instead, re-run the harness 2-3 times in a row:

Run: `venv\Scripts\python.exe -m modules.script_gen` (repeat 2-3x)

Expected: each run either succeeds outright, or (if you see a "attempt N/3 failed" warning in the log) still ends in a successful JSON print — confirming the retry wrapper recovers from an occasional malformed response the way `extract_copy_with_retry` used to.

- [ ] **Step 4: Commit**

```bash
git add modules/script_gen.py
git commit -m "Add script_gen: single-call topic pick + script + art-direction, replaces ai_extract's two-stage extraction"
```

---

### Task 4: Detach `video_gen.py` from `ai_extract.py`

**Files:**
- Modify: `modules/video_gen.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_ends_sentence(word: str) -> bool` now defined locally in `video_gen.py` instead of imported — no signature change, so `build_video`'s internal call site is untouched.

- [ ] **Step 1: Inline `_ends_sentence`**

In `modules/video_gen.py`, replace line 31:

```python
from modules.ai_extract import _ends_sentence
```

with a local definition (place it near the top, after the existing module-level constants around line 37, before `_bold_font_path`):

```python
_SENTENCE_END = (".", "!", "?")


def _ends_sentence(word: str) -> bool:
    """True if a word token ends a sentence (terminal punctuation, ignoring
    any trailing quote/bracket characters). Was imported from ai_extract.py
    (deleted); it's a pure text utility with a single caller here now."""
    return word.rstrip("\"')]}").endswith(_SENTENCE_END)
```

- [ ] **Step 2: Rewrite the `__main__` harness to require an existing plan cache**

The old harness (lines ~617-661) fell back to running `ai_extract.find_candidates`/`filter_candidates`/`extract_copy_with_retry` from scratch when no `.plan.json` existed next to the newest `tmp/*.mp3`. That fallback no longer has anywhere to call — script generation now happens in `main.py`/`script_gen.py`, not from an existing episode transcript. Replace lines 617-661 (from `mp3s = sorted(...)` through the end of the `if/else` cache block, i.e. up to but not including the `# Re-run JUST the extraction...` `has_images` block) with:

```python
    mp3s = sorted(glob.glob(os.path.join(config.TMP_DIR, "*.mp3")), key=os.path.getmtime, reverse=True)
    if not mp3s:
        raise SystemExit(f"No MP3 found in {config.TMP_DIR} - run main.py first to generate a script + voiceover.")
    audio_path = mp3s[0]

    cache_path = os.path.splitext(audio_path)[0] + ".plan.json"
    if not os.path.exists(cache_path):
        raise SystemExit(
            f"No plan cache found for {os.path.basename(audio_path)} "
            f"(expected {os.path.basename(cache_path)}). This harness only "
            "re-renders an existing script+audio pair -- run "
            "`main.py --render <slug> <audio_path>` to generate one."
        )
    print(f"Loading cached script + plan: {os.path.basename(cache_path)}", flush=True)
    with open(cache_path, encoding="utf-8") as f:
        cached = json.load(f)
    transcript, highlights = cached["transcript"], cached["highlights"]
```

Then delete the now-obsolete `has_images`/stale-cache-regeneration block that follows it (the `# Re-run JUST the extraction (no Groq) if the cached plan predates...` through the `with open(cache_path, "w"...) as f: json.dump(...)` a few lines later) — there's no more `ai_extract.extract_copy_with_retry` to call, and every plan this harness will ever see from now on already carries `image_scenes` (written by the new `script_gen.py`).

- [ ] **Step 3: Verify the module still imports and the harness fails cleanly with no cache**

Run: `venv\Scripts\python.exe -c "from modules import video_gen; print('import OK')"`

Expected: `import OK`, no `ImportError`.

Run: `venv\Scripts\python.exe -m modules.video_gen` (with no `tmp/*.plan.json` present for the newest mp3 — if `tmp/script_rumination_test.mp3` already has no matching `.plan.json` yet at this point in the migration, this exercises the new error path directly)

Expected: `SystemExit` with the "No plan cache found ... run main.py --render" message — confirms the removed fallback path is gone and the new guard fires instead of crashing on a missing `ai_extract` import.

- [ ] **Step 4: Commit**

```bash
git add modules/video_gen.py
git commit -m "video_gen: inline _ends_sentence, drop ai_extract dependency and its harness fallback"
```

---

### Task 5: Rename `best_quote` to `key_line` in `slide_gen.py`

**Files:**
- Modify: `modules/slide_gen.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `build_slides(highlights, ...)` now reads `highlights["key_line"]` instead of `highlights["best_quote"]` — `main.py` (Task 6) and `script_gen.py` (Task 3, already emits `key_line`) must agree on this key name.

- [ ] **Step 1: Update the docstring, the read, and the error message**

In `modules/slide_gen.py`, line 390:
```python
    Order: COVER (hook), INSIGHT 01-03 (insights), QUOTE (best_quote), FOLLOW (CTA).
    Requires keys: hook, insights (>=1, uses up to 3), best_quote.
```
becomes:
```python
    Order: COVER (hook), INSIGHT 01-03 (insights), QUOTE (key_line), FOLLOW (CTA).
    Requires keys: hook, insights (>=1, uses up to 3), key_line.
```

Lines 404-406:
```python
    quote = (highlights.get("best_quote") or "").strip()
    if not hook or not insights or not quote:
        raise ValueError("highlights needs hook, insights, and best_quote to build the deck")
```
becomes:
```python
    quote = (highlights.get("key_line") or "").strip()
    if not hook or not insights or not quote:
        raise ValueError("highlights needs hook, insights, and key_line to build the deck")
```

- [ ] **Step 2: Update `SAMPLE_HIGHLIGHTS`**

Line 444:
```python
    "best_quote": "In a world that's addicted to noise, being peaceful becomes your superpower.",
```
becomes:
```python
    "key_line": "In a world that's addicted to noise, being peaceful becomes your superpower.",
```

- [ ] **Step 3: Run the harness (no API calls, uses SAMPLE_HIGHLIGHTS)**

Run: `venv\Scripts\python.exe -m modules.slide_gen`

Expected: `Built 6 slides in ...` log line, no `ValueError`, 6 PNGs written under `output/slides/`.

- [ ] **Step 4: Commit**

```bash
git add modules/slide_gen.py
git commit -m "slide_gen: rename best_quote to key_line (nothing is quoted from a real speaker anymore)"
```

---

### Task 6: Rewrite `main.py` as a two-command pipeline

**Files:**
- Modify: `main.py`

**Interfaces:**
- Consumes: `modules.script_gen.generate_script_with_retry()`, `modules.script_history.record()`, `modules.transcribe.transcribe()`, `modules.background.select_backgrounds()`, `modules.video_gen.build_video()`, `modules.slide_gen.build_slides()`, `config.TMP_DIR`, `config.BRAND_NAME`.
- Produces: `tmp/<slug>.script.txt`, `tmp/<slug>.plan.json`, plus (on `--render`) the same video/slide outputs the old pipeline produced.

- [ ] **Step 1: Replace the whole file**

```python
"""Synthetic-script video pipeline orchestrator.

Two-command flow, split around a manual ElevenLabs voiceover step:

    main.py
        Pick a topic, write the full script + copy + art-direction package
        (modules.script_gen), save the narration text for you to paste into
        ElevenLabs, cache the package, log it to the dedup ledger.

    main.py --render <slug> <audio_path>
        Take the ElevenLabs audio you downloaded, transcribe it (Groq
        Whisper -- for word-level caption timestamps only, not content),
        generate the 6 wolf background images, render the karaoke MP4, then
        the 6-slide carousel.

Publishing is fully MANUAL by design: the run ends with a manual-post
checklist of local file paths -- no upload APIs.
"""

import json
import logging
import os
import sys

from dotenv import load_dotenv

import config
from modules import background, script_gen, script_history, slide_gen, transcribe, video_gen

load_dotenv()

# Episode/script titles often contain non-ASCII (curly quotes, em dashes); the
# Windows console defaults to cp1252 and would crash on them. Force UTF-8 on
# the console streams so logging/printing a title never takes down the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

os.makedirs(config.LOGS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("pipeline")


def _slugify(text: str) -> str:
    """Filesystem-safe slug from a title, e.g. 'Your Brain Replays That!' ->
    'your_brain_replays_that'. Same scheme video_gen already uses for output
    filenames, reused here as the cache key so both stay in sync by eye."""
    slug = "".join(c if c.isalnum() else "_" for c in text.lower())
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")[:80] or "script"


def _script_txt_path(slug: str) -> str:
    return os.path.join(config.TMP_DIR, f"{slug}.script.txt")


def _plan_cache_path(slug: str) -> str:
    return os.path.join(config.TMP_DIR, f"{slug}.plan.json")


def generate() -> str:
    """Step 1: pick a topic, write the package, cache it, log it. Returns the
    slug so the caller can print the exact --render command."""
    logger.info("[1/2] Generating script (Claude %s)", config.EXTRACT_MODEL)
    highlights = script_gen.generate_script_with_retry()

    slug = _slugify(highlights["title"])
    os.makedirs(config.TMP_DIR, exist_ok=True)

    script_path = _script_txt_path(slug)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(highlights["script"] + "\n")
    logger.info("Wrote narration script: %s", script_path)

    plan_path = _plan_cache_path(slug)
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump({"highlights": highlights}, f)
    logger.info("Cached plan: %s", plan_path)

    script_history.record(
        topic_cluster=highlights["topic_cluster"],
        hook=highlights["hook"],
        title=highlights["title"],
    )

    print("\n" + "=" * 64)
    print("SCRIPT READY")
    print("=" * 64)
    print(f"Title      : {highlights['title']}")
    print(f"Topic      : {highlights['topic_cluster']}")
    print(f"Hook       : {highlights['hook']}")
    print(f"Script file: {script_path}")
    print("\nNext steps:")
    print(f"  1. Paste the contents of {script_path} into ElevenLabs.")
    print("  2. Download the generated audio.")
    print(f"  3. Run: python main.py --render {slug} <path-to-audio-file>")
    print("=" * 64, flush=True)
    return slug


def _log_manual_post(video_path: str, slides: list[str]) -> None:
    lines = [
        "MANUAL POST (all platforms, by hand):",
        f"  Short/Reel video : {video_path}",
        f"  Carousel slides ({len(slides)}):",
    ]
    lines += [f"    {i}. {p}" for i, p in enumerate(slides, 1)]
    logger.info("\n".join(lines))


def render(slug: str, audio_path: str, render_slides: bool = True) -> dict:
    """Step 2: transcribe the manually-supplied audio, generate images, render
    video + slides."""
    plan_path = _plan_cache_path(slug)
    if not os.path.exists(plan_path):
        raise FileNotFoundError(
            f"No cached plan for slug {slug!r} (expected {plan_path}). "
            "Run `python main.py` first to generate a script."
        )
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    with open(plan_path, encoding="utf-8") as f:
        highlights = json.load(f)["highlights"]

    logger.info("[2/2] Transcribing voiceover for word timestamps (Groq Whisper): %s", audio_path)
    transcript = transcribe.transcribe(audio_path)

    logger.info("Backgrounds: select (gpt-image-2 -> gradient)")
    backgrounds = background.select_backgrounds(highlights, basename=slug)
    logger.info("Backgrounds: %d file(s)", len(backgrounds))

    logger.info("Video: render karaoke MP4")
    video_path = video_gen.build_video(
        audio_path,
        transcript["words"],
        highlights,
        podcast_name=config.BRAND_NAME,
        background_images=backgrounds,
    )

    if render_slides:
        logger.info("Slides: render deck (branded on the clip's wolf images)")
        slides = slide_gen.build_slides(highlights, photo_paths=backgrounds)
    else:
        logger.info("Slides: skipped (--no-slides)")
        slides = []

    _log_manual_post(video_path, slides)
    logger.info("Pipeline complete for: %s", highlights.get("title"))
    return {"highlights": highlights, "video_path": video_path, "slides": slides}


def _print_summary(result: dict) -> None:
    h = result["highlights"]
    line = "=" * 64
    print("\n" + line)
    print("PIPELINE SUMMARY")
    print(line)
    print(f"Title       : {h.get('title')}")
    print(f"Topic       : {h.get('topic_cluster')}")
    print(f"Video MP4   : {result['video_path']}")
    print(f"Slides ({len(result['slides'])})  :")
    for p in result["slides"]:
        print(f"              - {p}")
    print(line)
    print("MANUAL POST (all platforms, by hand):")
    print(f"  Short/Reel video : {result['video_path']}")
    print("  Carousel slides  :")
    for i, p in enumerate(result["slides"], 1):
        print(f"    {i}. {p}")
    print(line, flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Synthetic-script video pipeline")
    parser.add_argument(
        "--render",
        nargs=2,
        metavar=("SLUG", "AUDIO_PATH"),
        help="Render step 2: SLUG from a prior `main.py` run, AUDIO_PATH to the "
        "ElevenLabs voiceover you downloaded for it.",
    )
    parser.add_argument(
        "--no-slides",
        action="store_true",
        help="With --render, skip the slide-carousel render; produce the karaoke video only.",
    )
    args = parser.parse_args()

    try:
        if args.render:
            slug, audio_path = args.render
            result = render(slug, audio_path, render_slides=not args.no_slides)
            _print_summary(result)
        else:
            generate()
    except Exception:
        logger.exception("Pipeline FAILED")
        sys.exit(1)
```

- [ ] **Step 2: Verify step 1 end to end (real Anthropic API call)**

Run: `venv\Scripts\python.exe main.py`

Expected: logs "[1/2] Generating script...", then "SCRIPT READY" block printed with a title/topic/hook, and prints the exact `--render <slug> <audio_path>` command to run next. Verify the files exist:

Run: `venv\Scripts\python.exe -c "import glob; print(glob.glob('tmp/*.script.txt')); print(glob.glob('tmp/*.plan.json'))"`

Expected: at least one `<slug>.script.txt` and matching `<slug>.plan.json` listed.

Run: `venv\Scripts\python.exe -m modules.script_history`

Expected: `Entries: 1`, showing the just-generated topic.

- [ ] **Step 3: Verify step 2 end to end using known-good fixture data**

There's no real new ElevenLabs audio available for the script Step 2 just generated (that requires you to actually paste it into ElevenLabs by hand). Instead, verify the `--render` code path itself using the already-verified-good rumination script + audio + images from the 2026-07-31 manual test, repackaged into the new key-name schema so it exercises the exact same code `--render` will run for real content:

Run this one-off Python snippet to write a matching `tmp/rumination_test.plan.json` fixture (reusing the already-generated `tmp/script_rumination_test_bg_1..6.png` images and `tmp/script_rumination_test.mp3` audio from the earlier manual test, so `--render` finds cache hits for the images and doesn't re-spend an OpenAI call):

```python
# scratch snippet, run via: venv\Scripts\python.exe -c "<paste this>"
import json, os
highlights = {
    "topic_cluster": "fear_anxiety_rumination",
    "hook": "Your brain replays that argument at 2am for one reason",
    "script": "Your brain replays that argument at 2am for one reason: it thinks the threat is still in the room. Rumination isn't a personality flaw...",
    "insights": ["Your brain won't let go until it gets the ending it never had", "You're not broken, you're over-protected", "Naming it out loud is the signal your brain is waiting for"],
    "key_line": "The loop isn't punishment. It's protection that forgot to stop.",
    "title": "brain replays the argument",
    "hashtags": ["#Mindset", "#Anxiety", "#SelfAwareness"],
    "wolf_outfit": "a rumpled grey henley shirt with sleeves pushed up, plain dark sleep pants",
    "image_scenes": [
        {"beat": "problem", "concept": "an unresolved conflict feels like present danger", "action": "sitting up in bed gripping the sheets, staring at a glowing phone screen showing a text bubble reading 'I DIDN'T MEAN IT LIKE THAT'", "setting": "dark bedroom, moonlight slicing through blinds", "camera": "tight close-up on the face lit by phone glow"},
        {"beat": "problem", "concept": "the mind replays the same moment on a loop", "action": "pacing in a tight circle across the bedroom floor, hands laced behind the head, the same speech bubble trailing behind like a racetrack", "setting": "bedroom floor, long dramatic shadows", "camera": "wide shot showing the circular pacing path"},
        {"beat": "stakes", "concept": "the brain treats the unresolved moment like an active threat", "action": "standing rigid at the window like a sentry on watch, one hand pressed flat to the glass", "setting": "dark bedroom, distant city lights outside the window", "camera": "side silhouette profile against the window light"},
        {"beat": "reframe", "concept": "the loop is protection, not punishment", "action": "sitting on the edge of the bed holding a small hand-lettered card reading 'PROTECTION NOT PUNISHMENT', looking down at it", "setting": "bedroom with the lamp now switched on, warm light", "camera": "medium shot, lamp glow across the face"},
        {"beat": "payoff", "concept": "naming it out loud gives the brain the ending it needs", "action": "standing tall in the middle of the room, hand on the chest, a small speech bubble reading 'I'M SAFE NOW'", "setting": "same bedroom, pale dawn light now coming through the window", "camera": "front-facing three-quarter shot, confident stance"},
        {"beat": "payoff", "concept": "the loop finally closes and the mind rests", "action": "lying back in bed relaxed, one arm behind the head, faint content smile, phone lying face-down on the nightstand", "setting": "bedroom in soft full morning light", "camera": "gentle three-quarter angle"},
    ],
    "clip_start": 0.0,
    "clip_end": 9999.0,
}
os.makedirs("tmp", exist_ok=True)
with open("tmp/rumination_fixture.plan.json", "w", encoding="utf-8") as f:
    json.dump({"highlights": highlights}, f)
print("wrote tmp/rumination_fixture.plan.json")
```

Then copy the existing images so `background.select_backgrounds` cache-hits instead of re-generating:

Run (PowerShell): `Copy-Item tmp\script_rumination_test_bg_1.png tmp\rumination_fixture_bg_1.png; Copy-Item tmp\script_rumination_test_bg_2.png tmp\rumination_fixture_bg_2.png; Copy-Item tmp\script_rumination_test_bg_3.png tmp\rumination_fixture_bg_3.png; Copy-Item tmp\script_rumination_test_bg_4.png tmp\rumination_fixture_bg_4.png; Copy-Item tmp\script_rumination_test_bg_5.png tmp\rumination_fixture_bg_5.png; Copy-Item tmp\script_rumination_test_bg_6.png tmp\rumination_fixture_bg_6.png`

Run: `venv\Scripts\python.exe main.py --render rumination_fixture tmp/script_rumination_test.mp3`

Expected: logs "Backgrounds cached, skipping generation" x6 (no OpenAI spend), a video renders to `output/videos/brain_replays_the_argument.mp4` (or similar, from the fixture's title), 6 slides render to `output/slides/`, PIPELINE SUMMARY prints with a MANUAL POST checklist. No exceptions.

- [ ] **Step 4: Clean up the fixture artifacts**

Run: `venv\Scripts\python.exe -c "import os; os.remove('tmp/rumination_fixture.plan.json'); [os.remove(f'tmp/rumination_fixture_bg_{i}.png') for i in range(1,7)]"`

(Keep the real `<slug>.script.txt`/`<slug>.plan.json` from Step 2 and the `script_history.json` entry — those are genuine first output, not test scaffolding.)

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "main.py: rewrite as two-command flow (generate script, render against manual ElevenLabs audio)"
```

---

### Task 7: Delete the podcast-sourcing modules and dead test

**Files:**
- Delete: `modules/rss_ingest.py`, `modules/ai_extract.py`, `modules/candidate_bank.py`, `modules/posted_history.py`, `scripts/test_trim_to_cap.py`

**Interfaces:**
- Consumes: nothing (this task only removes files already fully de-referenced by Tasks 1-6).
- Produces: nothing new.

- [ ] **Step 1: Delete the files**

```bash
git rm modules/rss_ingest.py modules/ai_extract.py modules/candidate_bank.py modules/posted_history.py scripts/test_trim_to_cap.py
```

(`scripts/test_trim_to_cap.py` tested `ai_extract._trim_to_cap`'s payoff-preserving sentence-boundary trim — logic with no analog in the new pipeline, since there's no transcript window to snap; `video_gen` already clamps to the real TTS audio duration instead.)

- [ ] **Step 2: Grep-verify nothing live still references the deleted modules**

Run: `git grep -n -E "rss_ingest|ai_extract|candidate_bank|posted_history" -- '*.py' ':!docs/*'`

Expected: zero matches. (Historical files under `docs/superpowers/plans/` and `docs/superpowers/specs/` are excluded by the pathspec and are left as-is — they're dated records of past decisions, same as this repo's established practice for prior deletions.)

- [ ] **Step 3: Verify the full live import surface still resolves**

Run: `venv\Scripts\python.exe -c "import main; import config; from modules import script_gen, script_history, transcribe, image_gen, background, video_gen, slide_gen; print('all imports OK')"`

Expected: `all imports OK`, no `ModuleNotFoundError`/`ImportError`.

- [ ] **Step 4: Commit**

```bash
git commit -m "Remove podcast-sourcing modules (rss_ingest, ai_extract, candidate_bank, posted_history) and their dead test"
```

(The `git rm` in Step 1 already staged the deletions — this commits them.)

---

### Task 8: Update `CLAUDE.md` for the new architecture

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: nothing (documentation only).
- Produces: nothing (documentation only) — this task has no code interface; its "test" is a self-review readthrough plus a grep check.

- [ ] **Step 1: Rewrite the "What this is" and "Commands" sections**

Replace the opening description and the `Commands` section's example invocations to describe the two-command flow (`main.py` / `main.py --render <slug> <audio_path>`) instead of RSS feed args, `--scan`, `--bank`. Drop the `--scan`/`--bank`/candidate-bank command examples entirely. Keep the per-module `__main__` harness table, updating it to list `script_gen`/`script_history` in place of `ai_extract`/`rss_ingest`/`candidate_bank`/`posted_history`.

- [ ] **Step 2: Rewrite the "Architecture" section**

Replace the "Linear pipeline, config-driven" numbered flow and the entire "Candidate bank" subsection with a description of the new two-command flow (mirroring this plan's own Task 6 design). Update "Data contracts" to describe `script_gen.generate_script()`'s output schema (including the `key_line` rename and the `clip_start=0.0`/`clip_end=9999.0` sentinel convention) in place of `ai_extract`'s two-stage `find_candidates`/`extract_copy_for_window` contracts. Update "Caching to avoid burning API credits" to describe `tmp/<slug>.script.txt` / `tmp/<slug>.plan.json` / `tmp/script_history.json` in place of `tmp/<basename>.plan.json` / `tmp/candidate_bank.json` / `tmp/posted_history.json`.

- [ ] **Step 3: Update "External services & secrets" and "Gotchas"**

Confirm the 3-key secrets list still reads correctly (no change needed — `GROQ_API_KEY` is still required, now documented as "timestamps the ElevenLabs audio" rather than "transcribes the podcast episode"). Remove or rewrite gotcha entries that only make sense for the deleted RSS/extraction path (e.g. anything about `PODCAST_FEEDS`, episode dedup via GUID, the two-stage extraction's non-determinism specifics) — keep the ones that still apply verbatim (background pillarboxing fix, image cache namespacing, monotonic word timestamps, Windows fonts).

- [ ] **Step 4: Update "What's still TODO"**

Replace the "First real branded render via `main.py --bank`" line (now done — this migration's own end-to-end test in Task 6 Step 3 IS that first real branded render, using verified-good content) and drop any TODO that only applied to the candidate-bank/RSS workflow.

- [ ] **Step 5: Self-review grep check**

Run: `git grep -n -E "rss_ingest|ai_extract|candidate_bank|posted_history|PODCAST_FEEDS|--scan|--bank" CLAUDE.md`

Expected: zero matches (or only matches inside an explicit "see git history" historical note you intentionally left, if any — read each hit and confirm it's not describing current behavior as if it still exists).

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "CLAUDE.md: document the synthetic-script pipeline, remove stale podcast/candidate-bank references"
```
