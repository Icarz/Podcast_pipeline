# Candidate Shortlist + Human Pick Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single blind Claude clip-extraction call with a two-stage
flow — Stage 1 surfaces up to 5 ranked candidate clips, a human picks one (or
rejects all and the loop moves to the next episode), Stage 2 writes the full
copy/art-direction package only for the approved window.

**Architecture:** Split `modules/ai_extract.py`'s single `SYSTEM_PROMPT` /
`extract_highlights()` into `CANDIDATE_SYSTEM_PROMPT` / `find_candidates()` +
`filter_candidates()` (Stage 1, no copywriting) and `COPY_SYSTEM_PROMPT` /
`extract_copy_for_window()` (Stage 2, copy only, window already fixed).
`main.py` gains an interactive episode/candidate picker
(`_pick_episode_and_candidate`) that `run()` calls instead of
`pick_random_entry` + the old `_load_or_build_plan`. `--auto` reuses the same
picker with `interactive=False` (auto-picks the top survivor, no `input()`).

**Tech Stack:** Python 3.12, `anthropic` SDK (Claude Sonnet), existing
`modules/ai_extract.py` helpers (`_snap_to_sentences`, `_extend_to_floor`,
`_trim_to_cap`, `_content_gate`, `_validate`, `_brand_gate` — all reused
unchanged).

## Global Constraints

- Full spec: `docs/superpowers/specs/2026-07-09-candidate-shortlist-human-pick-design.md` (Status: Approved). Follow it exactly; this plan only adds file/line-level detail.
- **No pytest suite in this repo** (see `CLAUDE.md`) — the per-module `__main__` harness is the only test. Verification steps in this plan are therefore: (a) `.\venv\Scripts\python.exe -m py_compile <file>` for syntax, (b) a Python import smoke-check for wiring, and (c) manual trace-through against the spec. Do NOT invent a pytest test file — there isn't one to add to.
- **Do not run the module `__main__` harnesses or a live `main.py` invocation as "verification"** — they call live Groq/Claude/Pexels APIs (real cost), and `main.py`'s new interactive picker calls `input()` (a subagent has no stdin to satisfy). A live end-to-end run is the user's job after this plan lands, not part of task verification.
- Exact values: `CANDIDATE_COUNT = 5` (matches `config.SEARCH_QUERY_COUNT`-style existing constants). Clip window bounds stay `config.CLIP_WINDOW_MIN_SECONDS` (45) / `config.CLIP_WINDOW_MAX_HARD_SECONDS` (58) — unchanged, already defined.
- `extract_highlights` / `extract_highlights_with_retry` are REMOVED entirely (not deprecated, not kept as a shim) — replaced by `find_candidates` / `filter_candidates` / `extract_copy_for_window` / `extract_copy_with_retry`. Every call site must move to the new functions in the same task wave that removes the old ones (no window where the repo doesn't import-clean).
- `_validate`, `_brand_gate`, `_content_gate`, `_snap_to_sentences`, `_extend_to_floor`, `_trim_to_cap`, `_scene_safety_gate`, `_client`, `_strip_to_json`, `_format_segments`, `_normalize_query_list`, `_normalize_video_queries` are reused **as-is, unmodified** — no task in this plan edits their bodies.
- `_validate` already calls `_scene_safety_gate` internally (see `modules/ai_extract.py:702`) — never call `_scene_safety_gate` a second time alongside `_validate`.
- Commit after each task with a message describing the change (this repo has no enforced commit-message format beyond being descriptive — see recent `git log`).

---

### Task 1: `config.py` — add `CANDIDATE_COUNT`

**Files:**
- Modify: `config.py:263-270`

**Interfaces:**
- Produces: `config.CANDIDATE_COUNT` (int) — consumed by Task 2's `CANDIDATE_SYSTEM_PROMPT` and `find_candidates`.

- [ ] **Step 1: Add the constant**

Open `config.py`. Find this existing block (lines 261-270):

```python
# --- AI-generated themed backgrounds (Gemini 2.5 Flash Image / "Nano Banana") ---
IMAGE_MODEL = "gemini-2.5-flash-image"
IMAGE_PROMPT_COUNT = 4               # number of background prompts ai_extract emits
SEARCH_QUERY_COUNT = 5               # one art-directed stock-photo query per carousel slide
VIDEO_QUERY_COUNT = 4                # stock-VIDEO background SLOTS to actually fill
VIDEO_QUERY_SPARE = 1                # extra backup query ai_extract emits as a fallback
# What ai_extract emits: the 4 primary beats PLUS spare backups (5 total). pexels_bg
# fills VIDEO_QUERY_COUNT slots and dips into the spare(s) when a query yields a
# duplicate/empty result, so all 4 slots still get DISTINCT footage.
VIDEO_QUERY_EXTRACT_COUNT = VIDEO_QUERY_COUNT + VIDEO_QUERY_SPARE
```

Immediately **before** this block, insert a new block:

```python
# --- Candidate shortlist (Stage 1 of the two-stage extraction) ---
CANDIDATE_COUNT = 5                  # max ranked clip candidates find_candidates() surfaces

```

(Blank line after, to separate it from the existing "AI-generated themed backgrounds" comment block.)

- [ ] **Step 2: Verify syntax**

Run: `.\venv\Scripts\python.exe -m py_compile config.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Verify the value is importable**

Run: `.\venv\Scripts\python.exe -c "import config; print(config.CANDIDATE_COUNT)"`
Expected: prints `5`.

- [ ] **Step 4: Commit**

```bash
git add config.py
git commit -m "Add CANDIDATE_COUNT config constant for Stage 1 candidate shortlist"
```

---

### Task 2: `modules/ai_extract.py` — Stage 1 (`CANDIDATE_SYSTEM_PROMPT`, `find_candidates`, `filter_candidates`)

**Files:**
- Modify: `modules/ai_extract.py` (insert new code; do NOT touch the existing `SYSTEM_PROMPT` constant, `extract_highlights`, or `extract_highlights_with_retry` in this task — Task 3 removes them after copying what it needs)

**Interfaces:**
- Consumes: `config.CANDIDATE_COUNT` (Task 1), and these existing helpers already defined earlier in the file: `_client()`, `_strip_to_json(text) -> str`, `_format_segments(segments) -> str`, `_snap_to_sentences(data, words) -> None`, `_extend_to_floor(highlights, words, segments) -> dict`, `_trim_to_cap(data, words) -> None`, `_content_gate(data, transcript) -> None` (raises `ValueError` on rejection, fails open on API errors).
- Produces:
  - `CANDIDATE_SYSTEM_PROMPT: str` — consumed by this task's `find_candidates`.
  - `find_candidates(transcript: dict) -> list[dict]` — each dict has keys `clip_start` (float), `clip_end` (float), `hook` (str), `exposes` (str), `reframe` (str), `payoff` (str). Consumed by Task 4's `main.py` picker.
  - `filter_candidates(candidates: list[dict], transcript: dict) -> list[dict]` — same shape, survivors only, original rank order preserved. Consumed by Task 4.

- [ ] **Step 1: Insert `CANDIDATE_SYSTEM_PROMPT`**

Open `modules/ai_extract.py`. Find the line `def _client() -> Anthropic:` (currently line 488, right after the closing `)` of `SYSTEM_PROMPT`). Insert the following new constant **immediately before** that `def _client()` line (i.e., right after `SYSTEM_PROMPT`'s closing `)`, leaving `SYSTEM_PROMPT` itself completely untouched above it):

```python
CANDIDATE_SYSTEM_PROMPT = (
    "You are a podcast clip producer. You read a full episode transcript and "
    "surface a SHORTLIST of the best candidate short-form clips — not a single "
    "pick, and not the copywriting yet. A human will choose which candidate to "
    "develop into a finished clip.\n\n"
    "You MUST respond with ONLY a single valid JSON object and nothing else — "
    "no markdown, no code fences, no commentary before or after.\n\n"
    "BRAND MISSION — READ THIS BEFORE EVERYTHING ELSE:\n"
    "This channel is for people in the gap: they know what they should do, "
    "they've read the books, they're self-aware enough to see their own patterns — "
    "but they still can't make the shift. They keep starting over. They know "
    "better and still don't do better. Every clip must create a MOMENT OF "
    "RECOGNITION ('that's exactly what I do') and then hand them a new frame, "
    "a hidden mechanism, or a realization about how they actually work — not a "
    "pep talk, not a list of tips, not more information. The viewer must leave "
    "feeling: 'I finally understand WHY I do this.' "
    "Every clip must serve at least one of these outcomes for the viewer:\n"
    "  (A) SELF-AWARENESS: they understand their own behavior, mind, or patterns better.\n"
    "  (B) NEW PERSPECTIVE: they see themselves or life through a lens they didn't have before.\n"
    "  (C) HOPE + AGENCY: they leave feeling there is a path forward — not hopeless, not trapped.\n"
    "  (D) SELF-KNOWLEDGE: they learn something true about how humans (and therefore they) work.\n"
    "The content universe is human behavior, neurology, focus, motivation, identity, "
    "resilience, self-improvement, meaning, and money/wealth WHEN reframed as freedom, "
    "power, or identity (never as personal-finance tips or a savings hack — 'the purpose "
    "of money is to get free' is the validated frame; 'how to budget better' is not). "
    "Overthinking is a SIDE TOPIC only — "
    "never the primary theme. A clip that only diagnoses a problem without offering a "
    "new lens, self-awareness, or implied agency FAILS the brand mission and must be "
    "skipped. The viewer must leave with INSIGHT + HOPE, not just awareness of a trap.\n\n"
    "CLIP SELECTION RULES:\n"
    "1. Pick a clip containing a UNIVERSAL INSIGHT or PRINCIPLE — "
    "something true for any listener regardless of who is speaking.\n"
    "2. REJECT clips that are primarily: personal career stories, "
    "event-specific narratives (sold out a venue, got signed, met "
    "someone), name-dropping, entertainment anecdotes, or banter/trivia "
    "with no transferable insight.\n"
    "3. BRAND CHECK: the clip MUST (a) relate to at least one of: "
    "human behavior, neurology, focus, motivation, identity, resilience, "
    "self-awareness, self-knowledge, perspective on life, meaning, "
    "emotional regulation, habit formation, stoicism, self-belief, or "
    "money/wealth reframed as freedom or identity (never personal-finance tips) "
    "as a universal principle; AND (b) serve at least one of the four "
    "BRAND MISSION outcomes above (self-awareness, new perspective, "
    "hope/agency, or self-knowledge). If the best clip fails either "
    "check, pick the next best clip that passes both.\n"
    "4. TOPIC PRIORITY — when multiple clips pass the brand check, "
    "rank them in this order and pick the highest-ranked:\n"
    "   DIGESTIBILITY FIRST (overrides all tiers): Before ranking by topic, apply "
    "this filter — a complete stranger must grasp the core idea in under 3 seconds "
    "with ZERO prior context. The viewer's reaction must be 'yes, that's me' — not "
    "'interesting, let me think about that'. Concepts that require intellectual "
    "assembly, specialist vocabulary, or explanation of the speaker's framework are "
    "ALWAYS ranked below concepts that are immediately obvious once stated. "
    "SIMPLE ≠ SHALLOW: 'Your brain rehearses fake scenarios to feel safe' is simple "
    "AND deep. 'Anxiety and creativity are the same neural force' is interesting but "
    "requires assembly — deprioritize it unless nothing simpler is available. "
    "PROVEN DIGESTIBILITY PATTERN: the best performers ('Your Brain Is Addicted to "
    "Fake Scenarios', 'Opt Out of Modern Culture', 'Always Grab the Right Handle') "
    "all describe something the viewer is ALREADY doing or experiencing — they just "
    "didn't have the frame for it yet.\n"
    "   TIER 1 (pick first): The speaker reveals a psychological, neurological, or "
    "systemic mechanism that is acting on the viewer WITHOUT their awareness — "
    "AND the clip includes or implies a path to self-awareness or agency. "
    "The viewer discovers they are inside a system AND gets a new way to see it. "
    "PROVEN TOP PERFORMERS: "
    "'Your Brain Is Addicted to Fake Scenarios' (873 YT views day-1, 69% retention), "
    "'Your Brain Won't Let Go Until You Face It' (68% retention), "
    "'Opt Out of Modern Culture Before It Breaks You' (621 YT views), "
    "'Always Grab the Right Handle' (948 YT views) — all share this mechanic "
    "AND leave the viewer with a new way to respond. "
    "FEAR / ANXIETY / RUMINATION MECHANISMS are the single strongest recurring "
    "sub-topic within TIER 1 (3 of the last 6 top performers, including 'Your Fear "
    "Is a GPS' and both 'Brain' clips above) — actively favor transcript passages "
    "about how fear, anxiety, or rumination actually work in the body/mind whenever "
    "present, even over other TIER 1 candidates.\n"
    "   TIER 2: A reframe that gives the viewer a completely new lens on their own "
    "behavior or on life — stoic two-handle choices, identity vs. action distinctions, "
    "contrarian principles that shift perspective, individuality-vs-conformity "
    "(the courage to want more than the crowd finds acceptable — see 'You're Killing "
    "Your Dreams Just to Fit In' and 'You're Not Obsessed Enough', both proven "
    "performers), or money/wealth reframed as a vehicle for freedom or identity "
    "(see 'Purpose of Money Is to Get Free' — validated top-2 performer).\n"
    "   TIER 3: Self-knowledge or motivational insight grounded in a universal human truth.\n"
    "   AVOID: clips that only diagnose a trap without a path out; clips about a single "
    "behavioral problem with no self-awareness payoff; inspirational quotes without "
    "a reveal; motivational pep-talk; anything that could headline a self-help listicle; "
    "clips where the insight requires knowing the speaker's theory/framework first.\n"
    "   NEVER select a clip where the primary mechanism or core advice is substance-based "
    "or consumption-based: caffeine, coffee, energy drinks, supplements, nootropics, "
    "cold showers/plunges, sleep hacks, or any biohacking tactic. These produce "
    "lifestyle-hack content, not identity transformation. If the hook would make "
    "someone think of a coffee mug or a pill bottle, reject it.\n"
    "   TOPIC DIVERSITY: if the transcript's central topic is one you've likely used "
    "recently (e.g. overthinking, procrastination), search HARDER for a different "
    "angle in the same transcript — look for clips on identity, meaning, perspective, "
    "resilience, or self-knowledge that are buried deeper in the episode.\n"
    "5. SINGLE SPEAKER — HARD RULE: The selected clip window MUST contain ONE person "
    "speaking uninterrupted. It must sound like a monologue, lecture, or sustained "
    "personal reflection — NOT a conversation. REJECT any window where:\n"
    "   - An interviewer or second voice asks a question (even short fillers like "
    "'right?', 'yeah', 'exactly', 'so tell me', 'what do you mean' from anyone "
    "other than the main speaker disqualify the window).\n"
    "   - The transcript shows back-and-forth rhythm: short sentence → short "
    "response → short sentence → short response.\n"
    "   - Any exchange structure is present, even partial.\n"
    "   If the episode is an interview, scan for sections where the interviewee "
    "speaks without interruption for 45-58 seconds straight. These exist in almost "
    "every interview — find them. A qualifying window should feel like the person "
    "forgot they were being interviewed and just started talking.\n\n"
    "Return a JSON object with exactly one key:\n"
    f'  "candidates" : array of up to {config.CANDIDATE_COUNT} objects, ranked BEST '
    "FIRST (candidates[0] is your top pick). Surface as many DISTINCT, non-overlapping "
    "candidates as the transcript genuinely supports, up to the limit — fewer is fine "
    "and expected; never pad with a weak pick just to hit the count. Every candidate "
    "must independently satisfy the BRAND MISSION, CLIP SELECTION RULES, and SINGLE "
    "SPEAKER rule above — do not include anything you would not defend as a full pick "
    "on its own.\n\n"
    "Each object in \"candidates\" must have exactly these keys:\n"
    '  "clip_start" : number — MUST be the exact start timestamp of one of the '
    "segments in the provided list, AND must fall at the BEGINNING of a complete "
    "thought (the start of a sentence or idea), never mid-sentence.\n"
    '  "clip_end"   : number — MUST be the exact end timestamp of a LATER segment '
    "in the list, AND must fall at the END of a complete thought or conclusion. The "
    "candidate MUST contain a full, self-contained idea WITH its payoff — never a "
    "cliffhanger. If a complete thought runs long, ANCHOR clip_end on its concluding/"
    "payoff sentence and choose clip_start as LATE as needed to fit the length limit "
    "below — never drop the payoff to keep an earlier opening line.\n"
    '  "hook"       : string — a short DRAFT contrarian identity-frame teaser for this '
    "candidate, under 15 words, so a human reviewer can judge it at a glance. This is "
    "a working draft, not final polished copy — final hook copy is written later, "
    "only for the candidate a human actually picks.\n"
    '  "exposes"    : string — one sentence: the hidden behavior, pattern, or '
    "mechanism this clip exposes about the viewer.\n"
    '  "reframe"    : string — one sentence: the new lens or mechanism the clip hands '
    "the viewer.\n"
    '  "payoff"     : string — one sentence: the concrete takeaway or resolution the '
    "viewer walks away with.\n\n"
    "For EACH candidate:\n"
    "Clip length: The clip window (clip_end - clip_start) MUST be at least "
    f"{config.CLIP_WINDOW_MIN_SECONDS} seconds and MUST NOT exceed "
    f"{config.CLIP_WINDOW_MAX_HARD_SECONDS} seconds — both are hard limits, not "
    "targets. A complete short thought that runs under "
    f"{config.CLIP_WINDOW_MIN_SECONDS} seconds is NOT acceptable — keep reading "
    "forward through the transcript to include the actionable payoff, the "
    "practical application, or the next concrete example until you reach the "
    "floor. "
    "You may run slightly longer, but (clip_end - clip_start) MUST NEVER exceed "
    f"{config.CLIP_WINDOW_MAX_HARD_SECONDS} seconds under ANY circumstances. "
    "Within that hard limit, COMPLETENESS BEATS EXACT LENGTH: prefer a contiguous "
    "run of segments that forms a complete mini-story or a complete piece of "
    "advice (setup AND payoff) over hitting a precise duration. If a complete "
    f"thought will not fit within {config.CLIP_WINDOW_MAX_HARD_SECONDS} seconds, "
    "pick a SHORTER self-contained thought that does fit — do NOT exceed the "
    "limit to capture a longer passage. When trimming to fit, trim from the "
    "FRONT (start later) so the clip still ENDS on the payoff; never drop the "
    "concluding sentence.\n\n"
    "The transcript is given as timestamped segments, one per line, formatted "
    "[start-end] text. For each candidate, choose a contiguous run of segments "
    "that forms a self-contained, compelling moment, and set clip_start to that "
    "run's first segment start and clip_end to its last segment end. Do NOT "
    "invent timestamps — only use values that appear in the list."
)


