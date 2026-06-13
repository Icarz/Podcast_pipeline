"""AI extraction: turn a transcript into a structured clip plan via Claude.

Sends the transcript text to the Claude Messages API with a system prompt that
constrains the model to emit ONLY valid JSON, then parses and validates it.
"""

import json
import logging
import os
import re

from anthropic import Anthropic
from dotenv import load_dotenv

import config

load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a podcast clip producer. You read a full episode transcript and "
    "select the single best short-form clip plus social copy.\n\n"
    "You MUST respond with ONLY a single valid JSON object and nothing else — "
    "no markdown, no code fences, no commentary before or after.\n\n"
    "CLIP SELECTION RULES:\n"
    "1. Pick a clip containing a UNIVERSAL INSIGHT or PRINCIPLE — "
    "something true for any listener regardless of who is speaking.\n"
    "2. REJECT clips that are primarily: personal career stories, "
    "event-specific narratives (sold out a venue, got signed, met "
    "someone), name-dropping, or entertainment anecdotes.\n"
    "3. BRAND CHECK: the clip MUST relate to at least one of: "
    "discipline, focus, mindset, stoicism, neuroscience, habit "
    "formation, emotional regulation, identity, resilience, or "
    "self-belief as a universal principle. If the best clip fails "
    "this check, pick the next best clip that passes it.\n\n"
    "HOOK RULES — these determine 90% of whether the clip gets views.\n\n"
    "The hook MUST use a CONTRARIAN IDENTITY FRAME. It must challenge the viewer's "
    "current behavior or worldview and imply they are on the wrong side of a divide. "
    "The viewer should feel: 'wait — am I doing this wrong?'\n\n"
    "WINNING FORMULA (use one of these structures every time):\n"
    "  - 'You're [doing common thing] and it's [unexpected negative consequence]'\n"
    "  - '[Common belief] is a lie — here's what [wise/successful people] actually do'\n"
    "  - 'Every [person/situation] has [two sides] — [one destroys], [one elevates]'\n"
    "  - 'The world is [broken in specific way] — and [most people/you] are [complicit/unaware]'\n"
    "  - '[Uncomfortable truth] that nobody wants to hear'\n"
    "  - 'Stop [common behavior] — it's [destroying/weakening] [something you value]'\n\n"
    "BANNED HOOK PATTERNS (these get 2-4 views, proven by data):\n"
    "  - 'X tips/tricks/hacks for Y' — instructional, zero identity tension\n"
    "  - 'How to [achieve thing]' — promises information, not transformation\n"
    "  - 'The science behind X' — educational frame, audience scrolls past\n"
    "  - 'Why X happens' — explanatory, no stakes\n"
    "  - 'X things you didn't know about Y' — listicle, no emotional charge\n"
    "  - Any hook that could be a YouTube tutorial title\n\n"
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
    "The JSON object must have exactly these keys:\n"
    '  "hook"        : string — a contrarian identity-frame hook, under 15 words. See HOOK RULES above.\n'
    '  "insights"    : array of exactly 3 strings — the key takeaways. Each '
    "insight MUST be <= 100 characters total. Hard cap, no exceptions. Write them as "
    "IDENTITY STATEMENTS, not explanations — punchier is better:\n"
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
    '  "clip_start"  : number — MUST be the exact start timestamp of one of the '
    "segments in the provided list, AND must fall at the BEGINNING of a complete "
    "thought (the start of a sentence or idea), never mid-sentence.\n"
    '  "clip_end"    : number — MUST be the exact end timestamp of a LATER segment '
    "in the list, AND must fall at the END of a complete thought or conclusion. "
    "The clip MUST contain a full, self-contained idea WITH its payoff. NEVER cut "
    "off mid-sentence, and NEVER end on a setup/cliffhanger such as \"...this is "
    "what I want you to do\" or \"...here's the thing\" without the actual point "
    "that follows. The last segment must deliver the resolution, not tee it up.\n"
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
    "  - Crowds, stadiums, conferences, presentations to audiences, or any "
    "group/event setting.\n"
    "  - Faces of identifiable people (close-up portraits where the person is "
    "the subject).\n"
    "  - Person lying in bed, or intimate/sensual positioning.\n"
    "  - Traffic, cars, busy intersections, or urban infrastructure.\n"
    "  - Flowers, food styling, or Pinterest-aesthetic flat-lay arrangements.\n\n"
    "SEARCH_QUERIES — exactly 5 Pexels PHOTO search queries, one per slide IN "
    "ORDER:\n"
    "  [0] cover — visual that matches the hook's energy and stakes.\n"
    "  [1] insight 1 — matches the specific EMOTIONAL STATE of that insight, NOT "
    "its topic. Ask: what would a person FEEL in this moment? Find a scene where a "
    "real human is in that state.\n"
    "  [2] insight 2 — same rule: emotion first, then scene.\n"
    "  [3] insight 3 — same rule.\n"
    "  [4] quote — evokes the tone and stakes of the quote itself.\n"
    "COVER SLIDE PRIORITY: search_queries[0] is the MOST IMPORTANT — it becomes "
    "the Instagram grid thumbnail. It MUST be visually dramatic at 1:1 crop: high "
    "contrast, clear subject, no busy detail. Default to TIER 1 scenes (lone figure "
    "against vast landscape, silhouette at sunrise/sunset) unless the content "
    "specifically demands otherwise. Dramatic solitary landscape outperforms warm "
    "interior by 14x on Instagram grid — bias the cover hard toward TIER 1.\n"
    "Rules for every query:\n"
    "  - Describe a REAL SCENE a stock photographer actually shot (person walking "
    "foggy path, man looking at city from rooftop, runner at dawn, hands writing "
    "in notebook — specific and physical).\n"
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
    "VIDEO_QUERIES — you are a FILM EDITOR choosing B-roll for the chosen clip. "
    "Produce EXACTLY 5 beats: 4 primary beats that map IN ORDER to the 4 quarters "
    "of the clip window, PLUS a 5th SPARE backup (distinct scene, same tone) used "
    "only if a primary clip can't be sourced.\n\n"
    "MANDATORY PALETTE: Before writing any queries, choose ONE palette for all 4 slots:\n"
    "  - DRAMATIC-NATURAL: lone figure against vast landscape, dawn/dusk/storm light, "
    "high contrast. THIS IS THE HIGHEST PERFORMING PALETTE — default to this unless "
    "the content specifically demands interior.\n"
    "  - COOL-CINEMATIC: urban night, rain, blue hour, moody city. Use for "
    "modern-life or culture-critique content.\n"
    "  - WARM-INTERIOR: lamplit study, fireplace, morning window light. Use ONLY for "
    "contemplation/reading/writing content.\n"
    "State your chosen palette in your reasoning before writing queries.\n\n"
    "SCENE PRIORITY (ranked by proven performance):\n"
    "TIER 1 — USE THESE BY DEFAULT:\n"
    "  - Lone figure walking away on a dramatic path (mountain, forest, snow, fog)\n"
    "  - Silhouette against vast sky at sunrise/sunset\n"
    "  - Person standing at the edge of something vast (cliff, ocean, rooftop)\n"
    "  - Figure walking through morning mist or rain\n"
    "TIER 2 — USE WHEN TIER 1 DOESN'T FIT:\n"
    "  - Hands writing in a journal in warm light\n"
    "  - Person reading by a window with natural light\n"
    "  - Runner on a forest trail or mountain path (from behind, not face)\n"
    "TIER 3 — AVOID UNLESS EXPLICITLY REQUIRED:\n"
    "  - Coffee shops, kitchens, dining tables\n"
    "  - Office/desk/laptop scenes\n"
    "  - Flat-lay arrangements\n"
    "  - Any interior that reads as 'lifestyle blog'\n\n"
    "RULES:\n"
    "1. Each query MUST describe a concrete, filmable human scene. Prefer TIER 1 "
    "or TIER 2 scenes above. Map each beat to what's actually being discussed in "
    "that quarter of the clip — the scene subject comes from the content, the "
    "mood/lighting comes from the chosen palette.\n"
    '2. Do NOT use proper nouns, brand names, or place names. Generic settings are '
    "fine (a cliff, a forest trail, a rooftop, a river at dawn).\n"
    "3. TONAL CONSISTENCY: All 4 primary beats + the spare must share the chosen "
    "palette — same season, lighting direction, and color temperature. APPEND the "
    "palette's treatment word to EVERY query (e.g. 'golden hour', 'blue hour', "
    "'warm lamplight'). Do NOT pick a scene whose real-world light fights the "
    "chosen palette — a fog/rain/night scene cannot be saved by adding 'golden "
    "hour' and is forbidden under DRAMATIC-NATURAL. All 5 keywords distinct, all "
    "5 queries distinct.\n"
    "4. ALWAYS depict the ASPIRATIONAL version. When words describe a negative "
    "(fear, laziness, giving up), film the POSITIVE counterpart (persevering, "
    "moving forward, rising) — NEVER the failure-state, never a defeated or "
    "slumped subject.\n"
    "5. Keep subjects CALM and INWARD, never performative. No one grinning at "
    "camera, gesturing theatrically, presenting, or mid-laugh. Quiet determination.\n"
    "6. AVOID legible on-screen TEXT — no whiteboards, captioned screens, or "
    "signage. Captions are burned in; background text competes with them.\n"
    "7. The 'keyword' must name an ASPIRATIONAL or neutral emotional state — "
    "NEVER negative keywords (overwhelm, fear, anxiety, defeat, exhaustion). "
    "Use the counterpart: clarity, momentum, stillness, resolve, conviction.\n"
    "8. SAFE-SCENE FALLBACK: For abstract concepts (neuroplasticity, identity, "
    "meaning) with no filmable scene, use: lone runner on a forest path, hands "
    "writing in a notebook, figure in morning mist, sunrise from a high vantage, "
    "slow aerial over mountains or coast. Pick the closest in mood.\n"
    '9. Format: {"keyword": "one emotion word", "query": "filmable scene treatment"}\n'
    "Example (DRAMATIC-NATURAL palette — all beats at dawn/golden hour):\n"
    '[{"keyword": "resolve", "query": "lone figure mountain trail golden hour"}, '
    '{"keyword": "freedom", "query": "silhouette cliff edge sunset"}, '
    '{"keyword": "momentum", "query": "runner forest path dawn light"}, '
    '{"keyword": "stillness", "query": "person rooftop city sunrise"}, '
    '{"keyword": "clarity", "query": "figure walking mist forest morning"}]\n\n'
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
    "limit to capture a longer passage.\n\n"
    "The transcript is given as timestamped segments, one per line, formatted "
    "[start-end] text. Choose a contiguous run of segments that forms a "
    "self-contained, compelling moment, and set clip_start to that run's first "
    "segment start and clip_end to its last segment end. Do NOT invent "
    "timestamps — only use values that appear in the list."
)


