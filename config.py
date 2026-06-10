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
# trailing comment. The first four are the weekly ROTATION (see below).
PODCAST_FEEDS = {
    # Modern Wisdom — Chris Williamson (Megaphone)   https://feeds.megaphone.fm/modernwisdom
    "modern_wisdom":  "https://feeds.megaphone.fm/modernwisdom",
    # Huberman Lab — Andrew Huberman (Megaphone)     https://feeds.megaphone.fm/hubermanlab
    "huberman_lab":   "https://feeds.megaphone.fm/hubermanlab",
    # The Daily Stoic — Ryan Holiday (Art19)         https://rss.art19.com/the-daily-stoic
    "daily_stoic":    "https://rss.art19.com/the-daily-stoic",
    # The Mindset Mentor — Rob Dial (Simplecast)     https://feeds.simplecast.com/rpKQEwel
    "mindset_mentor": "https://feeds.simplecast.com/rpKQEwel",
    # The Jordan B. Peterson Podcast (Megaphone)     https://feeds.megaphone.fm/BVDWV6444647327
    # NOTE: canonical PUBLIC feed (per Apple + jordanbpeterson.com + podnews).
    # Recent episodes are Daily Wire+ members-only; public RSS lags (newest
    # public ep was Nov 2025 as of June 2026). --auto dedup handles re-pulls.
    "jordan_peterson": "https://feeds.megaphone.fm/BVDWV6444647327",
    # Jocko Podcast — Jocko Willink (RedCircle)      https://feeds.redcircle.com/64a89f88-a245-4098-8d8d-496325ec4f74
    # Active, ~2x/week (main + Jocko Underground). Verified live June 2026.
    "jocko_podcast":  "https://feeds.redcircle.com/64a89f88-a245-4098-8d8d-496325ec4f74",
    # Not in the rotation; kept for manual runs.     https://feeds.megaphone.fm/ISML9402684841
    # SOLVED with Mark Manson (monthly, drops the 1st). "The Mark Manson Show"
    # is not a current show; SOLVED is Manson's active podcast. Verified June 2026.
    "mark_manson":    "https://feeds.megaphone.fm/ISML9402684841",
    # Not in the rotation; kept for manual runs.     https://feeds.simplecast.com/UCwaTX1J
    "mel_robbins":    "https://feeds.simplecast.com/UCwaTX1J",
}

# Default feed used when none is specified.
DEFAULT_FEED = "mindset_mentor"

# --- Weekly multi-podcast rotation ---
# main.py --auto picks today's feed from here by weekday. Python's
# date.weekday() is Mon=0, Tue=1, ..., Sun=6. Any weekday NOT listed is a
# non-posting day: --auto logs "no posting day today" and exits 0.
ROTATION = {
    0: "modern_wisdom",    # Monday
    1: "jordan_peterson",  # Tuesday
    2: "huberman_lab",     # Wednesday
    3: "jocko_podcast",    # Thursday
    4: "daily_stoic",      # Friday
    5: "mindset_mentor",   # Saturday
}

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
CLIP_WINDOW_MIN_SECONDS = 25
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
IMAGE_ASPECT_RATIO = "9:16"          # vertical, matches the video frame
BG_IMAGE_PREFIX = "bg_"             # tmp/bg_<n>.png  /  tmp/bg_<n>.mp4

# --- Pexels stock video (primary background source) ---
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
