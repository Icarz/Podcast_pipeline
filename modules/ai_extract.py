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
    "The JSON object must have exactly these keys:\n"
    '  "hook"        : string — a scroll-stopping one-line hook for the clip.\n'
    '  "insights"    : array of exactly 3 strings — the key takeaways.\n'
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
    "SEARCH_QUERIES — exactly 5 Pexels PHOTO search queries, one per slide IN "
    "ORDER:\n"
    "  [0] cover — visual that matches the hook's energy and stakes.\n"
    "  [1] insight 1 — matches the specific EMOTIONAL STATE of that insight, NOT "
    "its topic. Ask: what would a person FEEL in this moment? Find a scene where a "
    "real human is in that state.\n"
    "  [2] insight 2 — same rule: emotion first, then scene.\n"
    "  [3] insight 3 — same rule.\n"
    "  [4] quote — evokes the tone and stakes of the quote itself.\n"
    "Rules for every query:\n"
    "  - Describe a REAL SCENE a stock photographer actually shot (person walking "
    "foggy path, man looking at city from rooftop, runner at dawn, hands writing "
    "in notebook — specific and physical).\n"
    "  - Do NOT use abstract concepts as queries (no \"success\", \"ambition\", "
    "\"clarity\", \"mindset\" — these return generic stock photos).\n"
    "  - 2-4 words max, portrait-friendly composition preferred.\n"
    "  - All 5 visually distinct — no two should return the same type of scene.\n\n"
    "VIDEO_QUERIES — think like a FILM EDITOR choosing B-roll for the chosen "
    "clip, NOT like a photo picker. Produce EXACTLY 5 beats: 4 that together form "
    "ONE coherent moving backdrop behind the clip, PLUS a 5th SPARE backup beat "
    "(same palette/energy, yet a distinct scene from the other 4) kept in reserve "
    "as a fallback. For EACH beat, work in TWO steps:\n"
    "  STEP 1 — KEYWORD: read the ACTUAL spoken words inside the clip window "
    "(clip_start..clip_end), split it into 4 emotional beats (opening, two middle "
    "beats, payoff), and for each beat name ONE core CONCEPT KEYWORD that captures "
    "its emotion — a single word such as wisdom, focus, calm, solitude, "
    "discipline, clarity, stillness, resilience, overwhelm, or freedom. This "
    "keyword is the emotional anchor.\n"
    "  STEP 2 — QUERY: build a short (2-4 word) cinematic portrait stock-VIDEO "
    "search query ANCHORED on that keyword — a concrete, filmable scene that "
    "embodies it. e.g. keyword \"focus\" -> \"person reading quiet room\"; "
    'keyword "solitude" -> "lone figure misty trail"; keyword "freedom" -> '
    '"open road sunrise drive".\n'
    "Rules for the queries: STRONGLY favor scenes with natural movement that loop "
    "and crossfade smoothly — walking figures and silhouettes, nature in motion "
    "(flowing water, drifting fog, trees in wind, waves, rain on glass), city "
    "movement (traffic, crowds, trains, light trails), slow aerial / landscape "
    "flyovers, and hands doing things (writing, brewing coffee, working). AVOID "
    "static concepts that only exist as still photos (logos, charts, posed "
    "headshots, a single object on a table). The captions already carry the "
    "literal words, so match the overall MOOD, not the dictionary meaning. All 5 "
    "KEYWORDS must be DISTINCT and all 5 QUERIES must be DISTINCT scenes, yet "
    "TONALLY CONSISTENT with one another (same time of day / palette / energy) so "
    "they crossfade as one continuous piece — do NOT mix a bright beach with a "
    "dark city. Favor vertical-friendly compositions.\n"
    "VIDEO QUERY RULES:\n"
    "1. NEVER use literal nouns or proper nouns from the clip content.\n"
    "   - content mentions stadium -> query 'person alone vast empty space'\n"
    "   - content mentions city -> query 'lone figure urban dawn'\n"
    "   - content mentions success -> query 'person confident morning light'\n"
    "   - content mentions fear -> query 'person sitting alone in silence'\n"
    "2. Each query must describe a CINEMATIC SCENE a stock photographer "
    "would shoot — not a concept, not an event, not a place name.\n"
    "3. The scene must feel like ELEVATION — upward motion, expansive "
    "space, solitary focus, dawn light, quiet determination. Never "
    "crowds, chaos, sports events, or celebrations.\n"
    "4. Format: {\"keyword\": \"one emotion word\", \"query\": "
    "\"filmable scene description\"}\n"
    "Example for a calm clip about inner peace (4 primary + 1 spare): "
    '[{"keyword": "stillness", "query": "misty forest morning"}, '
    '{"keyword": "calm", "query": "slow river flowing"}, '
    '{"keyword": "clarity", "query": "fog drifting mountains"}, '
    '{"keyword": "solitude", "query": "person walking trail"}, '
    '{"keyword": "serenity", "query": "lone boat still lake"}].\n\n'
    "Clip length: aim for a window of "
    f"{config.CLIP_WINDOW_MIN_SECONDS}-{config.CLIP_WINDOW_MAX_SECONDS} seconds. "
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
    # Fallback: grab the outermost {...} span.
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
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

    # Trim an over-long pick to a sentence boundary within the cap BEFORE
    # validation, so a deterministic too-long-by-a-few-seconds thought is
    # shortened rather than rejected (re-asking returns the same pick).
    words = transcript.get("words") if isinstance(transcript, dict) else None
    if words:
        _trim_to_cap(parsed, words)
    _validate(parsed)

    # Snap onto real sentence boundaries so the clip never cuts a word in half
    # and never opens/ends mid-thought, then re-cap (snapping clip_start earlier
    # can nudge the window back over the limit).
    if words:
        _snap_to_sentences(parsed, words)
        _trim_to_cap(parsed, words)
        window = parsed["clip_end"] - parsed["clip_start"]
        if window > config.CLIP_WINDOW_MAX_HARD_SECONDS:
            logger.warning(
                "Snapped clip window %.1fs exceeds hard max %ds",
                window, config.CLIP_WINDOW_MAX_HARD_SECONDS,
            )

    logger.info("Extracted clip: %.1f-%.1fs | title=%r", parsed["clip_start"], parsed["clip_end"], parsed["title"])
    return parsed


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
