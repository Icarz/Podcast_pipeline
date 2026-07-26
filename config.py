"""Central configuration for the podcast automation pipeline.

All tunable values and shared constants live here so modules import them
instead of hardcoding paths, dimensions, or feed URLs.
"""

import os

# --- Paths (absolute, anchored to this file's directory) ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TMP_DIR = os.path.join(BASE_DIR, "tmp")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
VIDEO_DIR = os.path.join(OUTPUT_DIR, "videos")
SLIDE_DIR = os.path.join(OUTPUT_DIR, "slides")

LOG_FILE = os.path.join(LOGS_DIR, "pipeline.log")

# --- Podcast RSS feeds (verified live June 2026; select by key) ---
# Each value is the show's official RSS URL — verify against the source in the
# trailing comment. The first five are the weekly ROTATION (see below).
# ROTATION PHILOSOPHY: prefer solo-speaker / monologue-format shows. Interview
# podcasts produce two-voice clips that perform poorly as Shorts.
PODCAST_FEEDS = {
# The Mel Robbins Podcast (Simplecast)            https://feeds.simplecast.com/UCwaTX1J
    # Solo motivational; consistently single-speaker. Brand: identity/resilience.
    "mel_robbins":    "https://feeds.simplecast.com/UCwaTX1J",
    # The Jordan B. Peterson Podcast (Megaphone)     https://feeds.megaphone.fm/BVDWV6444647327
    # NOTE: canonical PUBLIC feed (per Apple + jordanbpeterson.com + podnews).
    # Recent episodes are Daily Wire+ members-only; public RSS lags (newest
    # public ep was Nov 2025 as of June 2026). --auto dedup handles re-pulls.
    "jordan_peterson": "https://feeds.megaphone.fm/BVDWV6444647327",
    # The Daily Stoic — Ryan Holiday (Art19)         https://rss.art19.com/the-daily-stoic
    # Pure solo monologue (5–10 min commentary). Highest single-speaker signal.
    "daily_stoic":    "https://rss.art19.com/the-daily-stoic",
    # The Mindset Mentor — Rob Dial (Simplecast)     https://feeds.simplecast.com/rpKQEwel
    # Solo monologue. Brand: neuroscience/behavior/identity.
    "mindset_mentor": "https://feeds.simplecast.com/rpKQEwel",
    # --- manual-run only (not in rotation) ---
    # On Purpose — Jay Shetty (interview format; 2-speaker risk like Modern Wisdom)
    "jay_shetty":     "https://www.omnycontent.com/d/playlist/e73c998e-6e60-432f-8610-ae210140c5b1/32f1779e-bc01-4d36-89e6-afcb01070c82/e0c8382f-48d4-42bb-89d5-afcb01075cb4/podcast.rss",
    # Modern Wisdom — Chris Williamson (interview format; 2-speaker risk)
    "modern_wisdom":  "https://feeds.megaphone.fm/modernwisdom",
    # Jocko Podcast — Jocko Willink (RedCircle)
    "jocko_podcast":  "https://feeds.redcircle.com/64a89f88-a245-4098-8d8d-496325ec4f74",
    # SOLVED with Mark Manson (monthly)
    "mark_manson":    "https://feeds.megaphone.fm/ISML9402684841",
}

# Named host per feed, used by rss_ingest's prescreen to reject guest/interview
# episodes: if a guest is credited (title pattern like "| Guest Name", "with
# Guest Name", "ft. Guest Name") the episode is skipped entirely rather than
# risk extracting a clip of the GUEST's words attributed to the show's host.
# HARD RULE (user directive, Jul 2026): if the named host isn't the one
# speaking, don't take it — a solo/monologue episode is required.
PODCAST_HOSTS = {
    "mel_robbins":     "Mel Robbins",
    "jordan_peterson": "Jordan Peterson",
    "daily_stoic":     "Ryan Holiday",
    "mindset_mentor":  "Rob Dial",
    "jay_shetty":      "Jay Shetty",
    "modern_wisdom":   "Chris Williamson",
    "jocko_podcast":   "Jocko Willink",
    "mark_manson":     "Mark Manson",
}

# Default feed used when none is specified.
DEFAULT_FEED = "mindset_mentor"

