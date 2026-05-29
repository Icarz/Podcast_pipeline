"""Audio transcription via Groq Whisper."""

import logging
import os

from groq import Groq

logger = logging.getLogger(__name__)


def _client() -> Groq:
    return Groq(api_key=os.environ["GROQ_API_KEY"])


def transcribe(audio_path: str, model: str = "whisper-large-v3") -> dict:
    """Transcribe ``audio_path`` and return the transcript payload."""
    logger.info("Transcribing: %s", audio_path)
    client = _client()
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), f.read()),
            model=model,
            response_format="verbose_json",
        )
    # TODO: normalize segments/timestamps into the shape downstream modules expect.
    return {"text": result.text, "raw": result}