def _client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (check your .env file)")
    return Anthropic(api_key=api_key)


def _strip_to_json(text: str) -> str:
    """Strip markdown fences / surrounding prose to isolate the JSON object."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ``` fences if present.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Isolate the first complete JSON object: skip any leading prose to the first
    # '{', then use raw_decode so trailing data AFTER the closing brace (the model
    # sometimes appends a note/second object) doesn't trigger a "Extra data" error.
    start = text.find("{")
    if start != -1:
        try:
            obj, end = json.JSONDecoder().raw_decode(text, start)
            return json.dumps(obj)
        except json.JSONDecodeError:
            # Couldn't cleanly decode from the first '{' — fall back to the
            # outermost {...} span and let the caller's json.loads report.
            last = text.rfind("}")
            if last > start:
                text = text[start : last + 1]
    return text


def _normalize_query_list(data: dict, key: str, target: int) -> None:
    """Coerce ``data[key]`` to EXACTLY ``target`` non-empty query strings (in place).

    Drops blanks and extras (truncate) and pads by cycling the real queries when
    the model returns too few. Logs a warning whenever it adjusts the count so a
    stray count never crashes the pipeline. Only raises if there is nothing at
    all to derive a list from.
    """
    queries = [q.strip() for q in data[key] if isinstance(q, str) and q.strip()]
    if not queries:
        raise ValueError(f"{key!r} must contain at least one non-empty string")
    if len(queries) != target:
        logger.warning(
            "%s count %d != %d; normalizing to %d (truncating extras / "
            "duplicating to fill)",
            key, len(queries), target, target,
        )
        if len(queries) > target:
            queries = queries[:target]
        else:
            base = list(queries)
            i = 0
            while len(queries) < target:
                queries.append(base[i % len(base)])
                i += 1
    data[key] = queries


def _normalize_video_queries(data: dict, target: int) -> None:
    """Coerce ``data['video_queries']`` to EXACTLY ``target`` {keyword, query} objects.

    Each item is normalized to ``{"keyword": str, "query": str}``. Accepts a bare
    string from a model that ignored the object format (keyword left blank).
    Drops entries with an empty query, then de-duplicates so all keywords are
    distinct AND all queries are distinct (case-insensitive, first wins). Extras
    are truncated. If de-dup leaves fewer than ``target``, pads by cycling the
    surviving objects (last resort) — the Pexels-level dedup in pexels_bg still
    keeps the actual CLIPS distinct even when two queries coincide. Warns on any
    adjustment; only raises if there is nothing usable at all.
    """
    raw = data.get("video_queries") or []

    cleaned: list[dict] = []
    seen_keywords: set[str] = set()
    seen_queries: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            keyword = str(item.get("keyword") or "").strip()
            query = str(item.get("query") or "").strip()
        elif isinstance(item, str):
            keyword, query = "", item.strip()
        else:
            continue
        if not query:
            continue
        # Distinct keywords AND distinct queries (case-insensitive).
        qk = query.lower()
        kk = keyword.lower()
        if qk in seen_queries or (kk and kk in seen_keywords):
            continue
        seen_queries.add(qk)
        if kk:
            seen_keywords.add(kk)
        cleaned.append({"keyword": keyword, "query": query})

    if not cleaned:
        raise ValueError("'video_queries' must contain at least one usable {keyword, query}")

    if len(cleaned) != target:
        logger.warning(
            "video_queries count %d != %d after de-dup; normalizing to %d "
            "(truncating extras / cycling to fill — clips stay distinct via Pexels dedup)",
            len(cleaned), target, target,
        )
        if len(cleaned) > target:
            cleaned = cleaned[:target]
        else:
            base = list(cleaned)
            i = 0
            while len(cleaned) < target:
                cleaned.append(base[i % len(base)])
                i += 1

    data["video_queries"] = cleaned


def _validate(data: dict) -> None:
    """Raise ValueError if ``data`` doesn't match the required schema."""
    required = {
        "hook": str,
        "insights": list,
        "best_quote": str,
        "title": str,
        "clip_start": (int, float),
        "clip_end": (int, float),
        "hashtags": list,
        "image_prompts": list,
        "search_queries": list,
        "video_queries": list,
    }
    for key, expected_type in required.items():
        if key not in data:
            raise ValueError(f"Missing required key: {key!r}")
        if not isinstance(data[key], expected_type):
            raise ValueError(
                f"Key {key!r} has wrong type: expected {expected_type}, "
                f"got {type(data[key]).__name__}"
            )

    if len(data["insights"]) != 3:
        raise ValueError(f"'insights' must have exactly 3 items, got {len(data['insights'])}")

    if len(data["image_prompts"]) != config.IMAGE_PROMPT_COUNT:
        raise ValueError(
            f"'image_prompts' must have exactly {config.IMAGE_PROMPT_COUNT} items, "
            f"got {len(data['image_prompts'])}"
        )
    if not all(isinstance(p, str) and p.strip() for p in data["image_prompts"]):
        raise ValueError("'image_prompts' must be non-empty strings")

    # Normalize the query lists to their EXACT expected counts. Each downstream
    # consumer needs a fixed count (slides = one photo query per slide; the video
    # = one stock-video query per background slot), but a stray count from the
    # model must never crash the pipeline: drop blanks/extras, and pad by cycling
    # the real queries if too few.
    _normalize_query_list(data, "search_queries", config.SEARCH_QUERY_COUNT)
    _normalize_video_queries(data, config.VIDEO_QUERY_EXTRACT_COUNT)

    window = data["clip_end"] - data["clip_start"]
    lo, hi = config.CLIP_WINDOW_MIN_SECONDS, config.CLIP_WINDOW_MAX_HARD_SECONDS
    if not (lo <= window <= hi):
        raise ValueError(
            f"clip window {window:.1f}s outside allowed range [{lo}, {hi}]s "
            f"(start={data['clip_start']}, end={data['clip_end']})"
        )


