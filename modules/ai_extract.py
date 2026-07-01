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

SYSTEM_PROMPT = (
    "You are a podcast clip producer. You read a full episode transcript and "
    "select the single best short-form clip plus social copy.\n\n"
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
    '  "clip_start"  : number — MUST be the exact start timestamp of one of the '
    "segments in the provided list, AND must fall at the BEGINNING of a complete "
    "thought (the start of a sentence or idea), never mid-sentence.\n"
    '  "clip_end"    : number — MUST be the exact end timestamp of a LATER segment '
    "in the list, AND must fall at the END of a complete thought or conclusion. "
    "The clip MUST contain a full, self-contained idea WITH its payoff. NEVER cut "
    "off mid-sentence, and NEVER end on a setup/cliffhanger such as \"...this is "
    "what I want you to do\" or \"...here's the thing\" without the actual point "
    "that follows. The last segment must deliver the resolution, not tee it up. "
    "If a complete thought runs long, ANCHOR clip_end on its concluding/payoff "
    "sentence and choose clip_start as LATE as needed to fit the length limit — "
    "never drop the payoff to keep an earlier opening line.\n"
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


# Deterministic backstop for the NEVER DEPICT rules in SYSTEM_PROMPT. The model
# follows the prompt most of the time but not always (it once produced "two men
# diverging path golden hour" and "man standing still crowd passing dawn" in the
# same run despite both being against the prompt's own rules) — Pexels/Pixabay
# then return literal footage of exactly what was asked for, and the post-fetch
# bg_quality gate can miss it too (its Haar face-detector doesn't see small or
# distant faces in a wide crowd shot). This regex scan on the QUERY TEXT ITSELF
# is cheap, has no false-negative risk from image quality, and catches the
# failure at the earliest possible point — before a single Pexels call is made.
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
    off-brand footage. Checks ``search_queries`` (strings) and ``video_queries``
    (``{keyword, query}`` objects) — the only two fields that ever reach Pexels.
    """
    offenders: list[str] = []

    def _check(text: str) -> None:
        low = text.lower()
        if _BANNED_SCENE_WORDS.search(low) or any(p in low for p in _BANNED_SCENE_PHRASES):
            offenders.append(text)

    for q in data.get("search_queries", []):
        if isinstance(q, str):
            _check(q)
    for item in data.get("video_queries", []):
        if isinstance(item, dict):
            _check(str(item.get("query") or ""))

    if offenders:
        raise ValueError(
            "SCENE SAFETY GATE — one or more queries would surface a banned "
            f"scene (crowd/group/multi-person/female subject): {offenders!r}. "
            "Every query must depict exactly one male subject, alone."
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
    _scene_safety_gate(data)

    window = data["clip_end"] - data["clip_start"]
    lo, hi = config.CLIP_WINDOW_MIN_SECONDS, config.CLIP_WINDOW_MAX_HARD_SECONDS
    if not (lo <= window <= hi):
        raise ValueError(
            f"clip window {window:.1f}s outside allowed range [{lo}, {hi}]s "
            f"(start={data['clip_start']}, end={data['clip_end']})"
        )


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
    _brand_gate(parsed)
    _content_gate(parsed, transcript)

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


_RETRY_SLEEP_S = 65  # sleep between attempts to clear the 1-min rate-limit window


def extract_highlights_with_retry(transcript: dict, attempts: int = 3) -> dict:
    """Call :func:`extract_highlights` up to ``attempts`` times, tolerating the
    model's non-deterministic output.

    Extraction is NOT deterministic: the same transcript can yield an off-by-one
    count (e.g. 5 ``image_prompts`` instead of 4) or stray trailing data that trips
    ``_validate``/``json.loads`` (both raise ``ValueError``). Rather than die on the
    first throw, retry a few times — a later attempt almost always passes. Only
    ``ValueError`` and ``RateLimitError`` are caught; other transport/API errors
    propagate immediately. Re-raises the last error after the final attempt fails.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return extract_highlights(transcript)
        except ValueError as exc:
            last_exc = exc
            logger.warning(
                "extract_highlights attempt %d/%d failed (non-deterministic): %s",
                attempt, attempts, exc,
            )
        except anthropic.RateLimitError as exc:
            last_exc = exc
            logger.warning(
                "extract_highlights attempt %d/%d hit rate limit: %s",
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

    result = extract_highlights(transcript)

    print("\n=== Extracted clip plan ===")
    print(json.dumps(result, indent=2, ensure_ascii=False).encode("ascii", "replace").decode("ascii"))
    print(f"\nClip window: {result['clip_end'] - result['clip_start']:.1f}s (valid)")
