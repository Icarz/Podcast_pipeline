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
    "topic within it. Treat all five clusters as equally valid -- pick "
    "whichever has the strongest angle for this run: fear_anxiety_rumination "
    "(fear/anxiety/rumination mechanisms), individuality_vs_conformity "
    "(the courage to want more than the crowd finds acceptable), "
    "money_as_freedom (money reframed as freedom/identity, never budgeting "
    "advice), neurology_focus_motivation (brain/nervous-system mechanics, "
    "focus, motivation), and identity_resilience_meaning (identity, "
    "resilience, meaning/purpose). Do NOT pick the same topic_cluster as the "
    "most recently used one. Never write a hook or title that substantially repeats a recent "
    "one -- pick a genuinely different angle.\n\n"
    "HOOK RULES -- these determine 90% of whether the clip gets views.\n\n"
    "The hook MUST use either a CONTRARIAN IDENTITY FRAME (it challenges the "
    "viewer's current behavior or worldview and implies they are on the "
    "wrong side of a divide -- 'wait, am I doing this wrong?') or a "
    "SELF-CATEGORIZATION FRAME (it forces the viewer to sort themselves into "
    "one of two sharply contrasting types -- 'wait, which one am I?'). Both "
    "must make the viewer feel personally implicated within the first "
    "sentence.\n\n"
    "WINNING FORMULA (use one of these structures):\n"
    "  - 'Your [brain/nervous system/body] [won't let go of/is addicted to/"
    "is hijacking you with] [common behavior] -- [until/unless] [condition]' "
    "-- DEFAULT / HIGHEST RETENTION. Prioritize this whenever the script "
    "reveals a psychological/neurological mechanism acting on the viewer "
    "without their awareness. The mechanism must be immediately, viscerally "
    "felt -- not abstract -- so the payoff lands with almost no drop-off.\n"
    "  - SELF-CATEGORIZATION TEST: '[Type A] or [Type B] -- which one are "
    "you?' / 'Are you actually [Type A], or just [Type B]?'. Splits the "
    "topic into two sharply contrasting patterns of mind or behavior (never "
    "two people, never a relationship pairing) and forces the viewer to "
    "self-sort. Both types must be instantly recognizable from a single "
    "clause each -- the viewer must know which one they are within the "
    "hook, not wait for the payoff. Use whenever the topic naturally splits "
    "into two contrasting patterns; this is a proven top-tier hook mechanic "
    "(self-categorization has driven 10x the engagement of straight advice "
    "in comparable content) -- weight it as heavily as the default brain/"
    "nervous-system formula above.\n"
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
    "something the viewer can DO or SAY, not just a feeling). If the hook "
    "used the SELF-CATEGORIZATION FRAME, the REFRAME must clearly describe "
    "BOTH types (so every viewer places themselves), and the PAYOFF must "
    "give a concrete next step usable by whichever type the viewer is. "
    "Target "
    f"{config.CLIP_WINDOW_MIN_SECONDS}-{config.CLIP_WINDOW_MAX_HARD_SECONDS} "
    "seconds at natural spoken pace (roughly 90-"
    f"{config.SCRIPT_MAX_WORDS} words). "
    f"HARD CAP: the script must be {config.SCRIPT_MAX_WORDS} words or fewer "
    "-- count as you write and cut before you exceed it, never after. Write in "
    "second person, direct address to the viewer. Never write stage "
    "directions, sound effects, or bracketed notes -- ``script`` is spoken "
    "words ONLY, exactly what a narrator would read aloud.\n\n"
    "NAMED MECHANISM RULE: whenever the REFRAME rests on a real, "
    "established psychological or behavioral phenomenon (e.g. cognitive "
    "dissonance, loss aversion, the Zeigarnik effect, negativity bias, "
    "sunk-cost fallacy, precommitment/Ulysses pacts, variable-reward "
    "conditioning, attachment patterns), NAME it explicitly in the script "
    "the first time it's introduced (\"This is called ___\" or equivalent). "
    "A named mechanism reads as authoritative and gives the viewer language "
    "for what's happening to them -- do not water a real, nameable "
    "phenomenon down into a vague unnamed 'your brain does this' claim. "
    "Never invent a fake-sounding scientific name for something that isn't "
    "a real phenomenon; if no established term genuinely applies, describe "
    "the mechanism plainly instead of forcing one.\n\n"
    "The JSON object must have exactly these keys:\n"
    '  "topic_cluster" : string -- one of the provided topic_cluster values.\n'
    '  "hook"           : string -- a contrarian-identity-frame or '
    "self-categorization-frame hook, under 15 words. See HOOK RULES above.\n"
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
    "WOLF CHARACTER BACKGROUND (a persistent well of setting/prop/atmosphere "
    "ideas to draw on -- optional flavor, never forced): this wolf is a "
    "millennial software engineer with an ordinary grounded daily life -- "
    "he codes at a dual-monitor desk, commutes, trains CrossFit and lifts "
    "at the gym, loves rock and roll (vinyl, a guitar, a well-worn band "
    "tee, a turntable), hikes on weekends, and goes through the plain "
    "rhythm of waking, working, eating, training, sleeping. He has a wife "
    "and a 2-year-old daughter, but they are NEVER shown or described as "
    "figures in ANY scene -- that would break the single-figure rule below "
    "and the no-relationship-content rule. Family life may only be implied "
    "through small incidental environmental details when it genuinely fits "
    "the scene (a toy on the floor, small shoes by the door, a second "
    "coffee mug, a child's drawing on a fridge) -- sparing, background, "
    "never the focus, never a rendered person. Pull settings and props from "
    "this world whenever they plausibly fit the script's actual topic -- "
    "but STEP 1's content analysis and that script's specific idea always "
    "come first; never force a CrossFit gym or a hiking trail into a scene "
    "the topic doesn't call for.\n\n"
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
    "phone, a mirror, money, a clock -- whatever it actually is). Favor "
    "natural, in-motion actions (writing, walking, pausing, closing "
    "something) over static poses that just hold or display a prop toward "
    "the camera like a product shot. A viewer on mute should guess the "
    "topic from the image alone. If the prop carries words, you MUST spell "
    "out the EXACT words here (2-6 of them, never more) -- do not leave "
    "the wording to be invented downstream, and always describe the prop "
    "as facing the camera so the words render face-on and fully legible, "
    "never at an angle, foreshortened, upside-down, or turned away from "
    "the viewer.\n"
    '  "setting" : string -- where it happens. VARY ACROSS ALL SCENES -- '
    "never the same setting twice; lean OUT-IN-THE-WORLD (gym, sunlit "
    "street, driving a car, rooftop, market, park bench, workshop, balcony, "
    "bus stop) over domestic (home settings at most once per clip).\n"
    '  "camera"  : string -- dynamic film-still framing: low/high angle, '
    "three-quarter view, through a window, tracking alongside a moving "
    "subject, strong foreground/background depth.\n\n"
    "AVOID LAZY VISUAL SHORTHAND: never represent an abstract idea by "
    "literally duplicating one object (e.g. a row of identical shirts for "
    "'automated choices') or with an infographic-style prop (a labeled "
    "gauge, a giant grid of crossed-out marks, a chart). Find a concrete "
    "action, gesture, or environmental detail that dramatizes the idea "
    "instead -- something happening TO or AROUND the wolf, not something "
    "displayed at the camera.\n\n"
    'WOLF_OUTFIT: ONE outfit of ordinary human clothes that plausibly works '
    "in ALL of this script's settings, worn UNCHANGED in every scene. Feel "
    "free to draw on the WOLF CHARACTER BACKGROUND above when it fits (gym/"
    "CrossFit wear, a worn band tee, a plain smartwatch, hiking-ready "
    "layers) if that reads better against this script's settings than a "
    "generic outfit -- but it must still work across all 6 scenes "
    "unchanged. No logos, no readable text on the clothing.\n"
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

    script_word_count = len(data["script"].split())
    if script_word_count > config.SCRIPT_MAX_WORDS:
        raise ValueError(
            f"'script' is {script_word_count} words, over the "
            f"{config.SCRIPT_MAX_WORDS}-word hard cap (user directive: renders must "
            "stay under 50s). Trim it."
        )

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
        return "No prior scripts yet -- pick freely from all 5 clusters."
    lines = [
        f"- [{e.get('topic_cluster')}] {e.get('title')!r} (hook: {e.get('hook')!r})"
        for e in recent_entries
    ]
    return (
        "Recently used topics, most recent LAST (do not repeat the last "
        "entry's topic_cluster; never reuse a hook/title close to any of "
        "these):\n" + "\n".join(lines)
    )