# --- Weekly multi-podcast rotation ---
# main.py --auto picks today's feed from here by weekday. Python's
# date.weekday() is Mon=0, Tue=1, ..., Sun=6. Any weekday NOT listed is a
# non-posting day: --auto logs "no posting day today" and exits 0.
ROTATION = {
    0: "brendon_burchard", # Monday
    1: "mel_robbins",      # Tuesday
    2: "jordan_peterson",  # Wednesday
    4: "daily_stoic",      # Friday
    5: "mindset_mentor",   # Saturday
}

# --- Episode content pre-filter (used by rss_ingest.pick_random_entry) ---
# Episodes whose TITLE matches any EPISODE_REJECT_KEYWORDS word are dropped
# before random selection — they're clearly off-brand regardless of what's
# buried inside. Case-insensitive substring match on the episode title only
# (descriptions are too verbose and noisy for hard rejection).
EPISODE_REJECT_KEYWORDS = [
    # Economics / finance / markets
    "economy", "economic", "econom", "finance", "financial", "investing",
    "investment", "stock market", "crypto", "bitcoin", "nft", "hedge fund",
    "venture capital", "private equity", "real estate", "tax", "inflation",
    "recession", "debt", "money management", "wealth building",
    # Politics / ideology / debate
    "politics", "political", "election", "government", "policy", "congress",
    "democrat", "republican", "president", "prime minister", "war", "military",
    "geopolit", "climate change", "immigration",
    "anarchy", "anarchism", "libertarian", "socialism", "communism", "capitalism",
    "debate", "vs.", " vs ", "ideology", "manifesto",
    # Sports / entertainment / celebrity
    "nfl", "nba", "nhl", "mlb", "ufc", "mma", "boxing", "football", "basketball",
    "baseball", "soccer", "tennis", "golf", "athlete", "championship",
    "celebrity", "actor", "actress", "movie", "film", "hollywood", "music industry",
    # Time-specific / news
    "covid", "pandemic", "coronavirus", "lockdown",
    # Biohacking / substance-based performance (lifestyle tactics, not identity)
    "biohacking", "biohack", "nootropic", "supplement stack", "cold plunge",
    "cold shower", "ice bath", "intermittent fasting", "caffeine protocol",
    "pre-workout", "energy drink", "sleep hack",
]

# Episodes whose title OR description contains any of these words are scored
# higher and preferred during random selection. Purely additive — a zero-score
# episode is still eligible, just less likely to be picked than a scored one.
# Each match adds 1 point; random.choices weights by (score + 1) so every
# episode has a non-zero probability.
# Description-level reject phrases — checked against the first 500 chars of the
# episode description (summary). More specific than EPISODE_REJECT_KEYWORDS
# (which targets titles) to avoid false positives on verbose descriptions.
# Use multi-word phrases where possible; single words only when unambiguous.
EPISODE_REJECT_DESC_PHRASES = [
    # Science/institutional scandal
    "replication crisis", "replication failure", "research fraud",
    "scientific misconduct", "peer review scandal", "statistically invalid",
    "retracted stud", "false positive result",
    # Finance / markets (description-level)
    "stock market", "investment portfolio", "asset allocation",
    "interest rate", "hedge fund", "venture capital",
    "cryptocurrency", "blockchain technology", "nft",
    # Politics / elections
    "election result", "political campaign", "voting rights",
    "foreign policy", "immigration policy", "border control",
    "government spending", "federal budget", "tax policy",
    # Crime / legal
    "criminal trial", "convicted", "lawsuit", "legal battle",
    "war crime", "genocide", "terrorism",
    # Entertainment / sports scores
    "box office", "movie premiere", "film release", "album drop",
    "super bowl", "world cup", "grand slam", "championship game",
    "box office gross",
]

EPISODE_BRAND_KEYWORDS = [
    # Core universe
    "psychology", "mindset", "behavior", "behaviour", "mental", "brain",
    "neuroscience", "neurology", "cognitive", "consciousness",
    # Identity / self
    "identity", "self-", "confidence", "purpose", "meaning", "fulfillment",
    "self-worth", "self-image", "ego", "authenticity",
    # Performance / habit
    "habit", "discipline", "focus", "productivity", "performance", "routine",
    "willpower", "motivation", "drive", "ambition", "goal",
    # Resilience / growth
    "resilience", "adversity", "overcome", "struggle", "growth", "grit",
    "stoic", "stoicism", "philosophy", "wisdom", "virtue",
    # Emotional / relational
    "emotion", "emotional", "anxiety", "fear", "stress", "relationship",
    "loneliness", "connection", "trauma", "healing", "therapy",
    # Thinking / decision
    "decision", "thinking", "awareness", "perception", "belief", "bias",
    "perspective", "reframe", "clarity",
]