_SENTENCE_END = (".", "!", "?")


def _ends_sentence(word: str) -> bool:
    """True if a word token ends a sentence (terminal punctuation, ignoring
    any trailing quote/bracket characters)."""
    return word.rstrip("\"')]}").endswith(_SENTENCE_END)


def _trim_to_cap(data: dict, words: list) -> None:
    """Shorten an over-long clip to a sentence boundary within the hard cap.

    The model sometimes insists on a single complete thought that runs a few
    seconds past ``CLIP_WINDOW_MAX_HARD_SECONDS`` (it is deterministic, so simply
    re-asking returns the same pick). Rather than reject it, pull ``clip_end``
    back to the LATEST sentence-ending word that still fits under the cap — keeping
    the clip >= the min window when possible so it ends on a complete sentence
    instead of mid-thought. No-op when the clip already fits.
    """
    cs, ce = data.get("clip_start"), data.get("clip_end")
    if not isinstance(cs, (int, float)) or not isinstance(ce, (int, float)):
        return
    cap = config.CLIP_WINDOW_MAX_HARD_SECONDS
    if ce - cs <= cap:
        return

    ws = [
        w for w in words
        if w.get("start") is not None and w.get("end") is not None and (w.get("word") or "").strip()
    ]
    limit = cs + cap
    floor = cs + config.CLIP_WINDOW_MIN_SECONDS
    sentence_ends = [w["end"] for w in ws if _ends_sentence(w["word"]) and cs < w["end"] <= limit]
    # Prefer a sentence end at/above the min window; else the latest that fits;
    # else any word end under the cap; else a hard cut at the cap.
    in_band = [t for t in sentence_ends if t >= floor]
    if in_band:
        new_end = max(in_band)
    elif sentence_ends:
        new_end = max(sentence_ends)
    else:
        word_ends = [w["end"] for w in ws if cs < w["end"] <= limit]
        new_end = max(word_ends) if word_ends else limit

    logger.warning(
        "Clip window %.1fs exceeds cap %ds; trimming clip_end %.2f -> %.2f (now %.1fs)",
        ce - cs, cap, ce, new_end, new_end - cs,
    )
    data["clip_end"] = new_end


