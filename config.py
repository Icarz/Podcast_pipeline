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

# --- Slide dimensions (pixels) ---
# Vertical 9:16 for Reels/Shorts; switch to 1920x1080 for landscape YouTube.
SLIDE_WIDTH = 1080
SLIDE_HEIGHT = 1920
SLIDE_BG_COLOR = (17, 17, 17)       # near-black
SLIDE_TEXT_COLOR = (255, 255, 255)  # white
SLIDE_MARGIN = 96                   # safe-area padding

# --- Video output ---
VIDEO_FPS = 30
VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