def generate_script(recent_history: list[dict] | None = None, topic_hint: str | None = None) -> dict:
    """Pick a topic and write the full script + copy + art-direction package
    in one Claude call. Returns a dict with exactly: topic_cluster, hook,
    script, insights, key_line, title, hashtags, wolf_outfit, image_scenes,
    clip_start, clip_end (the last two are a fixed sentinel -- video_gen
    clamps clip_end to the real audio duration at render time regardless).

    ``topic_hint``, if given, pins the topic to a user-specified subject
    instead of letting the model free-pick -- all other rules (hook
    formula, brand gates, cluster taxonomy) still apply unchanged."""
    topic_instruction = (
        f"The user has requested this specific topic: {topic_hint!r}. Write "
        "the script for this exact topic -- still pick whichever "
        "topic_cluster fits it best from the list below, and still follow "
        "every rule above (hook formula, brand mission, banned content).\n\n"
        if topic_hint
        else ""
    )
    body = (
        f"Available topic clusters: {json.dumps(config.TOPIC_CLUSTERS)}\n\n"
        f"{topic_instruction}"
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


def generate_script_with_retry(attempts: int = 3, topic_hint: str | None = None) -> dict:
    """Call :func:`generate_script` up to ``attempts`` times, tolerating the
    model's non-deterministic output. Re-fetches recent history each attempt
    (cheap, local) so a slow retry loop never works off a stale dedup view.
    Only ``ValueError`` and ``anthropic.RateLimitError`` are caught; other
    transport/API errors propagate immediately."""
    from modules import script_history

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return generate_script(script_history.recent(8), topic_hint=topic_hint)
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