def _extend_to_floor(highlights: dict, words: list[dict], segments: list[dict]) -> dict:
    """Symmetric to ``_trim_to_cap``: rescue a clip that is TOO SHORT.

    If the model picked a window under ``CLIP_WINDOW_MIN_SECONDS``, push
    ``clip_end`` forward to the next sentence-ending word timestamp that brings
    the window up to at least the floor (so the clip ends on a complete thought,
    not mid-sentence). If no sentence boundary clears the floor within
    ``CLIP_WINDOW_MAX_HARD_SECONDS``, fall back to the last sentence boundary
    under the ceiling, then to the last segment end under the ceiling. No-op when
    the clip already meets the floor. Mutates and returns ``highlights``.
    """
    start = highlights["clip_start"]
    end = highlights["clip_end"]

    if (end - start) >= config.CLIP_WINDOW_MIN_SECONDS:
        return highlights  # nothing to do

    floor_target = start + config.CLIP_WINDOW_MIN_SECONDS
    hard_ceiling = start + config.CLIP_WINDOW_MAX_HARD_SECONDS

    # Sentence-ending words AFTER the current clip_end, within the hard ceiling.
    sentence_ends = [
        w["end"] for w in words
        if w["end"] > end
        and w["end"] <= hard_ceiling
        and w["word"].strip().rstrip("\"'").endswith((".", "!", "?"))
    ]

    # Prefer the FIRST sentence boundary that clears the floor.
    candidates = [t for t in sentence_ends if t >= floor_target]
    if candidates:
        highlights["clip_end"] = candidates[0]
    elif sentence_ends:
        # No boundary clears the floor — take the last one under the ceiling.
        highlights["clip_end"] = sentence_ends[-1]
    else:
        # No sentence boundaries at all — push to the last segment end under cap.
        seg_ends = [s["end"] for s in segments if s["end"] > end and s["end"] <= hard_ceiling]
        if seg_ends:
            highlights["clip_end"] = seg_ends[-1]

    if highlights["clip_end"] != end:
        logger.warning(
            "Clip window %.1fs under floor %ds; extending clip_end %.2f -> %.2f (now %.1fs)",
            end - start, config.CLIP_WINDOW_MIN_SECONDS, end,
            highlights["clip_end"], highlights["clip_end"] - start,
        )
    return highlights


