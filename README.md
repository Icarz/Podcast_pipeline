# Podcast Automation Pipeline

Turns a podcast RSS feed into published video content. For the latest episode it:
ingests the feed → transcribes the audio → extracts highlights with AI →
generates slides and a video → uploads to storage → publishes to YouTube and Instagram.

## Pipeline flow

```
RSS ingest → transcribe → AI extract → slide gen → video gen → storage → publish (YouTube + Instagram)
```

## Project structure

```
podcast-pipeline/
├── main.py                    # Orchestrator — runs the full flow
├── requirements.txt
├── .env.example               # Copy to .env and fill in
├── modules/
│   ├── rss_ingest.py          # feedparser: fetch latest episode + download audio
│   ├── transcribe.py          # Groq Whisper transcription
│   ├── ai_extract.py          # Anthropic Claude: titles, summary, highlights
│   ├── slide_gen.py           # Pillow: render highlight slides
│   ├── video_gen.py           # MoviePy: compose slides + audio into video
│   ├── storage.py             # boto3 → Cloudflare R2 upload
│   ├── youtube_publish.py     # YouTube Data API v3 upload
│   └── instagram_publish.py   # Meta Graph API (Reels) publish
├── output/videos/             # Rendered videos
├── output/slides/             # Rendered slide images
├── tmp/                       # Downloaded audio / scratch
└── logs/                      # pipeline.log
```

## Setup

1. Create and activate a virtual environment:

   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Configure secrets — copy the example env file and fill in your keys:

   ```powershell
   Copy-Item .env.example .env
   ```

### Required environment variables

| Group          | Variables |
|----------------|-----------|
| AI providers   | `ANTHROPIC_API_KEY`, `GROQ_API_KEY` |
| Cloudflare R2  | `CLOUDFLARE_R2_ENDPOINT`, `CLOUDFLARE_R2_ACCESS_KEY_ID`, `CLOUDFLARE_R2_SECRET_ACCESS_KEY`, `CLOUDFLARE_R2_BUCKET`, `CLOUDFLARE_R2_PUBLIC_URL` |
| YouTube        | `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` |
| Meta/Instagram | `META_APP_ID`, `META_APP_SECRET`, `META_ACCESS_TOKEN`, `META_IG_USER_ID` |

## Usage

```powershell
.\venv\Scripts\python.exe main.py <RSS_FEED_URL>
```

## Requirements

- **Python 3.12**
- **FFmpeg** — required by MoviePy. The bundled `imageio-ffmpeg` package provides a
  binary automatically, so no separate install is needed for basic rendering.

## Status

The module functions are scaffolded and wired into `main.py`. Bodies marked with
`TODO` / `NotImplementedError` still need their real implementations
(audio download, slide drawing, video rendering, and the publish OAuth flows).