```

- [ ] **Step 2: Insert `find_candidates` and `filter_candidates`**

Find the line `def extract_highlights(transcript: dict) -> dict:` (still present, untouched, currently around line 1111). Insert the following two functions **immediately before** that line:

```python
def find_candidates(transcript: dict) -> list[dict]:
    """Stage 1: surface up to ``config.CANDIDATE_COUNT`` ranked clip candidates.

    One Sonnet call using ``CANDIDATE_SYSTEM_PROMPT``. Each candidate is a dict
    with ``clip_start``, ``clip_end``, ``hook`` (draft), ``exposes``, ``reframe``,
    ``payoff`` — no copywriting yet. Candidates are NOT snapped to sentence
    boundaries or content-gated here; that happens in :func:`filter_candidates`.
    """
    segments = transcript.get("segments") if isinstance(transcript, dict) else None
    if segments:
        body = "Here is the episode transcript as timestamped segments:\n\n" + _format_segments(segments)
    else:
        text = transcript.get("text", "") if isinstance(transcript, dict) else str(transcript)
        if not text.strip():
            raise ValueError("Transcript has no segments or text to analyze")
        body = f"Here is the episode transcript:\n\n{text}"

    logger.info("Finding candidates via %s (%d segments)", config.EXTRACT_MODEL, len(segments or []))
    client = _client()

    response = client.messages.create(
        model=config.EXTRACT_MODEL,
        max_tokens=config.EXTRACT_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": CANDIDATE_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": body}],
    )

    raw = next((b.text for b in response.content if b.type == "text"), "")
    parsed = json.loads(_strip_to_json(raw))

    candidates = parsed.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(f"Expected a non-empty 'candidates' array, got: {parsed!r}")

    valid: list[dict] = []
    required = ("clip_start", "clip_end", "hook", "exposes", "reframe", "payoff")
    for c in candidates:
        if not isinstance(c, dict) or not all(k in c for k in required):
            logger.warning("Dropping malformed candidate (missing keys): %r", c)
            continue
        if not isinstance(c["clip_start"], (int, float)) or not isinstance(c["clip_end"], (int, float)):
            logger.warning("Dropping malformed candidate (non-numeric window): %r", c)
            continue
        valid.append(c)

    if not valid:
        raise ValueError(f"No usable candidates in model response: {parsed!r}")

    valid = valid[: config.CANDIDATE_COUNT]
    logger.info("Found %d raw candidate(s)", len(valid))
    return valid


