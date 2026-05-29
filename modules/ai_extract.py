"""AI extraction: pull highlights, titles, and clip-worthy moments from a transcript."""

import logging
import os

from anthropic import Anthropic

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-8"


def _client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def extract_highlights(transcript: dict) -> dict:
    """Extract titles, summary, and highlight segments from ``transcript``."""
    logger.info("Extracting highlights from transcript")
    client = _client()

    prompt = (
        "Extract a YouTube title, description, and 3-5 short clip-worthy "
        "highlight segments (with start/end if available) from this podcast "
        f"transcript:\n\n{transcript.get('text', '')}"
    )

    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    # TODO: parse structured JSON output into highlights schema.
    return {"raw": message.content[0].text if message.content else ""}