def _snap_to_sentences(data: dict, words: list) -> None:
    """Snap clip_start/clip_end onto real sentence boundaries (in place).

    Groq Whisper word tokens carry punctuation, so we snap to word timestamps at
    sentence edges rather than to coarse Whisper *segment* edges (which routinely
    fall mid-sentence and would leave the clip ending on a cliffhanger).

    clip_start -> the start time of the nearest sentence-opening word.
    clip_end   -> the end time of the nearest sentence-closing word.

    Because both targets are word boundaries, the clip never cuts a word in half;
    because they are sentence edges, it never opens or ends mid-thought.
    """
    ws = [
        w for w in words
        if w.get("start") is not None and w.get("end") is not None and (w.get("word") or "").strip()
    ]
    if not ws:
        return

    # End times of words that close a sentence.
    end_times = [w["end"] for w in ws if _ends_sentence(w["word"])]
    # Start times of words that open a sentence (first word, or after a closer).
    start_times = [ws[0]["start"]] + [
        ws[i]["start"] for i in range(1, len(ws)) if _ends_sentence(ws[i - 1]["word"])
    ]
    if not end_times or not start_times:
        return

    cs, ce = data["clip_start"], data["clip_end"]
    new_start = min(start_times, key=lambda t: abs(t - cs))
    # clip_end snaps BACKWARD only: take the LATEST sentence-ending word at or
    # before ce (+0.30s grace), so the clip ends on the last complete sentence and
    # never extends forward into the next one (a forward snap was producing the
    # "...freedom. It's—" mid-sentence cut). Fall back to nearest-boundary only
    # when no sentence ends at/before ce + 0.30s.
    back_ends = [t for t in end_times if t <= ce + 0.30]
    if back_ends:
        new_end = max(back_ends)
        if new_end != ce:
            logger.info(
                "Snapped clip_end back to last sentence boundary: %.2f -> %.2f", ce, new_end
            )
    else:
        new_end = min(end_times, key=lambda t: abs(t - ce))

    # Guard against a degenerate snap (end at/before start): fall back to raw end.
    if new_end <= new_start:
        new_end = ce

    if (new_start, new_end) != (cs, ce):
        logger.info(
            "Snapped clip to sentence boundaries: %.2f-%.2f -> %.2f-%.2f",
            cs, ce, new_start, new_end,
        )
    data["clip_start"], data["clip_end"] = new_start, new_end