def filter_candidates(candidates: list[dict], transcript: dict) -> list[dict]:
    """Stage 1 continued: snap each candidate to sentence boundaries and drop
    anything that fails the content gate.

    Reuses :func:`_snap_to_sentences`, :func:`_extend_to_floor`,
    :func:`_trim_to_cap`, and :func:`_content_gate` exactly as
    ``extract_highlights`` used to — applied per-candidate. A candidate whose
    window falls outside ``[CLIP_WINDOW_MIN_SECONDS, CLIP_WINDOW_MAX_HARD_SECONDS]``
    after snapping, or that fails :func:`_content_gate` (raises ``ValueError``),
    is dropped rather than raised. The model's original rank order is preserved
    among survivors.
    """
    words = transcript.get("words") if isinstance(transcript, dict) else None
    segments = transcript.get("segments") if isinstance(transcript, dict) else None

    survivors: list[dict] = []
    for candidate in candidates:
        c = dict(candidate)  # don't mutate the caller's list in place
        try:
            if words:
                _snap_to_sentences(c, words)
                _extend_to_floor(c, words, segments or [])
                _trim_to_cap(c, words)
            window = c["clip_end"] - c["clip_start"]
            lo, hi = config.CLIP_WINDOW_MIN_SECONDS, config.CLIP_WINDOW_MAX_HARD_SECONDS
            if not (lo <= window <= hi):
                logger.warning(
                    "Dropping candidate: window %.1fs outside [%d, %d]s after snapping (hook=%r)",
                    window, lo, hi, c.get("hook"),
                )
                continue
            _content_gate(c, transcript)
        except ValueError as exc:
            logger.warning("Dropping candidate (hook=%r): %s", c.get("hook"), exc)
            continue
        survivors.append(c)

    logger.info("Filtered %d candidate(s) -> %d survivor(s)", len(candidates), len(survivors))
    return survivors


```

- [ ] **Step 3: Verify syntax**

Run: `.\venv\Scripts\python.exe -m py_compile modules\ai_extract.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Verify import wiring**

Run: `.\venv\Scripts\python.exe -c "from modules import ai_extract; print(ai_extract.find_candidates); print(ai_extract.filter_candidates); print(len(ai_extract.CANDIDATE_SYSTEM_PROMPT) > 0); print(ai_extract.extract_highlights)"`
Expected: prints the two new function objects, `True`, and the still-present `extract_highlights` function object (it must NOT be broken by this task — Task 3 removes it).

- [ ] **Step 5: Commit**

```bash
git add modules/ai_extract.py
git commit -m "Add Stage 1 candidate shortlist: CANDIDATE_SYSTEM_PROMPT, find_candidates, filter_candidates"
```

---

### Task 3: `modules/ai_extract.py` — Stage 2 (`COPY_SYSTEM_PROMPT`, `extract_copy_for_window`, `extract_copy_with_retry`); remove Stage-0 code; update the module's own harness; delete the stale `scripts/verify_prompt.py`

**Files:**
- Modify: `modules/ai_extract.py`
- Delete: `scripts/verify_prompt.py`

**Interfaces:**
- Consumes: Task 2's `find_candidates`/`filter_candidates` (used only by this task's updated `__main__` harness, not by the library functions themselves). Existing `_validate(data) -> None` (raises `ValueError`; already calls `_scene_safety_gate` internally), `_brand_gate(data) -> None`, `_content_gate(data, transcript) -> None`, `_format_segments(segments) -> str`, `_client()`, `_strip_to_json(text) -> str`, `_RETRY_SLEEP_S` (module constant, currently `65`).
- Produces:
  - `COPY_SYSTEM_PROMPT: str`.
  - `extract_copy_for_window(transcript: dict, clip_start: float, clip_end: float, seed: dict) -> dict` — returns the same schema `extract_highlights` used to (`hook, insights[3], best_quote, title, clip_start, clip_end, hashtags[3-8], image_prompts[4], search_queries[5], video_queries[5]`). Consumed by Task 4's `main.py` and Task 5's `video_gen.py` harness.
  - `extract_copy_with_retry(transcript: dict, clip_start: float, clip_end: float, seed: dict, attempts: int = 3) -> dict` — same retry contract `extract_highlights_with_retry` used to. Consumed by Task 4 and Task 5.
- Removes: `SYSTEM_PROMPT`, `extract_highlights`, `extract_highlights_with_retry` (all three fully deleted from this file — do not leave commented-out remnants).

- [ ] **Step 1: Insert `COPY_SYSTEM_PROMPT`**

Insert the following new constant in the same location `CANDIDATE_SYSTEM_PROMPT` occupies (immediately after it, still before `def _client():`):