# Episode pre-screen (Claude Haiku call before download).
# A binary YES/NO call using the cheapest model — checks whether an episode
# likely contains on-brand content BEFORE committing to a 45–80 MB download
# and full Groq transcription. Tries up to PRESCREEN_MAX_ATTEMPTS random picks
# from the on-brand pool; falls back to the highest brand-scored episode if all
# fail. Set PRESCREEN_ENABLED=False to bypass (e.g. in tests or offline runs).
PRESCREEN_ENABLED = True
PRESCREEN_MODEL = "claude-haiku-4-5-20251001"
PRESCREEN_MAX_ATTEMPTS = 3

# Content gate: after extraction, send the actual clip transcript back to Haiku
# to verify the segment delivers a payoff and isn't rambling/small talk.
# Raises ValueError on fail so the retry wrapper re-extracts a different segment.
CONTENT_GATE_ENABLED = True
CONTENT_GATE_MODEL = "claude-haiku-4-5-20251001"

# Posted-history log: which episodes have already been uploaded (keyed by RSS
# GUID) so --auto never re-posts the same episode. Written ONLY after a
# successful YouTube upload. See modules/posted_history.py.
POSTED_HISTORY_PATH = os.path.join(TMP_DIR, "posted_history.json")

# Footage-history ledger: Pexels video ids used by PREVIOUS episodes, so a new
# episode never re-pulls the same stock clip. See modules/pexels_bg.py. Capped to
# the most-recent FOOTAGE_HISTORY_MAX ids (oldest evicted) so the file can't grow
# unbounded and eventually starve every query of fresh candidates.
FOOTAGE_HISTORY_PATH = os.path.join(TMP_DIR, "footage_history.json")
FOOTAGE_HISTORY_MAX = 300   # cap; evict oldest ids beyond this
FOOTAGE_HISTORY_TTL_DAYS = 30  # entries older than this re-enter the pool

# --- HTTP ---
# Browser-like headers so feeds/CDNs that block default clients still respond.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# --- Filename sanitization ---
FEED_NAME_MAX_LEN = 40
EPISODE_TITLE_MAX_LEN = 60

# --- Clip / highlight length limits (seconds) ---
CLIP_MIN_SECONDS = 15
CLIP_MAX_SECONDS = 90
MAX_CLIPS_PER_EPISODE = 5

# --- AI extraction (Claude) ---
# Requested claude-sonnet-4-20250514, but it 404s on this account (retired).
# Using its drop-in replacement, the current Sonnet.
EXTRACT_MODEL = "claude-sonnet-4-6"
EXTRACT_MAX_TOKENS = 2000
# Target clip window the model should aim for, in seconds.
# Capped at 58s so the finished Short stays UNDER 60s — YouTube blocks the
# Pixabay music bed on Shorts that are 60s or longer, so we keep a 2s safety
# margin under that threshold.
CLIP_WINDOW_MIN_SECONDS = 45
CLIP_WINDOW_MAX_SECONDS = 58
# Hard upper bound enforced by ai_extract._validate(). It must also sit at/below
# 58s (not just the soft target) — validation checks against THIS value, and
# sentence-boundary snapping can push the window a touch past the target, which
# the 2s margin absorbs.
CLIP_WINDOW_MAX_HARD_SECONDS = 58

# --- Slide dimensions (pixels) ---
# Vertical 9:16 for Reels/Shorts; switch to 1920x1080 for landscape YouTube.
SLIDE_WIDTH = 1080
SLIDE_HEIGHT = 1920
SLIDE_BG_COLOR = (17, 17, 17)       # near-black
SLIDE_TEXT_COLOR = (255, 255, 255)  # white
SLIDE_ACCENT_COLOR = (250, 204, 21)  # amber — eyebrow/labels
SLIDE_MARGIN = 96                   # safe-area padding
SLIDE_TITLE_FONT_SIZE = 84
SLIDE_BODY_FONT_SIZE = 66
SLIDE_EYEBROW_FONT_SIZE = 40
SLIDE_MIN_FONT_SIZE = 34            # floor when auto-shrinking long text

