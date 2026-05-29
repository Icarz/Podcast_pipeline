"""Podcast automation pipeline orchestrator.

Coordinates the end-to-end flow:
    RSS ingest -> transcribe -> AI extract -> slide/video gen
    -> storage -> publish (YouTube + Instagram).
"""

import logging
import os

from dotenv import load_dotenv

from modules import (
    ai_extract,
    instagram_publish,
    rss_ingest,
    slide_gen,
    storage,
    transcribe,
    video_gen,
    youtube_publish,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join("logs", "pipeline.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("pipeline")


def run(feed_url: str) -> None:
    """Run the full pipeline for the latest episode in ``feed_url``."""
    logger.info("Starting pipeline for feed: %s", feed_url)

    episode = rss_ingest.fetch_latest(feed_url)
    audio_path = rss_ingest.download_audio(episode)

    transcript = transcribe.transcribe(audio_path)
    highlights = ai_extract.extract_highlights(transcript)

    slides = slide_gen.build_slides(highlights)
    video_path = video_gen.build_video(audio_path, slides, highlights)

    asset_url = storage.upload(video_path)

    youtube_publish.publish(video_path, episode, highlights)
    instagram_publish.publish(asset_url, episode, highlights)

    logger.info("Pipeline complete for: %s", episode.get("title", "unknown"))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Podcast automation pipeline")
    parser.add_argument("feed_url", help="Podcast RSS feed URL")
    args = parser.parse_args()

    run(args.feed_url)