```python
COPY_SYSTEM_PROMPT = (
    "You are a podcast clip producer writing the social copy and visual "
    "art-direction for an ALREADY-CHOSEN short-form clip. The clip's transcript "
    "window has already been picked by a human — you do not choose it and must "
    "not change it. Your job is the copywriting and art-direction package only.\n\n"
    "You MUST respond with ONLY a single valid JSON object and nothing else — "
    "no markdown, no code fences, no commentary before or after.\n\n"
    "HOOK RULES — these determine 90% of whether the clip gets views.\n\n"
    "The hook MUST use a CONTRARIAN IDENTITY FRAME. It must challenge the viewer's "
    "current behavior or worldview and imply they are on the wrong side of a divide. "
    "The viewer should feel: 'wait — am I doing this wrong?'\n\n"
    "WINNING FORMULA (use one of these structures every time):\n"
    "  - 'Your [brain/nervous system/body] [won't let go of/is addicted to/is hijacking you with] "
    "[common behavior] — [until/unless] [condition]' — DEFAULT / HIGHEST RETENTION (68-69% avg-view-"
    "duration, the best of any hook family tested). Prioritize this whenever the clip is TIER 1 "
    "topic. The mechanism named must be immediately, viscerally felt — not abstract — so the payoff "
    "lands with almost no drop-off.\n"
    "  - 'You're [doing common thing] and it's [unexpected negative consequence]'\n"
    "  - '[Common belief] is a lie — here's what [wise/successful people] actually do'\n"
    "  - 'Every [person/situation] has [two sides] — [one destroys], [one elevates]'\n"
    "  - 'The world is [broken in specific way] — and [most people/you] are [complicit/unaware]'\n"
    "  - '[Uncomfortable truth] that nobody wants to hear'\n"
    "  - 'Stop [common behavior] — it's [destroying/weakening] [something you value]'\n"
    "  - IDENTITY-STAKES NUMBERED RULES (validated exception to the listicle ban — see BANNED "
    "PATTERNS note below): '[Deep identity outcome — freedom/power/respect/control] — [N] "
    "[rules/things/truths] that actually [work/matter]'. This is NOT a generic tips list: the "
    "number must be in service of a contrarian identity payoff, framed as 'most people get this "
    "wrong', not as neutral how-to information. Second-best performer tested (1,190 views, 61.1% "
    "retention) when done this way.\n\n"
    "BANNED HOOK PATTERNS (these get 2-4 views, proven by data):\n"
    "  - 'X tips/tricks/hacks for Y' — instructional, zero identity tension. EXCEPTION: a numbered "
    "format is allowed ONLY when it follows the IDENTITY-STAKES NUMBERED RULES structure above "
    "(deep identity payoff + contrarian 'actually work' framing) — generic utility listicles "
    "('5 tips to sleep better', '10 productivity hacks') remain banned.\n"
    "  - 'How to [achieve thing]' — promises information, not transformation\n"
    "  - 'The science behind X' — educational frame, audience scrolls past\n"
    "  - 'Why X happens' — explanatory, no stakes\n"
    "  - 'X things you didn't know about Y' — listicle, no emotional charge\n"
    "  - Any hook that could be a YouTube tutorial title\n\n"
    "METAPHOR HOOK RULE: a hook built on a clever or abstract metaphor (e.g. 'Your fear is a GPS') "
    "drives strong curiosity/clicks but is HIGH RISK for retention — tested data shows a metaphor "
    "hook can win #1 in views (1,389) while losing badly on retention (39.2%, viewers bail in ~7s) "
    "because the metaphor is never concretely cashed out. If you use a metaphor hook, the clip's "
    "OPENING segment (clip_start) MUST immediately and concretely explain what the metaphor means "
    "in practice — do not let it sit unexplained while the speaker builds up to it. If the "
    "available transcript doesn't unpack the metaphor within the first few sentences of the clip, "
    "prefer the neurological WINNING FORMULA instead.\n\n"
    "The hook must be writable in under 15 words. If it needs more, it's not sharp "
    "enough. The hook is NOT a summary of the clip — it's a provocative reframe that "
    "makes the clip's content feel urgent.\n\n"
    "Even when the source content is scientific (e.g. Huberman neuroscience), reframe "
    "the hook through identity/worldview:\n"
    "  - BAD: 'Your diet controls your sleep quality — and these 3 foods are why'\n"
    "  - GOOD: 'You're destroying your sleep every night and calling it dinner'\n"
    "  - BAD: 'Higher fiber intake leads to more deep sleep'\n"
    "  - GOOD: 'The meal you ate last night stole 2 hours of deep sleep from you'\n"
    "  - BAD: 'How to rewire your brain to love discipline'\n"
    "  - GOOD: 'Your brain has been secretly punishing you for trying to improve'\n\n"
    "REAL REJECTED EXAMPLE — memorize this failure pattern:\n"
    "  Episode: 'Why Is Behavioural Genetics Such A Hated Science?'\n"
    "  REJECTED hook: 'Science told millions the wrong story — and nobody apologized'\n"
    "  REJECTED insights:\n"
    "    - 'Entire careers were built on genetic research that turned out to be 99% unreplicable.'\n"
    "    - 'The system laundered bad science: negative results buried, positive ones published.'\n"
    "    - 'Science reformed itself — but never told the public it had been wrong for decades.'\n"
    "  WHY FULLY REJECTED:\n"
    "    (a) Hook is institutional — it describes what science did to 'millions', not what is "
    "happening TO the viewer. No 'you', no identity frame, no agency.\n"
    "    (b) All 3 insights are 3rd-person descriptions of an external institution. The viewer "
    "learns about a scandal — not about themselves. Zero self-awareness payoff.\n"
    "    (c) The viewer leaves feeling deceived by institutions with no path forward. This fails "
    "the HOPE + AGENCY brand outcome completely.\n"
    "  CORRECT REFRAME from the same episode:\n"
    "    GOOD hook: 'You've been building beliefs about yourself on science that was never proven'\n"
    "    GOOD insights:\n"
    "      - 'Your self-image may rest on studies that never replicated — and nobody told you.'\n"
    "      - 'You accepted expert consensus without knowing their evidence was statistically void.'\n"
    "      - 'The beliefs you hold about your own nature may have been shaped by bad data.'\n"
    "  KEY RULE: Any episode about science, institutions, history, or external systems MUST be "
    "reframed to show how it acts on the VIEWER — their beliefs, their self-image, their "
    "identity, their decisions. If you cannot find a moment where the external topic lands "
    "INSIDE the viewer's life, that episode fails the brand mission — pick a different clip.\n\n"
    "The JSON object must have exactly these keys:\n"
    '  "hook"        : string — a contrarian identity-frame hook, under 15 words. See HOOK RULES above.\n'
    '  "insights"    : array of exactly 3 strings — the key takeaways. Each '
    "insight MUST be <= 100 characters total. Hard cap, no exceptions. Write them as "
    "IDENTITY STATEMENTS, not explanations — punchier is better. EVERY insight must "
    "use 'you/your', 'me/my' (viewer's internal voice), or open with a direct "
    "imperative ('Move first', 'Stop', 'Choose') — pure 3rd-person descriptions of "
    "external events ('careers were built on…', 'the system did…') are banned:\n"
    "  - BAD: 'Higher fiber intake leads to more deep sleep according to research' (explanatory)\n"
    "  - GOOD: 'Your fiber intake dictates your deep sleep — period' (identity frame)\n"
    "  - BAD: 'Choosing the right handle means asking if this is happening to me or for me'\n"
    "  - GOOD: 'Is this happening TO me or FOR me? That question changes everything.'\n"
    "  Long insights make slide text shrink and become unreadable at thumbnail size.\n"
    '  "best_quote"  : string — the single most quotable verbatim line from the '
    "speaker. It MUST pass ALL of these tests:\n"
    "      1. Works as a standalone screenshot — someone seeing it with zero "
    "context still gets the full punch.\n"
    "      2. Has gravity or precision — not casual filler (\"dude\", \"like\", "
    "\"you know\"), not a half-thought that needs the surrounding conversation "
    "to land.\n"
    "      3. Under 25 words — tighter is stronger.\n"
    "      4. Sounds like something worth writing on a wall, not something you'd "
    "say in a text.\n"
    "      If the best candidate fails these tests, pick the second-best. Do NOT "
    "include a quote just because it's the loudest or most energetic moment.\n"
    "      QUOTE CHARACTER: The quote must feel like something carved in stone — "
    "timeless, defiant, and memorable. Prefer quotes that challenge the viewer's "
    "comfort. Never select quotes that are merely wise or pleasant. The quote should "
    "make someone want to screenshot it:\n"
    "  - BAD: 'When you don't sleep enough, you have physiological signals to eat more'\n"
    "  - GOOD: 'You grab the handle that makes you stronger — not the one that strips you of agency'\n"
    '  "title"       : string — a punchy video title (<= 80 chars).\n'
    '  "hashtags"    : array of strings — 3 to 8 relevant hashtags, each '
    'starting with "#".\n'
    '  "image_prompts": array of exactly 4 strings — cinematic visual '
    "descriptions for AI-generated vertical 9:16 background images that match "
    "the episode's theme and mood.\n"
    '  "search_queries": array of exactly 5 strings — one art-directed stock-'
    "PHOTO search query per carousel slide, IN THIS ORDER: [0] cover (matches the "
    "hook), [1] insight 1, [2] insight 2, [3] insight 3, [4] quote. Each 2-4 "
    "words. See the SEARCH_QUERIES art-direction rules below.\n"
    '  "video_queries": array of exactly 5 OBJECTS — art-directed stock-VIDEO '
    "beats for the moving background BEHIND the chosen clip (clip_start to "
    "clip_end). The FIRST 4 are the primary beats; the 5th is a SPARE BACKUP "
    "beat (same tone, a different scene) used only if one of the primary clips "
    "can't be sourced. These are SEPARATE from search_queries and tuned for "
    'motion footage. Each object is {"keyword": <one concept word>, "query": '
    "<2-4 word portrait stock-video search>}. See the VIDEO_QUERIES "
    "art-direction rules below.\n\n"
    "Style guidance for image_prompts: realistic lifestyle and cinematic "
    "environments — people in motion, cities at golden hour, gyms, workspaces "
    "with natural light, silhouettes, wide establishing shots. AVOID tight face "
    "close-ups. Each prompt must be vivid, specific, and self-contained "
    "(describe the scene, lighting, mood, and framing), suitable as a darkened "
    "background behind bold captions in a vertical 9:16 video.\n\n"
    "NEVER DEPICT (HARD BLACKLIST — applies to BOTH video_queries AND "
    "search_queries). NEVER produce queries that would surface footage/photos "
    "depicting any of these:\n"
    "  - Hunched, slumped, head-down, or seated-in-defeat human postures. The "
    "body must read upright, in motion, or in calm purposeful stillness.\n"
    "  - Smoking, vaping, drinking alcohol, drug use, junk food, or any active "
    "vice or unhealthy behavior.\n"
    "  - Readable signage, graffiti, books with legible text, flipcharts, "
    "screens displaying words, or any on-screen text that a viewer can read.\n"
    "  - Stadiums, conferences, presentations to audiences, or any organized "
    "group/event setting. A person walking through a naturally busy street or "
    "public space is fine — the subject must remain a SINGLE identifiable figure "
    "among anonymous passersby, never a crowd scene where no individual stands out.\n"
    "  - Crowds of any size, religious gatherings, political gatherings, "
    "protests, marches, festivals, or any scene showing an identifiable "
    "ethnic, cultural, or religious group activity. If a query could plausibly "
    "return a photo of a mass of people, do not write it — no 'crowd passing', "
    "'crowd rushing', 'people gathering', or similar phrasing, ever.\n"
    "  - MORE THAN ONE PERSON, period. No couples, no duos, no two men/two "
    "women, no friends walking together, no families, no partners. Every "
    "single query — video AND photo — must depict EXACTLY ONE man, alone, "
    "with no other person visible in frame. If the transcript's content "
    "literally compares two people or two paths ('you and I', 'him vs her', "
    "'one person does X, another does Y'), you MUST still depict only ONE "
    "figure — the viewer's own single path — never render the comparison as "
    "two figures in one shot.\n"
    "  - Faces of identifiable people (close-up portraits where the person is "
    "the subject).\n"
    "  - Person lying in bed, or intimate/sensual positioning.\n"
    "  - Traffic, cars, busy intersections, or stationary urban infrastructure "
    "(power lines, construction, parking lots). Walking/running THROUGH a city is "
    "allowed — the person must be the subject, not the infrastructure.\n"
    "  - Flowers, food styling, or Pinterest-aesthetic flat-lay arrangements.\n"
    "  - Coffee cups, mugs, energy drinks, supplement bottles, pills, or any food "
    "or drink being prepared, held, or consumed. No kitchen scenes, no café "
    "counter shots, no hands wrapped around a mug.\n"
    "  - Female figures. Every human subject must read as MALE — a man working on "
    "himself. A female figure breaks viewer identification for this audience.\n"
    "  - Vacation, leisure, or tourism aesthetics: beach strolls, coastal walks, "
    "people lounging at sunset, resort or holiday scenery, anyone who looks like "
    "they are relaxing or sightseeing. Every scene must feel deliberate and "
    "purposeful — the subject is working, training, thinking, or moving with "
    "intent. Not unwinding.\n\n"
    "SEARCH_QUERIES — exactly 5 Pexels PHOTO search queries, one per slide IN "
    "ORDER. Each photo must ILLUSTRATE its slide's specific message — the viewer "
    "should feel the connection between the words on the slide and the image "
    "behind them:\n"
    "  [0] cover — visual that matches the hook's energy and stakes. Must convey "
    "the hook's CONCEPT, not just be dramatic.\n"
    "  [1] insight 1 — scene that ILLUSTRATES this specific insight. Ask: what "
    "real-world scene IS this insight? A person in what situation, doing what?\n"
    "  [2] insight 2 — same rule: find the scene that makes this insight VISIBLE.\n"
    "  [3] insight 3 — same rule.\n"
    "  [4] quote — a scene that embodies what the quote SAYS, not just its mood.\n"
    "COVER SLIDE PRIORITY: search_queries[0] is the MOST IMPORTANT — it becomes "
    "the Instagram grid thumbnail. It MUST be visually dramatic at 1:1 crop: high "
    "contrast, clear subject, no busy detail. Default to TIER 1 scenes (lone MALE "
    "figure against vast landscape, male silhouette at sunrise/sunset) unless the "
    "content specifically demands otherwise. Dramatic solitary landscape outperforms "
    "warm interior by 14x on Instagram grid — bias the cover hard toward TIER 1.\n"
    "MALE-ONLY RULE: All human subjects across ALL 5 slide photos must be MALE. "
    "No female figures anywhere in the carousel.\n"
    "Rules for every query:\n"
    "  - Describe a REAL SCENE a stock photographer actually shot (person walking "
    "foggy path, man looking at city from rooftop, runner at dawn, figure walking "
    "beach shoreline, person walking through city crowd, musician playing guitar "
    "warm light, hands writing in notebook — specific and physical).\n"
    "  - Do NOT use abstract concepts as queries (no \"success\", \"ambition\", "
    "\"clarity\", \"mindset\" — these return generic stock photos).\n"
    "  - 2-4 words max, portrait-friendly composition preferred.\n"
    "  - All 5 visually distinct — no two should return the same type of scene.\n"
    "  - PALETTE: All 5 search_queries must share the same single tonal palette "
    "chosen for the video (see VIDEO_QUERIES rule 8). Slide photos and video clips "
    "must feel like one body of work, not five different accounts. Apply the SAME "
    "two hard requirements as rule 8: APPEND the palette's lighting treatment "
    "word to EVERY query, and NEVER pick a scene whose real-world light fights the "
    "palette (no fog/overcast/night/dusk under a warm palette, etc.).\n\n"
    "VIDEO_QUERIES — you are a DOCUMENTARY FILM EDITOR choosing B-roll that "
    "ILLUSTRATES what the speaker is saying. Every shot must visually reinforce "
    "the specific idea spoken in that quarter of the clip — the footage IS the "
    "storytelling, not decoration. A viewer watching on mute must be able to "
    "GUESS the topic from the footage alone.\n"
    "Produce EXACTLY 5 beats: 4 primary beats that map IN ORDER to the 4 quarters "
    "of the clip window, PLUS a 5th SPARE backup (distinct scene, same tone) used "
    "only if a primary clip can't be sourced.\n\n"
    "═══ STEP 1 — CONTENT ANALYSIS (MANDATORY — DO THIS BEFORE ANYTHING ELSE) ═══\n"
    "Divide the clip window into 4 roughly equal time quarters. For EACH quarter, "
    "write one plain sentence describing the SPECIFIC concept, idea, or action the "
    "speaker is expressing in those seconds. Do NOT skip to scene selection. You "
    "cannot choose a scene until you have named each quarter's specific content.\n"
    "  Q1: What problem, behavior, or situation is being introduced?\n"
    "  Q2: What is the mechanism, tension, or WHY it matters?\n"
    "  Q3: What is the insight, reframe, or turning point?\n"
    "  Q4: What is the payoff, resolution, or call to action?\n\n"
    "═══ STEP 2 — SCENE SELECTION (derived from content, not from a list) ═══\n"
    "For each quarter, ask: 'What real-world scene visually MEANS the same thing "
    "as the concept I identified in STEP 1?' The scene subject MUST come from the "
    "spoken content — not from a preset list of generic shots.\n"
    "  BAD (generic, could go under ANY motivational clip):\n"
    "    Concept 'you avoid difficult choices' → 'lone figure mountain sunrise' "
    "(decorative, says nothing about avoidance or choice)\n"
    "    Concept 'brain creates fake scenarios' → 'mountain lake mist' "
    "(pretty but semantically unrelated to brain/scenarios)\n"
    "    Concept 'consequences come later' → 'cliff edge silhouette' "
    "(generic dramatic, no connection to future consequences)\n"
    "  GOOD (scene subject IS the concept):\n"
    "    Concept 'you keep choosing the easier path' → 'figure standing at fork "
    "in road golden hour' (two visible paths = the choice moment is filmable)\n"
    "    Concept 'the pain comes later anyway' → 'person walking alone into open "
    "horizon dusk' (moving toward unknown future = consequences ahead)\n"
    "    Concept 'brain creates fake scenarios' → 'man standing motionless as "
    "storm clouds race overhead' (frozen in your head while the world moves — "
    "NEVER use a crowd or second person to show 'world moving'; use weather, "
    "light, or motion blur in the environment instead)\n"
    "    Concept 'discipline is freedom' → 'runner on open empty road at dawn' "
    "(chosen effort, unrestricted horizon ahead)\n"
    "    Concept 'you suppress who you are' → 'person walking away open door light' "
    "(stepping OUT of confinement = aspirational counterpart of suppression)\n"
    "    Concept 'stand in your truth' → 'lone figure mountain summit sunrise' "
    "(standing tall, exposed, unafraid = owning your conviction)\n\n"
    "═══ STEP 3 — PALETTE (lighting/mood modifier applied after scenes are chosen) ═══\n"
    "After scenes are selected from content, apply ONE palette as a lighting and "
    "mood modifier. The palette does NOT change the scene subject — only light "
    "quality and color temperature:\n"
    "  - DRAMATIC-NATURAL: golden hour, dawn, dusk, storm light, high contrast. "
    "DEFAULT — use unless content demands otherwise.\n"
    "  - COOL-CINEMATIC: blue hour, urban night, rain. Use for modern-life or "
    "culture-critique content.\n"
    "  - WARM-INTERIOR: lamplight, morning window. Use ONLY for "
    "contemplation/writing/reading content.\n"
    "APPEND the palette's treatment word to EVERY query. Do NOT pick a scene "
    "whose real-world light fights the palette.\n\n"
    "VISUAL QUALITY TIERS (how to FRAME the scene — NOT a scene shopping list; "
    "scene subjects always come from STEP 1 content first. ALL subjects are "
    "EXACTLY ONE MALE FIGURE, always alone, never a second person of any kind "
    "in frame — this is the single most important rule in this section):\n"
    "TIER 1 — TRAINING / PHYSICAL EFFORT (highest impact, serious, earned): one "
    "man training alone at dawn — push-ups on a rooftop, pull-ups on a bar, "
    "punching a bag, lifting weights in an empty gym, a hard sprint or trail run "
    "on an empty road. Must feel earned and serious — never recreational.\n"
    "TIER 2 — WORK / DISCIPLINE / REFLECTION (purposeful, introspective): one "
    "man working — at a laptop or workshop bench under a single lamp, writing "
    "down goals or journaling with intensity at a desk, meditating alone in "
    "stillness (seated upright, eyes closed, one figure only), walking "
    "purposefully through a city street at dawn (going somewhere, not "
    "strolling), sitting alone in focused thought by a window or on a rooftop.\n"
    "TIER 3 — AVOID: beach walks, coastal strolls, vacation scenery, coffee "
    "shops, lifestyle interiors, anyone who looks like they are relaxing, and "
    "ANY shot with a second person, couple, group, or crowd regardless of tier.\n\n"
    "RULES:\n"
    "1. CONTENT-FIRST (NON-NEGOTIABLE): Every beat's scene subject must come "
    "directly from the specific concept named in STEP 1. If the same query could "
    "appear under ANY motivational clip regardless of what the speaker said, it is "
    "too generic — rewrite it to be concept-specific.\n"
    '2. No proper nouns, brand names, or place names.\n'
    "3. TONAL CONSISTENCY: All 4 primary beats + spare share the chosen palette — "
    "same season, lighting direction, color temperature. APPEND the palette's "
    "treatment word to EVERY query. All 5 keywords distinct, all 5 queries distinct.\n"
    "4. ASPIRATIONAL ONLY: When content describes a negative (avoiding, fearing, "
    "giving up), show the positive counterpart — never the failure-state, never a "
    "defeated or slumped subject.\n"
    "5. SUBJECTS: calm and inward, never performative. No grinning at camera, "
    "gesturing theatrically, or mid-laugh. Quiet determination.\n"
    "6. NO legible on-screen TEXT in footage.\n"
    "7. KEYWORD: aspirational or neutral — NEVER negative (overwhelm, fear, anxiety, "
    "defeat). Use the counterpart: clarity, momentum, stillness, resolve, conviction.\n"
    "8. SAFE-SCENE FALLBACK: For abstract concepts with no filmable scene, fall back "
    "to one of these MALE, serious defaults: man doing push-ups at dawn on empty "
    "surface, man standing at rooftop edge at sunrise looking out, man walking alone "
    "on empty road in morning mist, man writing intensely at a desk under a lamp. "
    "NEVER fall back to a beach walk or coastal stroll. "
    "LIMIT: AT MOST ONE fallback per clip. If 2+ beats need a fallback, revisit "
    "STEP 1 — your content analysis was too vague.\n"
    "9. SELF-CHECK: Read back all 4 primary queries. For each ask: 'Does this shot "
    "SPECIFICALLY illustrate the concept spoken in that quarter, or is it a generic "
    "landscape that could go under any clip?' More than one generic query = rewrite.\n"
    '10. Format: {"keyword": "one emotion word", "query": "filmable scene treatment"}\n'
    "Example — clip about 'you always choose easy now and pay the price later':\n"
    "STEP 1 content analysis:\n"
    "  Q1 (you keep picking the comfortable option): concept = avoiding the harder path\n"
    "  Q2 (the pain comes either way): concept = future consequences are unavoidable\n"
    "  Q3 (choose your hard intentionally): concept = deliberate difficult forward motion\n"
    "  Q4 (the reward is on the other side): concept = earned open horizon\n"
    "DRAMATIC-NATURAL palette:\n"
    '[{"keyword": "choice", "query": "figure standing fork in road golden hour"}, '
    '{"keyword": "consequence", "query": "person walking alone open horizon dusk"}, '
    '{"keyword": "resolve", "query": "runner uphill empty road dawn"}, '
    '{"keyword": "freedom", "query": "person arms open cliff edge sunrise"}, '
    '{"keyword": "clarity", "query": "lone figure mountain summit golden hour"}]\n'
    "Each query is derived from its quarter's specific concept — not from a list.\n\n"
    "You are given the transcript excerpt for the already-chosen clip window "
    "below, plus a short note on why this segment was chosen (what it exposes, "
    "its reframe, and its payoff) for context only — you do not choose or adjust "
    "the window. Base every element of your copy on the actual words spoken in "
    "the excerpt; do not invent claims the speaker didn't make."
)


```