# --- Background music (single fixed track, mixed under the voice) ---
# Same track every video; skipped (voice-only) if the file is missing.
MUSIC_PATH = os.path.join(BASE_DIR, "assets", "music", "background.mp3")
MUSIC_GAIN_DB = -18        # music level relative to the voice (present bed under speech)
MUSIC_FADE_IN = 1.0        # seconds
MUSIC_FADE_OUT = 1.5       # seconds

# --- Video output ---
VIDEO_FPS = 30
VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
VIDEO_BG_COLOR = (10, 10, 15)        # #0A0A0F dark background

# Optional background image: first image found here is blurred + darkened.
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
BG_BLUR_RADIUS = 25
BG_DARKEN = 0.35                     # brightness multiplier (0=black, 1=original)

# --- Candidate shortlist (Stage 1 of the two-stage extraction) ---
CANDIDATE_COUNT = 5                  # max ranked clip candidates find_candidates() surfaces

# --- AI-generated themed backgrounds (OpenAI gpt-image-1 — primary background source) ---
OPENAI_IMAGE_MODEL = "gpt-image-1"
OPENAI_IMAGE_SIZE = "1024x1536"      # portrait, closest match to the 9:16 video frame
OPENAI_IMAGE_QUALITY = "medium"      # low/medium/high - medium is the cost/quality default
OPENAI_IMAGE_TIMEOUT = 90            # seconds per HTTP request
IMAGE_PROMPT_COUNT = 4               # number of background prompts ai_extract emits
SEARCH_QUERY_COUNT = 5               # one art-directed stock-photo query per carousel slide
VIDEO_QUERY_COUNT = 4                # stock-VIDEO background SLOTS to actually fill
VIDEO_QUERY_SPARE = 1                # extra backup query ai_extract emits as a fallback
# What ai_extract emits: the 4 primary beats PLUS spare backups (5 total). pexels_bg
# fills VIDEO_QUERY_COUNT slots and dips into the spare(s) when a query yields a
# duplicate/empty result, so all 4 slots still get DISTINCT footage.
VIDEO_QUERY_EXTRACT_COUNT = VIDEO_QUERY_COUNT + VIDEO_QUERY_SPARE
IMAGE_ASPECT_RATIO = "9:16"          # vertical, matches the video frame
BG_IMAGE_PREFIX = "bg_"             # tmp/bg_<n>.png  /  tmp/bg_<n>.mp4

# --- Pexels stock video/photo (video search now unused by background.py; photo
# search still feeds slide_gen.py's carousel slide backgrounds) ---
PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
# Photo search (still images for the carousel slide backgrounds).
PEXELS_PHOTO_SEARCH_URL = "https://api.pexels.com/v1/search"
PEXELS_PER_PAGE = 1
# Video search pulls several candidates per query so duplicate clips (the same
# Pexels video matching two different queries) can be skipped for the next one.
PEXELS_VIDEO_PER_PAGE = 12
# Orientations tried in order per query (portrait preferred for 9:16).
PEXELS_ORIENTATIONS = ["portrait", "square", "landscape"]
PEXELS_SIZE = "medium"
PEXELS_TIMEOUT = 60                  # seconds per HTTP request
PEXELS_BACKOFFS = [2, 4, 8, 16]      # waits between retries on 429 (free tier = 200 req/hr)

# --- Background-clip QUALITY GATE (modules/bg_quality.py) ---
# Post-fetch, pre-commit guardrails that inspect a candidate's Pexels poster
# frames (the `video_pictures` previews — NO full download) and reject footage
# the SYSTEM_PROMPT can ask against but can't actually see: too-dark/off-palette
# shots, close-up/identifiable faces & group settings, and legible on-screen
# text. `_find_video` skips a failing candidate and walks to the next of the
# PEXELS_VIDEO_PER_PAGE results; if EVERY candidate fails it keeps the best one
# as a last resort (a filled slot beats an empty one). Face/text checks need
# opencv (opencv-python-headless); without it the gate degrades to brightness
# only. Set BG_QUALITY_ENABLED=False to bypass entirely.
BG_QUALITY_ENABLED = True
BG_QUALITY_FRAMES = 4                # poster frames sampled per candidate (median/any-aggregated)
BG_BRIGHTNESS_MIN = 42              # reject if MEDIAN frame luma (0-255) below this (dark/dim/off-palette)
BG_FACE_AREA_MAX = 0.10            # reject if any one face bbox covers > this frac of the frame (close-up portrait)
BG_FACE_COUNT_MAX = 1              # reject if more than this many faces detected in a frame (group/crowd/classroom)
BG_TEXT_COVER_MAX = 0.045          # reject if text-like regions cover > this frac of the frame (signage/flipchart/book)

