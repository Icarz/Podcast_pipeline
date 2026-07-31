"""AI extraction: turn a transcript into a structured clip plan via Claude.

Sends the transcript text to the Claude Messages API with a system prompt that
constrains the model to emit ONLY valid JSON, then parses and validates it.
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
    "duration, the best of any hook family tested). Prioritize this whenever the clip reveals a "
    "psychological/neurological mechanism acting on the viewer without their awareness. The "
    "mechanism named must be immediately, viscerally felt — not abstract — so the payoff "
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
    "METAPHOR HOOK RULE: treat a hook built on a clever or abstract metaphor/analogy (e.g. 'Your "
    "fear is a GPS') as a FALLBACK, not a first choice — it is HIGH RISK for retention. This is "
    "confirmed, repeated data, not a single incident: across two separate review rounds, metaphor "
    "and abstract-wordplay hooks ('Your fear is a GPS', 'You're choosing your hard', 'You're not "
    "running from emptiness, you're running from meaning') kept WINNING on views (1,200-1,400) "
    "while landing in the WORST retention tier of the whole channel (16-22%, viewers bail in ~10s) "
    "— because the payoff is never cashed out before the viewer leaves. DEFAULT to the "
    "neurological WINNING FORMULA (or another direct/concrete structure above) whenever it fits "
    "the content. Only reach for a metaphor/analogy hook when BOTH: (a) no concrete structure fits "
    "this content, AND (b) the transcript's very FIRST sentence after clip_start already states "
    "plainly what the metaphor means in practice — not the second sentence, not 'a few sentences "
    "in'. If (b) isn't satisfiable from the available transcript, do not use a metaphor hook for "
    "this clip at all — pick a concrete structure or a different clip.\n\n"
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
    '  "wolf_outfit"  : string — ONE outfit for the illustrated wolf character, '
    "worn unchanged in every scene of this clip. See the IMAGE_SCENES rules below.\n"
    f'  "image_scenes" : array of exactly {config.IMAGE_PROMPT_COUNT} OBJECTS — '
    "structured scene directions for the clip's illustrated background images, "
    'each {"beat", "concept", "action", "setting", "camera"}. See the '
    "IMAGE_SCENES art-direction rules below.\n\n"
    f"IMAGE_SCENES — {config.IMAGE_PROMPT_COUNT} structured scene directions "
    "for the clip's illustrated background images. You write the CONTENT of "
    "each scene ONLY — the locked visual style (a vintage halftone comic-book "
    "illustration of ONE anthropomorphic wolf character in a warm, bright "
    "palette) is appended in code afterwards and is NOT yours to describe or "
    "vary. Never mention art style, palette, lighting quality, or the wolf's "
    "species/appearance in your fields — only what happens, where, and how "
    "it is framed.\n\n"
    "STEP 1 — CONTENT ANALYSIS (MANDATORY, do this before writing any "
    "scene): divide the clip window into 4 roughly equal time quarters. For "
    "EACH quarter, name in one plain sentence the SPECIFIC concept, claim, "
    "or action the speaker expresses in those seconds:\n"
    "  Q1: the problem, behavior, or situation being introduced.\n"
    "  Q2: the mechanism, tension, or why it matters.\n"
    "  Q3: the insight, reframe, or turning point.\n"
    "  Q4: the payoff, resolution, or call to action.\n\n"
    f"STEP 2 — {config.IMAGE_PROMPT_COUNT} SCENES, ONE STORY ARC. The scenes "
    "form a single visual story that mirrors the speech, mapped IN ORDER "
    'with these exact "beat" values: '
    f"{json.dumps(config.IMAGE_SCENE_BEATS)}.\n"
    "  Scenes 1-2 (problem): the wolf FACING Q1's specific problem — two "
    "DIFFERENT settings and camera angles on the same struggle.\n"
    "  Scene 3 (stakes): Q2 made visible — the weight, cost, or mechanism "
    "of the problem.\n"
    "  Scene 4 (reframe): Q3's turning point — the moment the new lens "
    "lands.\n"
    "  Scenes 5-6 (payoff): Q4 lived out — first in action, then resolved "
    "and forward-looking.\n"
    "ARC TENSION RULE: scenes 1-3 may show confrontation and tension — the "
    "wolf looks AT the problem, upright, jaw set, determined. NEVER "
    "slumped, defeated, head-in-hands, or despairing: the tension lives in "
    "the SCENE (the prop, the stakes), never in a broken posture.\n\n"
    "Each scene object has exactly these keys:\n"
    '  "beat"    : string — the fixed value for its position (sequence '
    "above).\n"
    '  "concept" : string — one plain sentence: the specific idea from '
    "STEP 1 this scene illustrates. Quote the quarter's actual idea, not a "
    "vague mood.\n"
    '  "action"  : string — what the wolf is physically DOING, including a '
    "LITERAL PROP: identify the exact thing said in that quarter (a to-do "
    "list, a choice between two objects, a clock, money, a mirror, a "
    "phone, whatever it actually is) and put a concrete visual stand-in "
    "for THAT THING in the wolf's hands or actions. A viewer on mute "
    "should guess the topic from the image alone — a generic mood action "
    "with no concrete link to that quarter's words is a FAILURE. If the "
    "prop naturally carries words (a list, a note, a sign, a label), you "
    "MAY specify 2-6 short readable words for it taken from that "
    "quarter's idea (e.g. a to-do list reading 'GYM - EMAILS - BILLS', a "
    "sticky note reading 'WHO IS DOING THIS?') — in-context prop text "
    "like this is encouraged when it sharpens the message; decorative or "
    "unrelated text is not.\n"
    '  "setting" : string — where it happens. VARY ACROSS ALL SCENES: '
    "never the same setting twice in one clip; lean OUT-IN-THE-WORLD (gym, "
    "busy sunlit street, riding in/driving a car, rooftop with a modern "
    "big-city skyline, market, park bench, garage or workshop, balcony "
    "with plants, bus stop, laundromat) over domestic — home settings "
    "(couch, kitchen, desk, doorway) at most once per clip. Pick whichever "
    "place makes the action make sense.\n"
    '  "camera"  : string — dynamic film-still framing: low or high angle, '
    "three-quarter view, through a car windshield or window, tracking "
    "alongside a moving subject, strong foreground/background depth, "
    "purposeful motion (wind, motion blur) where the scene calls for it.\n"
    "  Example scene (concept 'you plan external actions, not who to "
    "become'): {\"beat\": \"problem\", \"concept\": \"you plan all the "
    "external things to do, but never who to become\", \"action\": "
    "\"holding up a long handwritten to-do list next to a small mirror, "
    "eyes moving between the two\", \"setting\": \"a sunlit balcony "
    "crowded with potted plants, city rooftops behind\", \"camera\": "
    "\"bright three-quarter shot, list and mirror large in the "
    "foreground\"}\n\n"
    'WOLF_OUTFIT: also emit "wolf_outfit" — ONE outfit of ordinary human '
    "clothes that plausibly works in ALL of this clip's settings (e.g. 'a "
    "rust-orange hoodie, dark jeans and white sneakers'). It is worn "
    "UNCHANGED in every scene so the images read as one character's story. "
    "No logos, no readable text on the clothing.\n"
    "NEVER DEPICT (image scenes hard blacklist): no "
    "skull, skeleton, or death imagery; no cigarettes, alcohol, drugs, or "
    "vices; no slumped or defeated posture; no violence or gore; no crowds "
    "or extra figures of any kind (a busy street/market as an anonymous "
    "BACKDROP is fine — the wolf must remain the only clearly-rendered "
    "figure); no floating or decorative typography (short readable words "
    "ON a prop are allowed per the action rule above — the karaoke "
    "captions are composited separately at render time, so prop text must "
    "stay small and part of the scene, never poster-style lettering over "
    "the image).\n\n"
    "You are given the transcript excerpt for the already-chosen clip window "
    "below, plus a short note on why this segment was chosen (what it exposes, "
    "its reframe, and its payoff) for context only — you do not choose or adjust "
    "the window. Base every element of your copy on the actual words spoken in "
    "the excerpt; do not invent claims the speaker didn't make."
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


_SCENE_TEXT_KEYS = ("concept", "action", "setting", "camera")


def _normalize_image_scenes(data: dict) -> None:
    """Coerce ``data['image_scenes']`` to EXACTLY ``config.IMAGE_PROMPT_COUNT``
    usable scene objects (in place) and stamp the fixed beat sequence.

    A scene is usable when ``concept``, ``action``, and ``setting`` are all
    non-empty strings; ``camera`` gets a sane default when blank. Extras are
    truncated (warned); too few usable scenes raises so the retry wrapper
    re-extracts — padding would duplicate images on screen. Beats are always
    OVERWRITTEN with ``config.IMAGE_SCENE_BEATS`` (position defines the story
    arc; a model-mislabeled beat is corrected, never fatal).
    """
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


# Deterministic backstop for the NEVER DEPICT rules. The model follows the
# prompt most of the time but not always (in the Pexels era it produced "two
# men diverging path golden hour" and "man standing still crowd passing dawn"
# in the same run despite both being against the prompt's own rules). This
# regex scan on the scene text itself is cheap and catches the failure at the
# earliest possible point — before a single image is generated.
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
    """Raise ValueError if any generated query violates the single-male-subject /
    no-crowd rule, so the retry wrapper re-extracts instead of silently shipping
    off-brand imagery. Checks the VISUAL fields of ``image_scenes``
    (``action``/``setting`` — the wolf must stay alone; ``concept`` is
    deliberately NOT scanned since it restates the speech, which may
    legitimately mention people).
    """
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
            "SCENE SAFETY GATE — one or more image scenes describe a banned "
            f"subject (crowd/group/multi-person/female figure): {offenders!r}. "
            "Every scene must depict the wolf character alone."
        )


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

    if len(data["insights"]) != 3:
        raise ValueError(f"'insights' must have exactly 3 items, got {len(data['insights'])}")

    # Normalize image_scenes to the EXACT expected count (one image per scene,
    # 6 background slots + all 6 slide backgrounds). Blanks/extras are dropped,
    # but a scene shortfall raises (padding would duplicate images on screen)
    # so the retry wrapper re-extracts.
    _normalize_image_scenes(data)
    _scene_safety_gate(data)

    window = data["clip_end"] - data["clip_start"]
    lo, hi = config.CLIP_WINDOW_MIN_SECONDS, config.CLIP_WINDOW_MAX_HARD_SECONDS
    if not (lo <= window <= hi):
        raise ValueError(
            f"clip window {window:.1f}s outside allowed range [{lo}, {hi}]s "
            f"(start={data['clip_start']}, end={data['clip_end']})"
        )


_METAPHOR_HOOK_RE = re.compile(
    r"\bis a\b|\bis an\b|\bis like\b|\bacts? like\b|\bworks? like\b|\bis basically\b",
    re.IGNORECASE,
)


def is_metaphor_hook(hook: str) -> bool:
    """Best-effort flag for analogy-style hooks ('Your fear is a GPS').

    Two review rounds now confirm these hooks win on views (1,200-1,400) but
    land in the worst retention tier (16-22%) because the payoff isn't cashed
    out fast enough. This only catches literal 'X is a/like Y' analogy
    phrasing — other abstract wordplay ('You're choosing your hard') carries
    the same risk but isn't reliably regex-detectable, so a human reviewer
    should still judge the rest of the shortlist by eye, not rely on this flag
    alone.
    """
    return bool(_METAPHOR_HOOK_RE.search(hook or ""))


def _brand_gate(data: dict) -> None:
    """Raise ValueError if the extracted clip fails the brand mission gate.

    Three checks — all must pass or the retry wrapper re-extracts:

    1. HOOK IDENTITY FRAME — the hook must address the viewer directly
       ("you/your") or use an imperative contrarian structure ("Stop …",
       "Every …"). Hooks that describe an external system or event
       ("Science told millions…", "The reason why…") are banned by the
       SYSTEM_PROMPT but still slip through; this catches them in code.

    2. INSIGHT PERSON — at least 2 of 3 insights must be 2nd-person identity
       statements containing "you" or "your". Insights written entirely in 3rd
       person ("The system laundered bad science…", "Entire careers were
       built…") signal a diagnostic clip about external events — not a clip
       that gives the viewer self-awareness or agency.

    3. NO-AGENCY PATTERNS — certain hook phrases signal a pure exposure/scandal
       frame with zero path forward ("nobody apologized", "they lied", "the
       scandal"). These always fail the hope+agency brand outcome regardless of
       how interesting the underlying topic is.
    """
    hook = data.get("hook", "")
    insights = data.get("insights", [])
    hook_lower = hook.lower()

    # 1. Hook must address the viewer or use a contrarian imperative.
    identity_signals = ["you", "your", "we ", "our ", "stop ", "every ", "nobody "]
    if not any(sig in hook_lower for sig in identity_signals):
        raise ValueError(
            f"BRAND GATE — hook fails identity-frame check (no viewer address or "
            f"imperative): {hook!r}. Must contain 'you/your' or a contrarian imperative."
        )

    # 2. At least 2/3 insights must be viewer-addressed: 2nd-person ("you/your"),
    # 1st-person internal voice ("me/my/i ") as the viewer thinking aloud, or a
    # direct imperative verb opening ("move", "stop", "choose", "act", "be ",
    # "start", "ask ", "drop"). Pure 3rd-person external-system descriptions
    # ("entire careers were built on…") are the only thing we reject here.
    _VIEWER_SIGNALS = ("you", "your", " me", " my", " i ")
    _IMPERATIVES = ("move ", "stop ", "choose ", "act ", "be ", "start ", "ask ", "drop ")

    def _is_viewer_addressed(ins: str) -> bool:
        low = ins.lower()
        if any(sig in low for sig in _VIEWER_SIGNALS):
            return True
        # Imperative opening: first word is a command directed at the viewer.
        if any(low.startswith(imp) or low.startswith(imp.strip() + ",") for imp in _IMPERATIVES):
            return True
        return False

    second_person_count = sum(1 for ins in insights if _is_viewer_addressed(ins))
    if second_person_count < 2:
        raise ValueError(
            f"BRAND GATE — only {second_person_count}/3 insights are viewer-addressed "
            f"(need 'you/your', 'me/my', or an imperative opening). Pure 3rd-person "
            f"external descriptions are not allowed. Insights: {insights}"
        )

    # 3. Hook must not be a pure scandal/exposure frame with no viewer agency.
    no_agency_phrases = [
        "nobody apologized", "nobody told", "they never told", "lied to",
        "the scandal", "exposed the", "they hid", "covered up",
    ]
    if any(phrase in hook_lower for phrase in no_agency_phrases):
        raise ValueError(
            f"BRAND GATE — hook is a pure diagnosis/scandal frame with no viewer "
            f"agency: {hook!r}. The viewer must leave with a reframe or path, "
            "not just an exposure."
        )

    logger.info(
        "Brand gate PASSED — hook identity: yes, 2nd-person insights: %d/3, "
        "agency: yes | hook=%r",
        second_person_count, hook,
    )


def _content_gate(data: dict, transcript: dict) -> None:
    """Raise ValueError if the actual clip transcript is weak content.

    The brand gate validates the AI's *written* hook/insights but never reads
    the source audio.  This gate extracts the real transcript words in the clip
    window and asks Haiku whether the segment delivers a payoff — rejecting
    rambling, small talk, and segments that trail off without a landing.

    Fails open on API errors (broken gate never blocks the pipeline).
    """
    if not config.CONTENT_GATE_ENABLED:
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return

    words = transcript.get("words") if isinstance(transcript, dict) else None
    if not words:
        return

    cs, ce = float(data["clip_start"]), float(data["clip_end"])
    clip_words = [
        w["word"] for w in words
        if w.get("start") is not None
        and cs - 0.5 <= w["start"] <= ce + 0.5
        and (w.get("word") or "").strip()
    ]
    if not clip_words:
        return

    clip_text = " ".join(clip_words)

    prompt = (
        f"Clip hook: {data.get('hook', '')}\n"
        f"Clip title: {data.get('title', '')}\n\n"
        f"Below is the ACTUAL transcript of the selected clip segment "
        f"({ce - cs:.0f} seconds of audio). Read it carefully.\n\n"
        f"---\n{clip_text}\n---\n\n"
        "Evaluate this transcript segment on SIX criteria:\n"
        "1. PAYOFF — Does the segment land on a clear insight, reframe, or "
        "actionable takeaway that the VIEWER can use? (Not just build-up, "
        "a story that trails off, or a point that never lands.)\n"
        "2. DENSITY — Is the segment focused and tight, or is it padded with "
        "filler, rambling anecdotes, 'um/uh', or repetitive small talk?\n"
        "3. HOOK-MATCH — Does the actual spoken content deliver what the hook "
        "and title promise?\n"
        "4. UNIVERSALITY — The segment must speak to the VIEWER's life, not "
        "the speaker's personal story. If the last 20% of the segment is the "
        "speaker talking about themselves ('I did', 'my coaching', 'my "
        "experience', 'when I was'), it fails. Brief personal examples that "
        "serve a universal point are fine; self-promotion or extended "
        "autobiography is not.\n"
        "5. STRUCTURE — A good clip follows HOOK → TENSION → INSIGHT → PAYOFF. "
        "The segment must contain all four beats. HOOK: the opening creates an "
        "immediate reason to keep watching (a question, a contradiction, an "
        "uncomfortable truth). TENSION: the clip makes clear WHY this matters — "
        "what is at stake, what the viewer is losing or missing. INSIGHT: a new "
        "frame or mechanism the viewer didn't have before. PAYOFF: a resolution, "
        "realization, or action the viewer can take away. A segment that jumps "
        "straight to the answer without creating stakes first fails. A segment "
        "that is all build-up without a landing fails. A segment that gives the "
        "insight but never makes the viewer feel the stakes fails.\n"
        "6. DIGESTIBILITY — A complete stranger must be able to grasp the core "
        "idea within 3 seconds with ZERO prior context. The viewer's reaction "
        "must be 'yes, that's me' — not 'interesting, let me think about that'. "
        "If the concept requires explanation, intellectual assembly, specialist "
        "vocabulary, or prior knowledge of the speaker's framework to land, it "
        "fails. Simple ≠ shallow: 'Your brain is wired to rehearse fake "
        "scenarios' passes; 'anxiety and creativity share the same neural "
        "substrate' fails (requires explanation). Reject clips where the hook "
        "is intellectually interesting but not immediately obvious to anyone.\n"
        "7. SPECIFICITY — The clip must name a concrete mechanism, behaviour, or "
        "cause — not just a vibe, attitude, or feeling. A stranger who has never "
        "heard the speaker must be able to retell the core idea in one sentence "
        "that includes a WHAT and a WHY. Generic motivational conclusions fail: "
        "'stay in progress mode', 'keep going', 'believe in yourself', "
        "'you have what it takes' are attitudes, not mechanisms. "
        "GOOD (mechanism present): 'Your brain rehearses fake failure scenarios '  "
        "because it treats imagination as real threat — that's why anxiety feels "
        "physical.' BAD (vibe only): 'The more you stay in progress mode, the "
        "more you see a future others don't.' If the insight is only a feeling "
        "or an attitude with no named cause or mechanism, it fails.\n\n"
        "If ANY criterion clearly fails, respond 'NO: <one-sentence reason>'.\n"
        "If all seven pass, respond 'YES'.\n"
        "Respond with ONLY 'YES' or 'NO: <reason>'."
    )

    try:
        client = _client()
        response = client.messages.create(
            model=config.CONTENT_GATE_MODEL,
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = (response.content[0].text or "").strip()
        if answer.upper().startswith("YES"):
            logger.info("Content gate PASSED | hook=%r", data.get("hook", ""))
            return

        logger.warning("Content gate FAILED: %s", answer)
        raise ValueError(
            f"CONTENT GATE — clip transcript rejected: {answer}. "
            f"Segment ({cs:.1f}-{ce:.1f}s) did not deliver a payoff."
        )
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("Content gate error (failing open): %s", exc)


_SENTENCE_END = (".", "!", "?")


def _ends_sentence(word: str) -> bool:
    """True if a word token ends a sentence (terminal punctuation, ignoring
    any trailing quote/bracket characters)."""
    return word.rstrip("\"')]}").endswith(_SENTENCE_END)


def _sentence_open_starts(words: list) -> list[float]:
    """Start times of sentence-opening words: the first word, plus any word whose
    predecessor ends a sentence.

    Shared by :func:`_trim_to_cap` and :func:`_snap_to_sentences` so both agree on
    what a "sentence start" is. ``_ends_sentence`` covers the closing side.
    """
    ws = [
        w for w in words
        if w.get("start") is not None and w.get("end") is not None and (w.get("word") or "").strip()
    ]
    if not ws:
        return []
    return [ws[0]["start"]] + [
        ws[i]["start"] for i in range(1, len(ws)) if _ends_sentence(ws[i - 1]["word"])
    ]


def _trim_to_cap(data: dict, words: list) -> None:
    """Shorten an over-long clip while PRESERVING its payoff.

    The model is asked to end ``clip_end`` on the concluding/payoff sentence, so
    when the chosen thought runs past ``CLIP_WINDOW_MAX_HARD_SECONDS`` we keep
    ``clip_end`` FIXED and trim from the FRONT: push ``clip_start`` forward to the
    earliest sentence-opening word that brings the window under the cap (and still
    at/above the floor), keeping the most setup context the clip can while still
    landing on the payoff.

    Only in the rare case where the payoff sentence ALONE exceeds the cap (no
    sentence-opening word lands late enough to fit) do we fall back to the old
    behavior: pull ``clip_end`` back to the latest sentence boundary under the cap
    (the payoff is sacrificed). No-op when the clip already fits.
    """
    cs, ce = data.get("clip_start"), data.get("clip_end")
    if not isinstance(cs, (int, float)) or not isinstance(ce, (int, float)):
        return
    cap = config.CLIP_WINDOW_MAX_HARD_SECONDS
    floor = config.CLIP_WINDOW_MIN_SECONDS
    if ce - cs <= cap:
        return

    ws = [
        w for w in words
        if w.get("start") is not None and w.get("end") is not None and (w.get("word") or "").strip()
    ]

    # Preferred: keep the payoff (clip_end) and push clip_start forward. A valid
    # new start lands the window in [floor, cap] while still ending on clip_end:
    #   new_start >= ce - cap   (window <= cap)
    #   new_start <= ce - floor (window >= floor)
    # Pick the EARLIEST opening word in that band -> the longest clip that fits.
    band = [t for t in _sentence_open_starts(ws) if (ce - cap) <= t <= (ce - floor) and t > cs]
    if band:
        new_start = min(band)
        logger.warning(
            "Clip window %.1fs exceeds cap %ds; moved clip_start %.2f -> %.2f to "
            "preserve payoff (now %.1fs)",
            ce - cs, cap, cs, new_start, ce - new_start,
        )
        data["clip_start"] = new_start
        return

    # Fallback: the payoff sentence won't fit -> pull clip_end back to the latest
    # sentence boundary under the cap (old behavior; payoff sacrificed).
    limit = cs + cap
    sentence_ends = [w["end"] for w in ws if _ends_sentence(w["word"]) and cs < w["end"] <= limit]
    in_band = [t for t in sentence_ends if t >= cs + floor]
    if in_band:
        new_end = max(in_band)
    elif sentence_ends:
        new_end = max(sentence_ends)
    else:
        word_ends = [w["end"] for w in ws if cs < w["end"] <= limit]
        new_end = max(word_ends) if word_ends else limit

    logger.warning(
        "Clip window %.1fs exceeds cap %ds and the payoff sentence won't fit; "
        "trimmed clip_end %.2f -> %.2f (now %.1fs)",
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
    start_times = _sentence_open_starts(ws)
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
        # The model occasionally emits timestamps as strings ('780.15', even
        # ' 780.15') — coerce instead of dropping. A real 2026-07-31 scan lost
        # 3 strong candidates to a strict isinstance check here.
        try:
            c["clip_start"] = float(c["clip_start"])
            c["clip_end"] = float(c["clip_end"])
        except (TypeError, ValueError):
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


_RETRY_SLEEP_S = 65  # sleep between attempts to clear the 1-min rate-limit window


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
    ``hook``, ``insights``, ``best_quote``, ``title``, ``clip_start``,
    ``clip_end``, ``hashtags``, ``wolf_outfit``, and ``image_scenes``
    (structured scene specs — image_gen composes the final prompts from its
    locked style template; the same images now also back the slide deck).
    (``image_prompts``/``video_queries``/``search_queries`` were removed from
    the schema 2026-07-31; old cached plans still carrying them render fine
    via the background.py / slide_gen fallbacks.)

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
        flag = "  [!] METAPHOR HOOK" if is_metaphor_hook(c["hook"]) else ""
        print(f"{i}. [{c['clip_start']:.1f}-{c['clip_end']:.1f}s] {c['hook']!r}{flag}")

    if not survivors:
        raise SystemExit("No candidates survived filtering.")

    top = survivors[0]
    result = extract_copy_for_window(transcript, top["clip_start"], top["clip_end"], top)

    print("\n=== Extracted copy for top survivor ===")
    print(json.dumps(result, indent=2, ensure_ascii=False).encode("ascii", "replace").decode("ascii"))
    print(f"\nClip window: {result['clip_end'] - result['clip_start']:.1f}s (valid)")