- [ ] **Step 2: Insert `extract_copy_for_window` and `extract_copy_with_retry`, then remove the old Stage-0 code**

First, **delete** the entire `SYSTEM_PROMPT = ( ... )` constant (currently lines 23-485) and the entire `extract_highlights` function (currently lines 1111-1183) and the entire `extract_highlights_with_retry` function (currently lines 1189-1220). Leave `_RETRY_SLEEP_S = 65` (currently line 1186) in place — it's reused by the new retry function below.

In the gap left where `extract_highlights` used to be (immediately after `_format_segments`, before whatever now follows), insert:

```python
def _format_window_segments(segments: list, clip_start: float, clip_end: float) -> str:
    """Render only the segments overlapping [clip_start, clip_end] (with a small
    0.5s padding on each side) as grounded, timestamped lines, for the Stage 2
    copywriting prompt."""
    windowed = [
        s for s in segments
        if s.get("start") is not None and s.get("end") is not None
        and s["end"] >= clip_start - 0.5 and s["start"] <= clip_end + 0.5
    ]
    return _format_segments(windowed)


def extract_copy_for_window(
    transcript: dict, clip_start: float, clip_end: float, seed: dict
) -> dict:
    """Stage 2: write the full copy/art-direction package for an ALREADY-CHOSEN
    clip window.

    ``clip_start``/``clip_end`` are FIXED inputs, not chosen here. ``seed`` is
    the approved candidate dict (``hook``/``exposes``/``reframe``/``payoff``),
    passed as context so the copy stays anchored to what was approved. Returns
    the same schema ``extract_highlights`` used to (``hook``, ``insights``,
    ``best_quote``, ``title``, ``clip_start``, ``clip_end``, ``hashtags``,
    ``image_prompts``, ``search_queries``, ``video_queries``).

    Runs ``_validate`` (schema/count checks — the window itself was already
    vetted by ``filter_candidates``, so its bounds check always passes here;
    ``_validate`` also calls ``_scene_safety_gate`` internally), then
    ``_brand_gate``, then ``_content_gate``.
    """
    segments = transcript.get("segments") if isinstance(transcript, dict) else None
    if segments:
        excerpt = _format_window_segments(segments, clip_start, clip_end)
    else:
        excerpt = transcript.get("text", "") if isinstance(transcript, dict) else str(transcript)

    if not excerpt.strip():
        raise ValueError("No transcript excerpt found for the given clip window")

    context = (
        f"Hook (draft): {seed.get('hook', '')}\n"
        f"Exposes: {seed.get('exposes', '')}\n"
        f"Reframe: {seed.get('reframe', '')}\n"
        f"Payoff: {seed.get('payoff', '')}"
    )
    body = (
        f"Clip window: {clip_start:.2f}s - {clip_end:.2f}s\n\n"
        f"Why this candidate was chosen:\n{context}\n\n"
        f"Transcript excerpt for this window:\n\n{excerpt}"
    )

    logger.info("Extracting copy via %s for window %.2f-%.2fs", config.EXTRACT_MODEL, clip_start, clip_end)
    client = _client()

    response = client.messages.create(
        model=config.EXTRACT_MODEL,
        max_tokens=config.EXTRACT_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": COPY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": body}],
    )

    raw = next((b.text for b in response.content if b.type == "text"), "")
    parsed = json.loads(_strip_to_json(raw))

    # clip_start/clip_end are FIXED inputs, not chosen by this call — inject
    # them so _validate's schema/window checks (unchanged, reused as-is) still
    # apply; the window itself was already vetted by filter_candidates.
    parsed["clip_start"] = clip_start
    parsed["clip_end"] = clip_end

    _validate(parsed)
    _brand_gate(parsed)
    _content_gate(parsed, transcript)

    logger.info("Extracted copy for window %.1f-%.1fs | title=%r", clip_start, clip_end, parsed.get("title"))
    return parsed


def extract_copy_with_retry(
    transcript: dict, clip_start: float, clip_end: float, seed: dict, attempts: int = 3
) -> dict:
    """Call :func:`extract_copy_for_window` up to ``attempts`` times, tolerating
    the model's non-deterministic output.

    Unlike the old blind retry, this regenerates copy for the SAME fixed
    window each attempt — it cannot drift to a different segment. Only
    ``ValueError`` and ``anthropic.RateLimitError`` are caught; other
    transport/API errors propagate immediately. Re-raises the last error after
    the final attempt fails.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return extract_copy_for_window(transcript, clip_start, clip_end, seed)
        except ValueError as exc:
            last_exc = exc
            logger.warning(
                "extract_copy_for_window attempt %d/%d failed (non-deterministic): %s",
                attempt, attempts, exc,
            )
        except anthropic.RateLimitError as exc:
            last_exc = exc
            logger.warning(
                "extract_copy_for_window attempt %d/%d hit rate limit: %s",
                attempt, attempts, exc,
            )
        if attempt < attempts:
            logger.info("Sleeping %ds before retry %d/%d …", _RETRY_SLEEP_S, attempt + 1, attempts)
            time.sleep(_RETRY_SLEEP_S)
    assert last_exc is not None
    raise last_exc


```

