"""YouTube publishing via the YouTube Data API v3.

Authenticates with a long-lived OAuth refresh token (no browser flow at
runtime) and uploads the rendered clip as a YouTube Short with a resumable
upload. Credentials come from the YOUTUBE_* env vars (see .env.example).
"""

import logging
import os

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

load_dotenv()

logger = logging.getLogger(__name__)

# Token endpoint Google exchanges the refresh token at.
_TOKEN_URI = "https://oauth2.googleapis.com/token"
# Upload-only scope: enough to insert videos, nothing more.
_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# People & Blogs — the standard bucket for talking-head / mindset content.
_CATEGORY_ID = "22"
# YouTube hard-caps the title at 100 chars and the description at 5000.
_TITLE_MAX = 100
_DESCRIPTION_MAX = 5000
# Tags always appended on top of the episode hashtags.
_DEFAULT_TAGS = ["shorts", "mindset", "selfimprovement"]
_PEXELS_ATTRIBUTION = "Background footage via Pexels"


def _credentials() -> Credentials:
    """Build OAuth credentials straight from the stored refresh token."""
    client_id = os.environ["YOUTUBE_CLIENT_ID"]
    client_secret = os.environ["YOUTUBE_CLIENT_SECRET"]
    refresh_token = os.environ["YOUTUBE_REFRESH_TOKEN"]

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=_SCOPES,
    )
    # Exchange the refresh token for a live access token up front so auth
    # failures surface here (clear message) rather than mid-upload.
    creds.refresh(Request())
    return creds


def _clean_tag(tag: str) -> str:
    """Strip a leading '#' and surrounding whitespace from a hashtag."""
    return tag.lstrip("#").strip()


def _build_title(highlights: dict) -> str:
    """Episode title + ' #Shorts', clamped to YouTube's 100-char limit."""
    base = (highlights.get("title") or "").strip()
    suffix = " #Shorts"
    if len(base) + len(suffix) > _TITLE_MAX:
        base = base[: _TITLE_MAX - len(suffix)].rstrip()
    return f"{base}{suffix}"


def _build_description(highlights: dict) -> str:
    """Hook + insight bullets + Pexels attribution + hashtags."""
    parts: list[str] = []

    hook = (highlights.get("hook") or "").strip()
    if hook:
        parts.append(hook)

    insights = [i.strip() for i in highlights.get("insights", []) if i and i.strip()]
    if insights:
        parts.append("\n".join(f"• {i}" for i in insights))

    parts.append(_PEXELS_ATTRIBUTION)

    hashtags = [
        f"#{_clean_tag(h)}" for h in highlights.get("hashtags", []) if _clean_tag(h)
    ]
    if hashtags:
        parts.append(" ".join(hashtags))

    return "\n\n".join(parts)[:_DESCRIPTION_MAX]


def _build_tags(highlights: dict) -> list[str]:
    """Episode hashtags (de-#'d) plus the default Shorts/mindset tags."""
    tags: list[str] = []
    seen: set[str] = set()
    for raw in list(highlights.get("hashtags", [])) + _DEFAULT_TAGS:
        tag = _clean_tag(raw)
        key = tag.lower()
        if tag and key not in seen:
            seen.add(key)
            tags.append(tag)
    return tags


def publish(
    video_path: str,
    episode: dict,
    highlights: dict,
    privacy_status: str = "private",
) -> str:
    """Upload ``video_path`` to YouTube as a Short and return its watch URL.

    ``privacy_status`` defaults to ``"private"`` for safe testing; pass
    ``"public"`` or ``"unlisted"`` to change visibility.
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"video file not found: {video_path}")

    title = _build_title(highlights)
    logger.info("Publishing to YouTube (%s): %s", privacy_status, title)

    body = {
        "snippet": {
            "title": title,
            "description": _build_description(highlights),
            "tags": _build_tags(highlights),
            "categoryId": _CATEGORY_ID,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        youtube = build("youtube", "v3", credentials=_credentials())
        media = MediaFileUpload(
            video_path, mimetype="video/mp4", chunksize=-1, resumable=True
        )
        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("Upload progress: %d%%", int(status.progress() * 100))

        video_id = response["id"]
        url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info("Uploaded to YouTube: %s (id=%s)", url, video_id)
        return url

    except HttpError as exc:
        logger.error("YouTube API error during upload: %s", exc)
        raise
    except KeyError as exc:
        logger.error("Missing YouTube credential env var: %s", exc)
        raise
    except Exception:
        logger.exception("Unexpected error uploading to YouTube")
        raise


if __name__ == "__main__":
    import glob

    import config

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    videos = sorted(
        glob.glob(os.path.join(config.VIDEO_DIR, "*.mp4")),
        key=os.path.getmtime,
        reverse=True,
    )
    if not videos:
        raise SystemExit(f"No videos found in {config.VIDEO_DIR}")

    video_path = videos[0]
    logger.info("Most recent video: %s", video_path)

    # Pull cached extraction highlights so the title/description/tags are real.
    # The video filename is slugified from the clip title, which won't match the
    # plan's episode-named cache file, so match on the highlights title instead;
    # fall back to the most recent plan, then to minimal metadata.
    import json
    import re

    def _slug(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

    basename = os.path.splitext(os.path.basename(video_path))[0]
    plans = sorted(
        glob.glob(os.path.join(config.TMP_DIR, "*.plan.json")),
        key=os.path.getmtime,
        reverse=True,
    )
    highlights: dict = {}
    for plan_path in plans:
        with open(plan_path, "r", encoding="utf-8") as f:
            cand = json.load(f).get("highlights", {})
        if _slug(cand.get("title")) == _slug(basename):
            highlights = cand
            logger.info("Matched plan by title: %s", plan_path)
            break
    if not highlights and plans:
        with open(plans[0], "r", encoding="utf-8") as f:
            highlights = json.load(f).get("highlights", {})
        logger.warning("No title match; using most recent plan: %s", plans[0])
    if not highlights:
        logger.warning("No plan cache found; using minimal metadata")
        highlights = {"title": basename.replace("_", " ")}

    url = publish(video_path, {"title": highlights.get("title")}, highlights,
                  privacy_status="private")
    print(f"\nUploaded (PRIVATE): {url}")
