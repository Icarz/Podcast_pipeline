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

# --- Podcast RSS feeds (verified live; select by key) ---
PODCAST_FEEDS = {
    "mindset_mentor": "https://feeds.simplecast.com/rpKQEwel",
    "modern_wisdom":  "https://feeds.megaphone.fm/modernwisdom",
    "huberman_lab":   "https://feeds.megaphone.fm/hubermanlab",
    "daily_stoic":    "https://rss.art19.com/the-daily-stoic",
    "mel_robbins":    "https://feeds.simplecast.com/UCwaTX1J",
}

# Default feed used when none is specified.
DEFAULT_FEED = "mindset_mentor"

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
# Target clip window the model must pick, in seconds.
CLIP_WINDOW_MIN_SECONDS = 45
CLIP_WINDOW_MAX_SECONDS = 65

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

# --- Video output ---
VIDEO_FPS = 30
VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
VIDEO_BG_COLOR = (10, 10, 15)        # #0A0A0F dark background

# Optional background image: first image found here is blurred + darkened.
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
BG_BLUR_RADIUS = 25
BG_DARKEN = 0.35                     # brightness multiplier (0=black, 1=original)

# Burned word-level karaoke captions (lower band of the frame).
CAPTION_FONT_SIZE = 80
CAPTION_COLOR = (255, 255, 255)          # inactive words: white
CAPTION_HIGHLIGHT_COLOR = (255, 214, 10)  # active (spoken) word: yellow
CAPTION_STROKE_COLOR = (0, 0, 0)         # black outline
CAPTION_STROKE_WIDTH = 8
CAPTION_WORDS_PER_GROUP = 5              # max words shown at once
CAPTION_GROUP_GAP = 0.7                  # a pause longer than this starts a new group
CAPTION_CENTER_Y = 0.80                  # vertical center of the caption block (lower 35%)
CAPTION_LINE_SPACING = 1.2

# Hook text (top of frame, first few seconds only).
HOOK_FONT_SIZE = 58
HOOK_COLOR = "white"
HOOK_DURATION = 3.0                  # seconds
HOOK_TOP = 0.08                      # fraction of height

# Podcast-name watermark (bottom-right, muted).
WATERMARK_FONT_SIZE = 34
WATERMARK_COLOR = (160, 160, 160)