- [ ] **Step 3: Update the module's `__main__` harness**

Replace the entire `if __name__ == "__main__":` block at the bottom of the file with:

```python
if __name__ == "__main__":
    import glob

    from modules import transcribe

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    mp3s = sorted(
        glob.glob(os.path.join(config.TMP_DIR, "*.mp3")),
        key=os.path.getmtime,
        reverse=True,
    )
    if not mp3s:
        raise SystemExit(f"No MP3 found in {config.TMP_DIR} - run rss_ingest first.")

    print(f"Transcribing: {os.path.basename(mp3s[0])}")
    transcript = transcribe.transcribe(mp3s[0])

    candidates = find_candidates(transcript)
    print(f"\n=== {len(candidates)} raw candidate(s) ===")
    for i, c in enumerate(candidates, 1):
        print(f"{i}. [{c['clip_start']:.1f}-{c['clip_end']:.1f}s] {c['hook']!r}")

    survivors = filter_candidates(candidates, transcript)
    print(f"\n=== {len(survivors)} survivor(s) after filtering ===")
    for i, c in enumerate(survivors, 1):
        print(f"{i}. [{c['clip_start']:.1f}-{c['clip_end']:.1f}s] {c['hook']!r}")

    if not survivors:
        raise SystemExit("No candidates survived filtering.")

    top = survivors[0]
    result = extract_copy_for_window(transcript, top["clip_start"], top["clip_end"], top)

    print("\n=== Extracted copy for top survivor ===")
    print(json.dumps(result, indent=2, ensure_ascii=False).encode("ascii", "replace").decode("ascii"))
    print(f"\nClip window: {result['clip_end'] - result['clip_start']:.1f}s (valid)")
```

- [ ] **Step 4: Delete the stale `scripts/verify_prompt.py`**

This throwaway script (its own docstring: `"""THROWAWAY verification driver for the SYSTEM_PROMPT quality changes."""`) calls `ai_extract.extract_highlights_with_retry`, which no longer exists after this task. It was a one-off tool for a past prompt-tuning task (nothing else in the repo imports it — verified via `grep -rn verify_prompt`), not ongoing pipeline infrastructure, so delete it rather than adapt it:

```bash
git rm scripts/verify_prompt.py
```

- [ ] **Step 5: Verify syntax**

Run: `.\venv\Scripts\python.exe -m py_compile modules\ai_extract.py`
Expected: no output, exit code 0.

- [ ] **Step 6: Verify import wiring and that the old names are gone**

Run:
```
.\venv\Scripts\python.exe -c "from modules import ai_extract; print(ai_extract.extract_copy_for_window); print(ai_extract.extract_copy_with_retry); print(hasattr(ai_extract, 'extract_highlights')); print(hasattr(ai_extract, 'extract_highlights_with_retry')); print(hasattr(ai_extract, 'SYSTEM_PROMPT'))"
```
Expected: prints the two new function objects, then `False`, `False`, `False`.

- [ ] **Step 7: Commit**

```bash
git add modules/ai_extract.py
git commit -m "Add Stage 2 copy extraction; remove single-pass extract_highlights; delete stale verify_prompt.py script"
```

---

### Task 4: `main.py` — interactive episode/candidate picker

**Files:**
- Modify: `main.py`

**Interfaces:**
- Consumes: Task 3's `ai_extract.find_candidates`, `ai_extract.filter_candidates`, `ai_extract.extract_copy_with_retry`. Existing `rss_ingest.pick_random_entry(feed_url, exclude_guids, host_name=None) -> tuple | None`, `rss_ingest.download_latest(feed, entry) -> dict`, `posted_history.load() -> dict`, `posted_history.mark_used(guid, feed, title) -> None` (idempotent), `transcribe.transcribe(audio_path) -> dict`.
- Removes: `_load_or_build_plan` (replaced by `_load_or_build_transcript` + the picker functions below). `_AUTO_MAX_EPISODE_ATTEMPTS` and its associated CONTENT/BRAND-GATE string-matching retry loops in `run_auto()` and the CLI `__main__` block (superseded — `_pick_episode_and_candidate` now owns all episode/candidate looping, bounded naturally by the RSS window via `pick_random_entry` returning `None` when exhausted).
- Produces (new, module-private):
  - `_load_or_build_transcript(audio_path: str) -> dict`
  - `_print_candidates(candidates: list[dict]) -> None`
  - `_find_and_pick_candidate(transcript: dict, interactive: bool = True) -> dict | None`
  - `_pick_episode_and_candidate(feed_arg: str, interactive: bool = True) -> tuple[dict, dict, dict]`
- Changes `run()`'s signature: adds `interactive: bool = True`.

- [ ] **Step 1: Replace `_load_or_build_plan` with `_load_or_build_transcript`**

Find this existing block (lines 86-123):

```python
def _plan_cache_path(audio_path: str) -> str:
    """The plan cache path video_gen uses: tmp/<basename>.plan.json."""
    return os.path.splitext(audio_path)[0] + ".plan.json"


def _write_plan(cache_path: str, transcript: dict, highlights: dict) -> None:
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"transcript": transcript, "highlights": highlights}, f)


def _load_or_build_plan(audio_path: str) -> tuple[dict, dict]:
    """Return (transcript, highlights), reusing the cache when present.

    Only transcribes (Groq) + extracts (Claude) on a cache miss, so repeat
    runs of the same episode never burn API credits.
    """
    cache_path = _plan_cache_path(audio_path)

    if os.path.exists(cache_path):
        logger.info("[2-3/6] Plan cache HIT: %s (skipping Groq + Claude)", os.path.basename(cache_path))
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        transcript, highlights = cached["transcript"], cached["highlights"]
        # Regenerate JUST the extraction (no Groq) if the cached plan predates a
        # schema field we now require.
        if "search_queries" not in highlights:
            logger.info("Cached plan missing search_queries; re-running extraction (no Groq)")
            highlights = ai_extract.extract_highlights_with_retry(transcript)
            _write_plan(cache_path, transcript, highlights)
        return transcript, highlights

    logger.info("[2/6] Transcribe (Groq Whisper): %s", os.path.basename(audio_path))
    transcript = transcribe.transcribe(audio_path)
    logger.info("[3/6] Extract clip plan (Claude %s)", config.EXTRACT_MODEL)
    highlights = ai_extract.extract_highlights_with_retry(transcript)
    _write_plan(cache_path, transcript, highlights)
    logger.info("Cached transcript + plan: %s", os.path.basename(cache_path))
    return transcript, highlights
```

Replace it entirely with:

```python
def _plan_cache_path(audio_path: str) -> str:
    """The final combined plan cache: tmp/<basename>.plan.json (video_gen reads
    this too). Written only once, after Stage 2 succeeds for an approved
    candidate."""
    return os.path.splitext(audio_path)[0] + ".plan.json"


def _transcript_cache_path(audio_path: str) -> str:
    """Transcribe-only cache: tmp/<basename>.transcript.json. Separate from the
    plan cache so re-running Stage 1 (e.g. across a reject-all candidate loop,
    or a second manual run before the episode is approved) never re-hits Groq,
    even though nothing has been approved yet."""
    return os.path.splitext(audio_path)[0] + ".transcript.json"


def _write_plan(cache_path: str, transcript: dict, highlights: dict) -> None:
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"transcript": transcript, "highlights": highlights}, f)


def _load_or_build_transcript(audio_path: str) -> dict:
    """Return the transcript dict, reusing a cache when present.

    Transcribe-only (Groq). The candidate/copy extraction (Claude) happens
    later, after a human picks a candidate, and must not force a
    re-transcription just because the pick changes or is rejected.
    """
    cache_path = _transcript_cache_path(audio_path)
    if os.path.exists(cache_path):
        logger.info("Transcript cache HIT: %s (skipping Groq)", os.path.basename(cache_path))
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    logger.info("[2/6] Transcribe (Groq Whisper): %s", os.path.basename(audio_path))
    transcript = transcribe.transcribe(audio_path)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(transcript, f)
    logger.info("Cached transcript: %s", os.path.basename(cache_path))
    return transcript


def _print_candidates(candidates: list[dict]) -> None:
    print("\nCandidate clips:")
    for i, c in enumerate(candidates, 1):
        print(f"  {i}. [{c['clip_start']:.1f}-{c['clip_end']:.1f}s] {c['hook']!r}")
        print(f"     exposes: {c.get('exposes', '')}")
        print(f"     reframe: {c.get('reframe', '')}")
        print(f"     payoff : {c.get('payoff', '')}")


def _find_and_pick_candidate(transcript: dict, interactive: bool = True) -> dict | None:
    """Run Stage 1 (find + filter) and return the chosen candidate, or ``None``
    if there are no survivors or the human rejects every candidate.

    ``interactive=False`` (used by ``--auto``) skips the prompt and takes the
    top-ranked survivor automatically — no human is present for scheduled runs.
    """
    candidates = ai_extract.find_candidates(transcript)
    survivors = ai_extract.filter_candidates(candidates, transcript)
    if not survivors:
        logger.info("No viable candidates after filtering (%d raw)", len(candidates))
        return None

    if not interactive:
        logger.info("Auto mode: taking top candidate (%d survivor(s))", len(survivors))
        return survivors[0]

    _print_candidates(survivors)
    choice = input("Pick a number, or 0 to reject all: ").strip()
    if choice == "0":
        return None
    try:
        idx = int(choice) - 1
    except ValueError:
        idx = -1
    if 0 <= idx < len(survivors):
        return survivors[idx]
    print("Invalid choice; treating as reject-all.")
    return None


def _pick_episode_and_candidate(feed_arg: str, interactive: bool = True) -> tuple[dict, dict, dict]:
    """Loop episodes until a candidate is approved, or the RSS window is exhausted.

    Returns ``(episode, transcript, chosen_candidate)``. Raises ``RuntimeError``
    when every episode in the RSS window has been used/rejected. A rejected
    episode (empty shortlist, or the human rejects every candidate) is retired
    via ``posted_history.mark_used`` — same permanent-skip treatment as a
    published episode, per the design spec.
    """
    feed_url = config.PODCAST_FEEDS.get(feed_arg, feed_arg)
    host_name = config.PODCAST_HOSTS.get(feed_arg)
    used_guids = set(posted_history.load().keys())

    while True:
        picked = rss_ingest.pick_random_entry(feed_url, exclude_guids=used_guids, host_name=host_name)
        if picked is None:
            raise RuntimeError(
                f"All episodes in the RSS window for '{feed_arg}' have already been used. "
                "Delete tmp/posted_history.json to reset."
            )
        feed_obj, entry, meta = picked
        guid = meta.get("guid", "")
        logger.info("[1/6] Ingest: candidate episode %r (guid=%s)", meta.get("title"), guid)
        episode = rss_ingest.download_latest(feed_obj, entry)
        transcript = _load_or_build_transcript(episode["audio_path"])

        candidate = _find_and_pick_candidate(transcript, interactive=interactive)
        if candidate is None:
            if guid:
                posted_history.mark_used(guid=guid, feed=feed_arg, title=episode.get("title", ""))
                used_guids.add(guid)
            print(f"No candidate approved for {episode.get('title')!r} — trying next episode.")
            continue

        return episode, transcript, candidate
```