# Ken Burns + crossfade for the AI background montage.
BG_CROSSFADE = 1.0                   # seconds of crossfade overlap between images
BG_KENBURNS_ZOOM_FROM = 1.08         # start scale; margin (1080*.08/2=43px) > pan so edges never show
BG_KENBURNS_ZOOM_TO = 1.18           # end scale — slow zoom-in
BG_KENBURNS_PAN = 40                 # max pan drift in pixels per axis
BG_OVERLAY_OPACITY = 0.5             # 50% black overlay so captions stay readable
# Hard cap on how long any single background clip may hold on screen. If too few
# distinct clips arrive to tile the window under this cap, video_gen adds more
# (shorter) slots and cycles the available clips with Ken Burns variations rather
# than letting one shot stretch out and feel static.
MAX_BG_CLIP_DURATION = 18            # seconds, per-slot ceiling

# Burned word-level karaoke captions (lower band of the frame).
CAPTION_FONT_SIZE = 80
CAPTION_COLOR = (255, 255, 255)          # inactive words: white
CAPTION_HIGHLIGHT_COLOR = (255, 214, 10)  # active (spoken) word: yellow
CAPTION_STROKE_COLOR = (0, 0, 0)         # black outline
CAPTION_STROKE_WIDTH = 8
CAPTION_WORDS_PER_GROUP = 5              # max words shown at once
CAPTION_GROUP_GAP = 0.7                  # a pause longer than this starts a new group
CAPTION_CENTER_Y = 0.62                  # vertical center of caption block; kept above the Reels/Shorts bottom UI
CAPTION_LINE_SPACING = 1.2

# Hook text (top of frame, first few seconds only).
HOOK_FONT_SIZE = 58
HOOK_COLOR = "white"
HOOK_DURATION = 3.0                  # seconds
HOOK_TOP = 0.08                      # fraction of height

# Brand name — single source of truth for the video watermark AND the slide
# footer wordmark. Change it here; both renderers read from this constant.
BRAND_NAME = "Icarus Wings"

# Brand watermark (right side). Kept well inside the safe area — the
# bottom of the pill sits at WATERMARK_BASELINE_Y so there's clear padding below.
# Solid white text on a semi-transparent dark rounded pill so it reads clearly
# on any background while staying small and subtle.
WATERMARK_FONT_SIZE = 34
WATERMARK_COLOR = (255, 255, 255)        # solid white text
WATERMARK_BASELINE_Y = 1700              # px (of 1920): bottom edge of the watermark pill
WATERMARK_PILL_COLOR = (0, 0, 0)         # pill background
WATERMARK_PILL_OPACITY = 0.45            # 45% black behind the text
WATERMARK_PILL_PAD_X = 18                # horizontal padding text -> pill edge
WATERMARK_PILL_PAD_Y = 10                # vertical padding text -> pill edge
WATERMARK_PILL_RADIUS = 16               # rounded-corner radius (px)

# CTA overlay (appears centered in the last few seconds of every video).
CTA_TEXT = "Follow for more"
CTA_DURATION = 2.5          # seconds the CTA is visible at end
CTA_FADE_IN = 0.4           # fade-in duration
CTA_Y = 0.80                # vertical center (fraction of height); below caption band, above safe-area bottom
CTA_FONT_SIZE = 46
CTA_COLOR = (255, 255, 255)
CTA_PILL_COLOR = (0, 0, 0)
CTA_PILL_OPACITY = 0.60
CTA_PILL_PAD_X = 28
CTA_PILL_PAD_Y = 14
CTA_PILL_RADIUS = 22