def _format_segments(segments: list) -> str:
    """Render segments as grounded, timestamped lines: ``[start-end] text``."""
    lines = []
    for s in segments:
        start, end, text = s.get("start"), s.get("end"), (s.get("text") or "").strip()
        if start is None or end is None or not text:
            continue
        lines.append(f"[{start:.2f}-{end:.2f}] {text}")
    return "\n".join(lines)


def extract_highlights(transcript: dict) -> dict:
    """Extract a structured clip plan from a ``transcript`` dict.

    Uses ``transcript['segments']`` (with real start/end times) so the model
    grounds clip_start/clip_end in actual timestamps. Falls back to plain
    ``transcript['text']`` only if no segments are present.

    Returns the validated JSON object as a dict.
    """
    segments = transcript.get("segments") if isinstance(transcript, dict) else None
    if segments:
        body = "Here is the episode transcript as timestamped segments:\n\n" + _format_segments(segments)
    else:
        text = transcript.get("text", "") if isinstance(transcript, dict) else str(transcript)
        if not text.strip():
            raise ValueError("Transcript has no segments or text to analyze")
        body = f"Here is the episode transcript:\n\n{text}"

    logger.info("Extracting highlights via %s (%d segments)", config.EXTRACT_MODEL, len(segments or []))
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

    # Rescue an out-of-band pick at sentence boundaries BEFORE validation, so a
    # deterministic too-short / too-long thought is adjusted rather than rejected
    # (re-asking returns the same pick). Order matters: extend a too-short clip up
    # to the floor first, THEN cap a too-long one back under the ceiling (extending
    # can itself create an over-length window).
    words = transcript.get("words") if isinstance(transcript, dict) else None
    if words:
        _extend_to_floor(parsed, words, segments or [])
        _trim_to_cap(parsed, words)
    _validate(parsed)

    # Snap onto real sentence boundaries so the clip never cuts a word in half
    # and never opens/ends mid-thought, then re-extend/re-cap: snapping clip_start
    # later can drop the window back under the floor, and snapping it earlier can
    # nudge it back over the ceiling.
    if words:
        _snap_to_sentences(parsed, words)
        _extend_to_floor(parsed, words, segments or [])
        _trim_to_cap(parsed, words)
        window = parsed["clip_end"] - parsed["clip_start"]
        if window > config.CLIP_WINDOW_MAX_HARD_SECONDS:
            logger.warning(
                "Snapped clip window %.1fs exceeds hard max %ds",
                window, config.CLIP_WINDOW_MAX_HARD_SECONDS,
            )

    logger.info("Extracted clip: %.1f-%.1fs | title=%r", parsed["clip_start"], parsed["clip_end"], parsed["title"])
    return parsed


def extract_highlights_with_retry(transcript: dict, attempts: int = 3) -> dict:
    """Call :func:`extract_highlights` up to ``attempts`` times, tolerating the
    model's non-deterministic output.

    Extraction is NOT deterministic: the same transcript can yield an off-by-one
    count (e.g. 5 ``image_prompts`` instead of 4) or stray trailing data that trips
    ``_validate``/``json.loads`` (both raise ``ValueError``). Rather than die on the
    first throw, retry a few times — a later attempt almost always passes. Only
    ``ValueError`` is caught (schema/parse variance); transport/API errors propagate
    immediately. Re-raises the last ``ValueError`` after the final attempt fails.
    """
    last_exc: ValueError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return extract_highlights(transcript)
        except ValueError as exc:
            last_exc = exc
            logger.warning(
                "extract_highlights attempt %d/%d failed (non-deterministic): %s",
                attempt, attempts, exc,
            )
    assert last_exc is not None
    raise last_exc


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

    result = extract_highlights(transcript)

    print("\n=== Extracted clip plan ===")
    print(json.dumps(result, indent=2, ensure_ascii=False).encode("ascii", "replace").decode("ascii"))
    print(f"\nClip window: {result['clip_end'] - result['clip_start']:.1f}s (valid)")