- [ ] **Step 2: Rewrite `run()`**

Find the existing `run()` function (lines 203-311, from `def run(` through its closing `return {...}` block). Replace it entirely with:

```python
def run(
    feed_arg: str,
    episode: dict | None = None,
    privacy_status: str = "private",
    render_slides: bool = True,
    interactive: bool = True,
) -> dict:
    """Run ingest -> render -> publish for an episode of ``feed_arg``.

    ``feed_arg`` is a key in ``config.PODCAST_FEEDS`` or a raw RSS URL.

    When ``episode`` is ``None`` (RSS mode — feed runs and ``--auto``), episode
    AND candidate-clip selection are both handled internally by
    :func:`_pick_episode_and_candidate`, looping to another episode whenever a
    candidate shortlist comes up empty or (interactively) every candidate is
    rejected. If Stage 2 copywriting (:func:`ai_extract.extract_copy_with_retry`)
    exhausts its retries for the chosen candidate, the episode is retired and
    the picker runs again for a fresh candidate/episode.

    When ``episode`` is given (``--url`` direct-audio mode: there is only one
    episode, nothing to fall back to), a plan-cache hit short-circuits both
    stages entirely (e.g. a second run of the same URL); on a cache miss, a
    single Stage-1 pick and Stage-2 copy pass are attempted and any failure
    (reject-all, or Stage 2 exhaustion) raises immediately rather than looping.

    ``interactive=False`` (used by ``run_auto``) disables the human prompt and
    auto-picks the top-ranked surviving candidate at each Stage 1 pass.
    ``privacy_status`` is forwarded to YouTube (default ``"private"``).
    ``render_slides=False`` skips the carousel deck entirely (video-only run) —
    ``_publish_stage`` and the summary printer already treat an empty
    ``slides`` list as a no-op. Returns a summary dict.
    """
    podcast_name = _display_name(feed_arg)
    logger.info("Starting pipeline | feed=%s", feed_arg)

    if episode is not None:
        # --url direct-audio mode: exactly one episode, no RSS loop.
        audio_path = episode["audio_path"]
        cache_path = _plan_cache_path(audio_path)
        if os.path.exists(cache_path):
            logger.info("[2-6/6] Plan cache HIT: %s (skipping Groq + Claude)", os.path.basename(cache_path))
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            transcript, highlights = cached["transcript"], cached["highlights"]
        else:
            transcript = _load_or_build_transcript(audio_path)
            logger.info("[3/6] Find + filter clip candidates (Claude %s)", config.EXTRACT_MODEL)
            candidate = _find_and_pick_candidate(transcript, interactive=interactive)
            if candidate is None:
                raise RuntimeError(
                    f"No candidate approved for {episode.get('title')!r}; "
                    "nothing to fall back to in --url mode."
                )
            logger.info("Extracting copy (Claude %s) for approved candidate", config.EXTRACT_MODEL)
            highlights = ai_extract.extract_copy_with_retry(
                transcript, candidate["clip_start"], candidate["clip_end"], candidate,
            )
            _write_plan(cache_path, transcript, highlights)
            logger.info("Cached transcript + plan: %s", os.path.basename(cache_path))
    else:
        # RSS mode (feed runs and --auto): pick episode + candidate, looping on
        # empty shortlists, reject-all, or Stage 2 retry exhaustion.
        while True:
            episode, transcript, candidate = _pick_episode_and_candidate(feed_arg, interactive=interactive)
            audio_path = episode["audio_path"]
            cache_path = _plan_cache_path(audio_path)
            try:
                logger.info("Extracting copy (Claude %s) for approved candidate", config.EXTRACT_MODEL)
                highlights = ai_extract.extract_copy_with_retry(
                    transcript, candidate["clip_start"], candidate["clip_end"], candidate,
                )
            except Exception as exc:  # noqa: BLE001 - Stage 2 exhaustion must fall back, not crash the run
                logger.warning(
                    "Stage 2 copy extraction failed for %r (%s); retiring episode "
                    "and trying another candidate/episode", episode.get("title"), exc,
                )
                guid = episode.get("guid")
                if guid:
                    posted_history.mark_used(guid=guid, feed=feed_arg, title=episode.get("title", ""))
                continue
            _write_plan(cache_path, transcript, highlights)
            logger.info("Cached transcript + plan: %s", os.path.basename(cache_path))
            break

    logger.info("Episode: %s", episode.get("title"))
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Downloaded audio missing: {audio_path}")

    cs, ce = float(highlights["clip_start"]), float(highlights["clip_end"])
    logger.info("Clip window: %.2f-%.2fs (%.1fs)", cs, ce, ce - cs)

    # Retire the episode immediately after the plan is built (RSS mode only —
    # 'manual' --url episodes are never excluded from re-selection since
    # there's no RSS pool to exclude them from) so a YouTube failure later
    # doesn't leave it available for re-selection next run.
    guid = episode.get("guid")
    if guid and feed_arg != "manual":
        posted_history.mark_used(guid=guid, feed=feed_arg, title=episode.get("title", ""))

    # 4) Background selection: Pexels video -> Gemini image -> gradient chain.
    logger.info("[4/6] Backgrounds: select (Pexels -> Gemini -> gradient)")
    backgrounds = background.select_backgrounds(highlights)
    bg_kind = "video" if backgrounds and backgrounds[0].lower().endswith(".mp4") else "image"
    logger.info("Backgrounds: %d %s file(s)", len(backgrounds), bg_kind)

    # 5) Render the karaoke video.
    logger.info("[5/6] Video: render karaoke MP4")
    video_path = video_gen.build_video(
        audio_path,
        transcript["words"],
        highlights,
        podcast_name=podcast_name,
        background_images=backgrounds,
    )

    # 6) Render the static slide deck (hook + 3 insights + quote = 5 PNGs).
    if render_slides:
        logger.info("[6/6] Slides: render deck")
        slides = slide_gen.build_slides(highlights)
    else:
        logger.info("[6/6] Slides: skipped (render_slides=False)")
        slides = []

    # 7) Publish: R2 -> YouTube -> manual reminders (best-effort, never fatal).
    logger.info("[7/7] Publish: R2 + YouTube + manual reminders")
    publish = _publish_stage(
        episode, highlights, video_path, slides, privacy_status=privacy_status
    )

    # 8) Stamp the YouTube URL on the already-retired episode entry (written at
    #    render time above). A YouTube failure is fine — the episode is already
    #    excluded from future random selection regardless.
    if publish.get("youtube_url") and not publish.get("youtube_error"):
        posted_history.record(
            guid=episode.get("guid"),
            feed=feed_arg,
            title=episode.get("title"),
            youtube_url=publish["youtube_url"],
        )

    logger.info("Pipeline complete for: %s", episode.get("title"))
    return {
        "episode": episode,
        "highlights": highlights,
        "clip_start": cs,
        "clip_end": ce,
        "video_path": video_path,
        "slides": slides,
        "publish": publish,
    }
```

- [ ] **Step 3: Rewrite `run_auto()`**

Find the existing `run_auto()` function together with the `_AUTO_MAX_EPISODE_ATTEMPTS = 5` line above it (lines 314-392). Replace both entirely with:

```python
def run_auto(privacy_status: str = "private") -> dict | None:
    """Rotation entry point: pick today's feed and process a random unused episode.

    Resolves today's weekday against ``config.ROTATION`` and:
      * not a posting day            -> log and return None,
      * feed unreachable             -> log and return None,
      * all RSS entries already used -> log and return None,
      * otherwise                    -> run the full pipeline via :func:`run`
                                        with ``interactive=False`` (no human is
                                        present for scheduled runs; the top
                                        surviving candidate is taken
                                        automatically at each Stage 1 pass).

    Episode/candidate selection, filtering, and Stage 2 retry-exhaustion
    fallback are all handled inside :func:`run` (via
    :func:`_pick_episode_and_candidate`) — this function only resolves which
    feed to use today and translates a fully-exhausted RSS window or a feed
    error into a clean ``None`` return instead of a crash.
    """
    weekday = date.today().weekday()
    feed_key = config.ROTATION.get(weekday)
    if feed_key is None:
        logger.info("Auto mode: no posting day today (%s); nothing to do", _WEEKDAY_NAMES[weekday])
        return None

    logger.info("Auto mode: %s -> feed '%s' (random episode)", _WEEKDAY_NAMES[weekday], feed_key)

    try:
        return run(feed_key, privacy_status=privacy_status, interactive=False)
    except RuntimeError as exc:
        logger.info("Auto mode: %s; nothing to do", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - feed/network problems must not break scheduling
        logger.warning("Auto mode: feed '%s' unavailable (%s); exiting cleanly", feed_key, exc)
        return None
```

