"""Audio transcription via the Groq Whisper API.

Sends an audio file to Groq's ``whisper-large-v3`` model and returns the full
text plus segment-level timestamps.
"""

import logging
import os

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = "whisper-large-v3"


def _client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set (check your .env file)")
    return Groq(api_key=api_key)


def _seg_value(segment, key):
    """Read ``key`` from a segment that may be a dict or an SDK object."""
    if isinstance(segment, dict):
        return segment.get(key)
    return getattr(segment, key, None)


def transcribe(audio_path: str) -> dict:
    """Transcribe ``audio_path`` with Groq Whisper (segment + word timestamps).

    Returns a dict with:
        ``text``     -- the full transcript string
        ``segments`` -- list of {"start": float, "end": float, "text": str}
        ``words``    -- list of {"word": str, "start": float, "end": float}
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(audio_path)

    logger.info("Transcribing %s with %s", os.path.basename(audio_path), MODEL)
    client = _client()

    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), f.read()),
            model=MODEL,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
        )

    raw_segments = _seg_value(result, "segments") or []
    segments = [
        {
            "start": _seg_value(s, "start"),
            "end": _seg_value(s, "end"),
            "text": (_seg_value(s, "text") or "").strip(),
        }
        for s in raw_segments
    ]

    raw_words = _seg_value(result, "words") or []
    words = [
        {
            "word": (_seg_value(w, "word") or "").strip(),
            "start": _seg_value(w, "start"),
            "end": _seg_value(w, "end"),
        }
        for w in raw_words
        if (_seg_value(w, "word") or "").strip()
    ]

    text = (_seg_value(result, "text") or "").strip()
    logger.info("Transcribed %d segments, %d words, %d chars", len(segments), len(words), len(text))

    return {"text": text, "segments": segments, "words": words}


if __name__ == "__main__":
    import glob

    import config

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

    audio_path = mp3s[0]
    print(f"Transcribing: {os.path.basename(audio_path)}\n")

    result = transcribe(audio_path)

    print(f"Total segments: {len(result['segments'])}")
    print(f"Total chars   : {len(result['text'])}\n")

    print("=== First 5 segments ===")
    for seg in result["segments"][:5]:
        print(f"[{seg['start']:>7.2f} - {seg['end']:>7.2f}]  {seg['text']}")

    print("\n=== Transcript preview (first 400 chars) ===")
    print(result["text"][:400].encode("ascii", "replace").decode("ascii"))
