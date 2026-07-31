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

# --- AI extraction (Claude) ---
# claude-sonnet-5 (swapped from claude-sonnet-4-6 on 2026-07-31): the current
# Sonnet — near-Opus quality on the judgment-heavy extraction/copywriting work,
# same $3/$15 price class ($2/$10 intro through 2026-08-31). Drop-in for this
# pipeline (no temperature/thinking/prefill used anywhere). NOTE: its tokenizer
# yields ~30% more tokens for the same text, hence the max-tokens bump below.
EXTRACT_MODEL = "claude-sonnet-5"
EXTRACT_MAX_TOKENS = 4000  # headroom for the 6 image_scenes objects under the sonnet-5 tokenizer
# Target spoken duration for modules/script_gen.py's scripts, in seconds.
# There is no transcript to snap to anymore -- the REAL, final duration is
# whatever the ElevenLabs voiceover comes out to; video_gen.build_video
# already clamps clip_end to the actual audio duration regardless of what's
# cached here (proven by the 2026-07-31 manual test: a 39.7s script rendered
# clean with no floor enforcement needed). Capped at 58s so the finished
# Short stays UNDER 60s -- YouTube blocks the music bed on Shorts >= 60s.
CLIP_WINDOW_MIN_SECONDS = 45
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

# --- AI-generated themed backgrounds (OpenAI gpt-image-2 — primary background source) ---
OPENAI_IMAGE_MODEL = "gpt-image-2"
OPENAI_IMAGE_SIZE = "1024x1536"      # portrait, closest match to the 9:16 video frame
OPENAI_IMAGE_QUALITY = "medium"      # low/medium/high - medium is the cost/quality default
OPENAI_IMAGE_TIMEOUT = 90            # seconds per HTTP request
# Number of illustrated background images per clip. ai_extract emits this many
# structured `image_scenes` objects (scene CONTENT only); image_gen composes the
# final prompts by wrapping each scene in the locked style template
# (image_gen.STYLE_BLOCK — edit the look THERE, not in the extraction prompt).
# 6 (raised from 4 on 2026-07-31): image every ~8-9s of a ~52s clip for faster
# visual pacing; ~$0.28/short at medium quality.
IMAGE_PROMPT_COUNT = 6
# Fixed story-arc beat per scene position: the 4 content quarters map to 6
# scenes (problem and payoff get two shots each). ai_extract coerces whatever
# the model emits to exactly this sequence; image_gen maps each beat to a mood
# line in the composed prompt.
IMAGE_SCENE_BEATS = ["problem", "problem", "stakes", "reframe", "payoff", "payoff"]

# --- Script topic clusters (modules/script_gen.py) ---
# Fixed taxonomy so modules/script_history.py's dedup ledger stays consistent
# across runs. The model still free-picks a SPECIFIC topic each run; this only
# tags which bucket it falls in. First 3 are the proven top performers (see
# CLAUDE.md's content-performance data) and should be favored by the prompt;
# the other 2 cover the rest of the content universe.
TOPIC_CLUSTERS = [
    "fear_anxiety_rumination",
    "individuality_vs_conformity",
    "money_as_freedom",
    "neurology_focus_motivation",
    "identity_resilience_meaning",
]

# Dedup ledger: every topic modules/script_gen.py has generated, so each new
# run can avoid repeating a recent topic_cluster or hook. See
# modules/script_history.py.
SCRIPT_HISTORY_PATH = os.path.join(TMP_DIR, "script_history.json")

IMAGE_ASPECT_RATIO = "9:16"          # vertical, matches the video frame
BG_IMAGE_PREFIX = "bg_"             # tmp/<basename>_bg_<n>.png

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