- [ ] **Step 4: Simplify the CLI `__main__` block**

Find the `else:` branch of the CLI block that currently does its own episode-retry loop (from `else:` at what is currently line 518 through the matching `if result is None: raise RuntimeError(...)` at line 563 — i.e. everything between the `elif args.url:` branch and the `except Exception:` handler). Replace that entire `else:` branch with:

```python
        else:
            result = run(
                args.feed, privacy_status=args.privacy,
                render_slides=not args.no_slides, interactive=True,
            )
```

Also update the `elif args.url:` branch immediately above it (currently lines 509-517) to pass `interactive=True` explicitly, so it reads:

```python
        elif args.url:
            # Direct-URL mode: skip rss_ingest, download the audio, then run the
            # normal pipeline from transcribe onward as the "manual" feed.
            logger.info("Direct URL mode: %s", args.url)
            episode = rss_ingest.fetch_from_url(args.url, title=args.title)
            result = run(
                "manual", episode=episode, privacy_status=args.privacy,
                render_slides=not args.no_slides, interactive=True,
            )
```

- [ ] **Step 5: Verify syntax**

Run: `.\venv\Scripts\python.exe -m py_compile main.py`
Expected: no output, exit code 0.

- [ ] **Step 6: Verify import wiring and CLI parsing**

Run:
```
.\venv\Scripts\python.exe -c "import main; print(main._load_or_build_transcript); print(main._find_and_pick_candidate); print(main._pick_episode_and_candidate); print(hasattr(main, '_load_or_build_plan')); print(hasattr(main, '_AUTO_MAX_EPISODE_ATTEMPTS'))"
```
Expected: prints the three new function objects, then `False`, `False`.

Then verify the CLI still parses (this only exercises `argparse`, no network/API calls, since `--help` exits before any pipeline code runs):
Run: `.\venv\Scripts\python.exe main.py --help`
Expected: prints the usage/help text and exits 0, including the `--no-slides` flag added in a prior session.

- [ ] **Step 7: Commit**

```bash
git add main.py
git commit -m "Add interactive candidate-shortlist picker to main.py; replace blind single-pick extraction"
```

---

### Task 5: `modules/video_gen.py` — update the `__main__` harness's extraction call

**Files:**
- Modify: `modules/video_gen.py:636-651`

**Interfaces:**
- Consumes: Task 3's `ai_extract.find_candidates`, `ai_extract.filter_candidates`, `ai_extract.extract_copy_with_retry`.

- [ ] **Step 1: Replace the cache-miss extraction call**

Find this block (currently lines 635-640):

```python
    else:
        print(f"Transcribing: {os.path.basename(audio_path)}", flush=True)
        transcript = transcribe.transcribe(audio_path)
        highlights = ai_extract.extract_highlights_with_retry(transcript)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"transcript": transcript, "highlights": highlights}, f)
```

Replace it with:

```python
    else:
        print(f"Transcribing: {os.path.basename(audio_path)}", flush=True)
        transcript = transcribe.transcribe(audio_path)
        candidates = ai_extract.find_candidates(transcript)
        survivors = ai_extract.filter_candidates(candidates, transcript)
        if not survivors:
            raise SystemExit("No candidates survived filtering for this episode.")
        top = survivors[0]
        highlights = ai_extract.extract_copy_with_retry(
            transcript, top["clip_start"], top["clip_end"], top,
        )
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"transcript": transcript, "highlights": highlights}, f)
```

- [ ] **Step 2: Replace the stale-cache regeneration call**

Find this block (currently lines 645-651):

```python
    vq = highlights.get("video_queries")
    stale_vq = not vq or not all(isinstance(v, dict) for v in vq)
    if "search_queries" not in highlights or stale_vq:
        print("Cached plan missing/old search_queries/video_queries; regenerating extraction...", flush=True)
        highlights = ai_extract.extract_highlights_with_retry(transcript)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"transcript": transcript, "highlights": highlights}, f)
```

Replace it with:

```python
    vq = highlights.get("video_queries")
    stale_vq = not vq or not all(isinstance(v, dict) for v in vq)
    if "search_queries" not in highlights or stale_vq:
        print("Cached plan missing/old search_queries/video_queries; regenerating copy for the same window...", flush=True)
        highlights = ai_extract.extract_copy_with_retry(
            transcript, highlights["clip_start"], highlights["clip_end"], seed={},
        )
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"transcript": transcript, "highlights": highlights}, f)
```

- [ ] **Step 3: Verify syntax**

Run: `.\venv\Scripts\python.exe -m py_compile modules\video_gen.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Verify no remaining references to the removed API**

Run: `.\venv\Scripts\python.exe -c "import ast,sys; src=open('modules/video_gen.py',encoding='utf-8').read(); sys.exit(1) if 'extract_highlights' in src else print('clean')"`
Expected: prints `clean`.

- [ ] **Step 5: Commit**

```bash
git add modules/video_gen.py
git commit -m "Update video_gen __main__ harness for the two-stage candidate/copy extraction API"
```

---

### Task 6: `CLAUDE.md` — update stale documentation

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- None (documentation only; no code interfaces).

- [ ] **Step 1: Update the pipeline-flow bullet**

Find (in the `### Linear pipeline, config-driven` subsection):

```
2-3. `_load_or_build_plan(audio_path)` — transcribe + extract, cached together as
`tmp/<basename>.plan.json`. Immediately after this step, `posted_history.mark_used()`
retires the episode so it can never be re-selected, even if the YouTube upload
later fails.
```

Replace with:

```
2-3. Two-stage extraction, orchestrated by `main.py`'s `_pick_episode_and_candidate`
/ `_find_and_pick_candidate`: transcribe (cached separately as
`tmp/<basename>.transcript.json`) → `ai_extract.find_candidates` surfaces up to
`config.CANDIDATE_COUNT` (5) ranked clip candidates → `ai_extract.filter_candidates`
snaps each to sentence boundaries and drops content-gate failures → a human
picks one (or rejects all, looping to the next episode) → `ai_extract.extract_copy_with_retry`
writes the full copy/art-direction package for the approved window only, cached
as `tmp/<basename>.plan.json`. `posted_history.mark_used()` retires the episode
once a candidate is approved and Stage 2 succeeds (or immediately, for a
rejected/empty-shortlist episode), so it can never be re-selected, even if the
YouTube upload later fails.
```

- [ ] **Step 2: Update the `ai_extract` data-contract paragraph**

Find:

```
- `ai_extract.extract_highlights(transcript)` → validated JSON dict with exactly these keys:
```

Replace the opening of that bullet (just this line — leave every following paragraph in the same bullet about `_validate`, `_trim_to_cap`, `_snap_to_sentences`, the render-time sentence guard, and query normalization untouched, since those helpers are reused unchanged by the new flow) with:

```
- `ai_extract` now runs a two-stage extraction. Stage 1 — `find_candidates(transcript)`
  → up to `config.CANDIDATE_COUNT` (5) ranked `{clip_start, clip_end, hook, exposes,
  reframe, payoff}` dicts (no copywriting yet); `filter_candidates(candidates, transcript)`
  → survivors only, snapped to sentence boundaries and content-gated. Stage 2 —
  `extract_copy_for_window(transcript, clip_start, clip_end, seed)` (wrapped by
  `extract_copy_with_retry`, same 3-attempt/65s-sleep retry shape) writes the full
  copy for an ALREADY-FIXED window and returns the same schema the old single-pass
  `extract_highlights` used to (now removed) — exactly these keys:
```

- [ ] **Step 3: Update the retry-helper paragraph**

Find the paragraph starting:

```
**Extraction is non-deterministic — re-extracts go through a 3-attempt retry helper.**
```

through its end (`Don't call \`extract_highlights\` directly from a pipeline path — it dies on the first throw.`). Replace the entire paragraph with:

```
**Extraction is non-deterministic — both stages go through retry helpers.** Identical
transcript inputs yield varying output: occasional trailing data after the JSON (the
model appends a note/second object), or off-by-one counts (e.g. 5 `image_prompts`
instead of 4) that trip `_validate`. `_strip_to_json` isolates the **first complete
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
```

- [ ] **Step 4: Update the "Stale plan caches" gotcha**

Find (in the `## Gotchas / current state` section):

```
- **Stale plan caches:** `main.py`'s `_load_or_build_plan` only auto-regenerates
extraction when `search_queries` is missing — it does **not** check for
`video_queries`. A cache written before `video_queries` existed will keep an
older highlights dict; `background.py` handles this by falling back to
`search_queries` for the video search. (The `video_gen` harness *does*
regenerate on a missing `video_queries`.) Delete the `*.plan.json` to force a
clean re-extract.
```

Replace with:

```
- **Stale plan caches:** `main.py`'s `run()` treats a `*.plan.json` hit as final —
it does **not** check the cached highlights for missing/stale fields (that
migration path only exists in the `video_gen` harness, which regenerates Stage 2
copy for the cached window when `search_queries` is missing or `video_queries`
is in the old plain-string form). `background.py` separately falls back to
`search_queries` for the video search on old cached plans that predate
`video_queries` entirely. Delete the `*.plan.json` (and, if you want a fresh
Stage 1 candidate scan too, the matching `*.transcript.json`) to force a clean
re-extract.
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "Update CLAUDE.md for the two-stage candidate-shortlist extraction flow"
```

---

## Self-Review Notes (already applied above; recorded for the reviewer)

- **Spec coverage:** §1 (two prompts) → Task 2 + 3. §2 (new/changed `ai_extract` functions) → Task 2 + 3, with `extract_highlights`/`extract_highlights_with_retry` removal in Task 3. §3 (interactive loop in `main.py`, `_load_or_build_plan` split) → Task 4. §4 (gate/retry behavior, including Stage 2 exhaustion fallback) → Task 4's `run()` while-loop. §5 (`--auto`/`--url` handling) → Task 4's `interactive` param threading. §6 (testing/harness update) → Task 3 Step 3. Additional call sites the spec didn't enumerate but that would otherwise break the build (`modules/video_gen.py`, `scripts/verify_prompt.py`) → Task 5 and Task 3 Step 4 respectively. Stale docs → Task 6.
- **Type consistency:** candidate dict shape `{clip_start, clip_end, hook, exposes, reframe, payoff}` is identical across `CANDIDATE_SYSTEM_PROMPT`'s schema, `find_candidates`, `filter_candidates`, `_find_and_pick_candidate`, `_print_candidates`, and `extract_copy_for_window`'s `seed` parameter usage. `extract_copy_for_window`/`extract_copy_with_retry` signatures match between their Task 3 definition and every Task 4/5 call site.
